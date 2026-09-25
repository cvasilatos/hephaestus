#!/bin/sh
set -eu

APP_DIR=/opt/gpu-booking
WEB_ENV=/etc/gpu-booking/web.env
AGENT_ENV=/etc/gpu-booking/agent.env
STATE_DIR=/var/lib/gpu-booking
BASELINE_ACL=$STATE_DIR/device-baseline.acl
BASELINE_PATHS=$STATE_DIR/device-baseline.paths
BASELINE_READY=$STATE_DIR/device-baseline.ready
BASELINE_CHECKSUMS=$STATE_DIR/device-baseline.sha256
UDEV_RULE=/etc/udev/rules.d/70-gpu-booking.rules
UDEV_BACKUP=$STATE_DIR/70-gpu-booking.rules.before-install
UDEV_ABSENT=$STATE_DIR/udev-rule-was-absent

write_baseline_checksums() {
    checksums_tmp=$(mktemp "$STATE_DIR/device-baseline.sha256.XXXXXX")
    if [ -f "$UDEV_BACKUP" ]; then
        (cd "$STATE_DIR" && sha256sum \
            device-baseline.acl \
            device-baseline.paths \
            70-gpu-booking.rules.before-install) > "$checksums_tmp"
    else
        (cd "$STATE_DIR" && sha256sum \
            device-baseline.acl \
            device-baseline.paths \
            udev-rule-was-absent) > "$checksums_tmp"
    fi
    chmod 0600 "$checksums_tmp"
    chown root:root "$checksums_tmp"
    mv "$checksums_tmp" "$BASELINE_CHECKSUMS"
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi
if [ ! -f "$APP_DIR/pyproject.toml" ]; then
    echo "Expected a root-owned checkout at $APP_DIR" >&2
    exit 1
fi
if [ "$(stat -c %u "$APP_DIR/pyproject.toml")" -ne 0 ]; then
    echo "$APP_DIR must be owned by root before privileged services execute its code." >&2
    exit 1
fi
if [ ! -f "$WEB_ENV" ] || [ ! -f "$AGENT_ENV" ]; then
    echo "Create $WEB_ENV and $AGENT_ENV before installing." >&2
    exit 1
fi
: "${GPU_BOOKING_SERVER_NAME:?Set GPU_BOOKING_SERVER_NAME}"
: "${GPU_BOOKING_TLS_CERT:?Set GPU_BOOKING_TLS_CERT}"
: "${GPU_BOOKING_TLS_KEY:?Set GPU_BOOKING_TLS_KEY}"

command -v uv >/dev/null
command -v getfacl >/dev/null
command -v setfacl >/dev/null
command -v nvidia-smi >/dev/null
command -v sha256sum >/dev/null
if ! find /dev -maxdepth 1 -type c -name 'nvidia[0-9]*' -print -quit | grep -q .; then
    echo "No numeric NVIDIA device nodes were found." >&2
    exit 1
fi

getent group gpu-booking-admins >/dev/null || groupadd --system gpu-booking-admins
getent group gpu-booking >/dev/null || groupadd --system gpu-booking
getent passwd gpu-booking >/dev/null || useradd --system --gid gpu-booking --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin gpu-booking
install -d -o root -g root -m 0755 /etc/gpu-booking "$STATE_DIR"
chmod 0600 "$WEB_ENV" "$AGENT_ENV"
chown root:root "$WEB_ENV" "$AGENT_ENV"

if [ ! -f "$BASELINE_ACL" ]; then
    rm -f \
        "$BASELINE_PATHS" \
        "$BASELINE_READY" \
        "$BASELINE_CHECKSUMS" \
        "$UDEV_BACKUP" \
        "$UDEV_ABSENT"
    paths_tmp=$(mktemp "$STATE_DIR/device-baseline.paths.XXXXXX")
    acl_tmp=$(mktemp "$STATE_DIR/device-baseline.acl.XXXXXX")
    find /dev -maxdepth 2 -type c -path '/dev/nvidia*' -print | sort > "$paths_tmp"
    if [ ! -s "$paths_tmp" ]; then
        rm -f "$paths_tmp" "$acl_tmp"
        echo "No NVIDIA device nodes were found while capturing the rollback baseline." >&2
        exit 1
    fi
    : > "$acl_tmp"
    while IFS= read -r device_path; do
        if ! getfacl -p "$device_path" >> "$acl_tmp"; then
            rm -f "$paths_tmp" "$acl_tmp"
            echo "Could not capture the ACL for $device_path" >&2
            exit 1
        fi
    done < "$paths_tmp"
    chmod 0600 "$paths_tmp" "$acl_tmp"
    chown root:root "$paths_tmp" "$acl_tmp"
    mv "$paths_tmp" "$BASELINE_PATHS"
    mv "$acl_tmp" "$BASELINE_ACL"

    if [ -L "$UDEV_RULE" ]; then
        echo "Refusing to replace a symbolic-link udev rule: $UDEV_RULE" >&2
        exit 1
    elif [ -e "$UDEV_RULE" ]; then
        if [ ! -f "$UDEV_RULE" ]; then
            echo "Refusing to replace a non-regular udev rule: $UDEV_RULE" >&2
            exit 1
        fi
        cp -a "$UDEV_RULE" "$UDEV_BACKUP"
        rm -f "$UDEV_ABSENT"
    else
        : > "$UDEV_ABSENT"
        chmod 0600 "$UDEV_ABSENT"
        chown root:root "$UDEV_ABSENT"
        rm -f "$UDEV_BACKUP"
    fi
    write_baseline_checksums
    : > "$BASELINE_READY"
    chmod 0600 "$BASELINE_READY"
    chown root:root "$BASELINE_READY"
elif [ ! -f "$BASELINE_READY" ]; then
    if [ ! -s "$BASELINE_PATHS" ]; then
        # Upgrade a baseline captured by an older installer. The former uninstaller
        # always removed this application's udev rule, so preserve that behavior.
        sed -n 's/^# file: //p' "$BASELINE_ACL" > "$BASELINE_PATHS"
        chmod 0600 "$BASELINE_PATHS"
        chown root:root "$BASELINE_PATHS"
        : > "$UDEV_ABSENT"
        chmod 0600 "$UDEV_ABSENT"
        chown root:root "$UDEV_ABSENT"
    elif [ ! -f "$UDEV_BACKUP" ] && [ ! -f "$UDEV_ABSENT" ]; then
        # Finish a baseline capture interrupted before its udev state was recorded.
        if [ -L "$UDEV_RULE" ]; then
            echo "Refusing to back up a symbolic-link udev rule: $UDEV_RULE" >&2
            exit 1
        elif [ -e "$UDEV_RULE" ]; then
            if [ ! -f "$UDEV_RULE" ]; then
                echo "Refusing to back up a non-regular udev rule: $UDEV_RULE" >&2
                exit 1
            fi
            cp -a "$UDEV_RULE" "$UDEV_BACKUP"
        else
            : > "$UDEV_ABSENT"
            chmod 0600 "$UDEV_ABSENT"
            chown root:root "$UDEV_ABSENT"
        fi
    fi
    write_baseline_checksums
    : > "$BASELINE_READY"
    chmod 0600 "$BASELINE_READY"
    chown root:root "$BASELINE_READY"
fi
if [ ! -f "$BASELINE_CHECKSUMS" ]; then
    write_baseline_checksums
fi

install -o root -g root -m 0755 \
    deploy/rollback-gpu-state.sh /usr/local/sbin/gpu-booking-rollback

rollback_armed=true
rollback_on_exit() {
    status=$?
    trap - 0 HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$rollback_armed" = true ]; then
        echo "Installation failed; restoring the pre-install GPU state." >&2
        if ! /usr/local/sbin/gpu-booking-rollback; then
            echo "Automatic GPU rollback was incomplete; run gpu-booking-rollback manually." >&2
        fi
    fi
    exit "$status"
}
trap rollback_on_exit 0
trap 'exit 1' HUP INT TERM

systemctl stop gpu-booking-agent 2>/dev/null || true
if systemctl is-active --quiet gpu-booking-agent 2>/dev/null; then
    echo "Could not stop gpu-booking-agent before installation." >&2
    exit 1
fi
systemctl disable gpu-booking-agent 2>/dev/null || true

cd "$APP_DIR"
uv sync --frozen --no-dev
set -a
. "$WEB_ENV"
set +a
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy --fail-level WARNING

install -o root -g root -m 0644 deploy/pam/gpu-booking /etc/pam.d/gpu-booking
install -o root -g root -m 0644 deploy/udev/70-gpu-booking.rules "$UDEV_RULE"
install -o root -g root -m 0644 deploy/profile/90-gpu-booking.sh /etc/profile.d/90-gpu-booking.sh
install -o root -g root -m 0644 deploy/tmpfiles/gpu-booking.conf /usr/lib/tmpfiles.d/gpu-booking.conf
install -o root -g root -m 0755 bin/gpu-run /usr/local/bin/gpu-run
install -o root -g root -m 0755 bin/gpu-shell /usr/local/bin/gpu-shell
install -o root -g root -m 0755 bin/gpu-accessd /usr/local/sbin/gpu-accessd
install -o root -g root -m 0644 deploy/systemd/gpu-booking-web.service /etc/systemd/system/gpu-booking-web.service
install -o root -g root -m 0644 deploy/systemd/gpu-booking-worker.service /etc/systemd/system/gpu-booking-worker.service
install -o root -g root -m 0644 deploy/systemd/gpu-booking-agent.service /etc/systemd/system/gpu-booking-agent.service
install -o root -g root -m 0644 deploy/systemd/gpu-booking-authd.service /etc/systemd/system/gpu-booking-authd.service

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

server_name=$(escape_sed_replacement "$GPU_BOOKING_SERVER_NAME")
tls_cert=$(escape_sed_replacement "$GPU_BOOKING_TLS_CERT")
tls_key=$(escape_sed_replacement "$GPU_BOOKING_TLS_KEY")
sed -e "s|__SERVER_NAME__|$server_name|g" \
    -e "s|__TLS_CERT__|$tls_cert|g" \
    -e "s|__TLS_KEY__|$tls_key|g" \
    deploy/nginx/gpu-booking.conf > /etc/nginx/sites-available/gpu-booking.conf
ln -sf /etc/nginx/sites-available/gpu-booking.conf /etc/nginx/sites-enabled/gpu-booking.conf

udevadm control --reload-rules
find /dev -maxdepth 1 -type c -name 'nvidia[0-9]*' -exec chmod 0600 {} +
systemd-tmpfiles --create /usr/lib/tmpfiles.d/gpu-booking.conf
nginx -t
systemctl daemon-reload
systemctl enable gpu-booking-authd gpu-booking-web gpu-booking-worker
systemctl restart gpu-booking-authd gpu-booking-web gpu-booking-worker
systemctl restart nginx

rollback_armed=false
trap - 0 HUP INT TERM
echo "Web services installed. Apply database agent grants, then run discover and doctor."
echo "The privileged agent has not been enabled."
