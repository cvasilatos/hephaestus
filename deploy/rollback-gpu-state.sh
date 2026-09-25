#!/bin/sh
set -eu

STATE_DIR=${GPU_BOOKING_STATE_DIR:-/var/lib/gpu-booking}
UDEV_RULE=${GPU_BOOKING_UDEV_RULE:-/etc/udev/rules.d/70-gpu-booking.rules}
BASELINE_ACL=$STATE_DIR/device-baseline.acl
BASELINE_PATHS=$STATE_DIR/device-baseline.paths
BASELINE_CHECKSUMS=$STATE_DIR/device-baseline.sha256
UDEV_BACKUP=$STATE_DIR/70-gpu-booking.rules.before-install
UDEV_ABSENT=$STATE_DIR/udev-rule-was-absent
RESTORED_MARKER=$STATE_DIR/gpu-state-restored
DEVICE_PREFIX=${GPU_BOOKING_DEVICE_PREFIX:-/dev/nvidia}

fail() {
    echo "error: $*" >&2
    exit 1
}

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--dry-run" ]; }; then
    fail "Usage: gpu-booking-rollback [--dry-run]"
fi
dry_run=false
if [ "$#" -eq 1 ]; then
    dry_run=true
fi

[ -s "$BASELINE_ACL" ] || fail "GPU baseline not found: $BASELINE_ACL"
if [ -f "$BASELINE_CHECKSUMS" ]; then
    command -v sha256sum >/dev/null 2>&1 || fail "Required command not found: sha256sum"
    if ! (cd "$STATE_DIR" && sha256sum --check "$(basename "$BASELINE_CHECKSUMS")" >/dev/null); then
        fail "GPU baseline checksum verification failed; refusing to restore corrupted state."
    fi
fi

temporary_paths=
current_acl=
expected_paths=
observed_paths=
cleanup() {
    [ -z "$temporary_paths" ] || rm -f "$temporary_paths"
    [ -z "$current_acl" ] || rm -f "$current_acl"
    [ -z "$expected_paths" ] || rm -f "$expected_paths"
    [ -z "$observed_paths" ] || rm -f "$observed_paths"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

if [ -s "$BASELINE_PATHS" ]; then
    paths_file=$BASELINE_PATHS
else
    temporary_paths=$(mktemp "$STATE_DIR/device-baseline.paths.XXXXXX")
    sed -n 's/^# file: //p' "$BASELINE_ACL" > "$temporary_paths"
    paths_file=$temporary_paths
fi
[ -s "$paths_file" ] || fail "GPU baseline does not contain any device paths."

while IFS= read -r device_path; do
    case "$device_path" in
        "$DEVICE_PREFIX"*) ;;
        *) fail "Invalid path in GPU baseline: $device_path" ;;
    esac
done < "$paths_file"

if [ "$dry_run" = true ]; then
    echo "Would stop and disable gpu-booking-agent."
    if [ -f "$UDEV_BACKUP" ]; then
        echo "Would restore the pre-install udev rule from $UDEV_BACKUP."
    else
        echo "Would remove the Hephaestus udev rule at $UDEV_RULE."
    fi
    echo "Would restore and verify ACLs, ownership, and modes for:"
    sed 's/^/  /' "$paths_file"
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    fail "Run this rollback as root."
fi
for command in cmp find getfacl setfacl sort systemctl udevadm; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

systemctl stop gpu-booking-agent 2>/dev/null || true
if systemctl is-active --quiet gpu-booking-agent 2>/dev/null; then
    fail "gpu-booking-agent is still active; refusing to race it while restoring ACLs."
fi
rollback_failed=false
if systemctl is-enabled --quiet gpu-booking-agent 2>/dev/null; then
    if ! systemctl disable gpu-booking-agent; then
        echo "error: could not disable gpu-booking-agent" >&2
        rollback_failed=true
    fi
fi

if [ -f "$UDEV_BACKUP" ]; then
    [ -f "$UDEV_BACKUP" ] && [ ! -L "$UDEV_BACKUP" ] \
        || fail "Saved udev rule is not a regular file."
    rm -f "$UDEV_RULE"
    cp -a "$UDEV_BACKUP" "$UDEV_RULE"
    if ! cmp -s "$UDEV_BACKUP" "$UDEV_RULE"; then
        echo "error: restored udev rule does not match its backup" >&2
        rollback_failed=true
    fi
elif [ -f "$UDEV_ABSENT" ]; then
    rm -f "$UDEV_RULE"
    if [ -e "$UDEV_RULE" ]; then
    echo "error: could not remove the Hephaestus udev rule" >&2
        rollback_failed=true
    fi
else
    # Compatibility with installations created before udev state was recorded.
    rm -f "$UDEV_RULE"
fi
if ! udevadm control --reload-rules; then
    echo "error: could not reload udev rules" >&2
    rollback_failed=true
fi

if ! setfacl --restore="$BASELINE_ACL"; then
    echo "error: setfacl could not completely restore the GPU baseline" >&2
    rollback_failed=true
fi

current_acl=$(mktemp "$STATE_DIR/device-current.acl.XXXXXX")
capture_failed=false
: > "$current_acl"
while IFS= read -r device_path; do
    case "$device_path" in
        "$DEVICE_PREFIX"*) ;;
        *)
            echo "error: invalid path in GPU baseline: $device_path" >&2
            capture_failed=true
            continue
            ;;
    esac
    if [ ! -c "$device_path" ]; then
        echo "error: GPU device is missing during rollback verification: $device_path" >&2
        capture_failed=true
        continue
    fi
    if ! getfacl -p "$device_path" >> "$current_acl"; then
        capture_failed=true
    fi
done < "$paths_file"

if [ "$capture_failed" = true ] || ! cmp -s "$BASELINE_ACL" "$current_acl"; then
    echo "error: restored GPU device state does not match the pre-install baseline" >&2
    rollback_failed=true
fi

expected_paths=$(mktemp "$STATE_DIR/device-expected.paths.XXXXXX")
observed_paths=$(mktemp "$STATE_DIR/device-observed.paths.XXXXXX")
sort -u "$paths_file" > "$expected_paths"
find /dev -maxdepth 2 -type c -path "$DEVICE_PREFIX*" -print | sort -u > "$observed_paths"
if ! cmp -s "$expected_paths" "$observed_paths"; then
    echo "error: the current NVIDIA device set differs from the pre-install baseline" >&2
    rollback_failed=true
fi

if [ "$rollback_failed" = true ]; then
    fail "GPU rollback was incomplete; keep enforcement disabled and inspect the errors above."
fi

umask 077
date -u +%Y-%m-%dT%H:%M:%SZ > "$RESTORED_MARKER"
echo "GPU device ACLs, ownership, modes, and the previous udev rule were restored and verified."
