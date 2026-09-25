#!/bin/sh
set -eu

STATE_DIR=/var/lib/gpu-booking
APP_DIR=/opt/gpu-booking

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this uninstaller as root." >&2
    exit 1
fi

if [ -x /usr/local/sbin/gpu-booking-rollback ]; then
    /usr/local/sbin/gpu-booking-rollback
elif [ -x "$APP_DIR/deploy/rollback-gpu-state.sh" ]; then
    "$APP_DIR/deploy/rollback-gpu-state.sh"
elif [ -s "$STATE_DIR/device-baseline.acl" ]; then
    systemctl disable --now gpu-booking-agent 2>/dev/null || true
    setfacl --restore="$STATE_DIR/device-baseline.acl"
    rm -f /etc/udev/rules.d/70-gpu-booking.rules
else
    echo "No GPU baseline was found; refusing to claim that GPU state was restored." >&2
    exit 1
fi
systemctl disable --now gpu-booking-worker gpu-booking-web gpu-booking-authd 2>/dev/null || true

rm -f /etc/systemd/system/gpu-booking-agent.service
rm -f /etc/systemd/system/gpu-booking-worker.service
rm -f /etc/systemd/system/gpu-booking-web.service
rm -f /etc/systemd/system/gpu-booking-authd.service
rm -f /etc/pam.d/gpu-booking
rm -f /etc/profile.d/90-gpu-booking.sh
rm -f /usr/lib/tmpfiles.d/gpu-booking.conf
rm -f /usr/local/bin/gpu-run
rm -f /usr/local/bin/gpu-shell
rm -f /usr/local/sbin/gpu-accessd
rm -f /usr/local/sbin/gpu-booking-rollback
rm -f /run/gpu-booking/auth.sock
rm -f /run/gpu-booking/web.sock
rm -f /etc/nginx/sites-enabled/gpu-booking.conf
rm -f /etc/nginx/sites-available/gpu-booking.conf

systemctl daemon-reload
udevadm control --reload-rules
nginx -t && systemctl reload nginx

if [ -f "$STATE_DIR/device-baseline.acl" ]; then
    snapshot_dir=$STATE_DIR/rollback-history/$(date -u +%Y%m%dT%H%M%SZ)-$$
    install -d -o root -g root -m 0700 "$STATE_DIR/rollback-history" "$snapshot_dir"
    for state_file in \
        device-baseline.acl \
        device-baseline.paths \
        device-baseline.ready \
        device-baseline.sha256 \
        70-gpu-booking.rules.before-install \
        udev-rule-was-absent \
        gpu-state-restored; do
        if [ -e "$STATE_DIR/$state_file" ]; then
            mv "$STATE_DIR/$state_file" "$snapshot_dir/$state_file"
        fi
    done
fi

echo "Host integration removed and captured GPU ACLs restored."
echo "Database, checkout, service user, configuration, audit data, and rollback history were preserved."
