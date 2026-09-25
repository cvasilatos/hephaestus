#!/bin/sh
set -eu

APP_DIR=/opt/gpu-booking
CONFIG_DIR=/etc/gpu-booking
SECRETS_FILE=$CONFIG_DIR/bootstrap-secrets.env
WEB_ENV=$CONFIG_DIR/web.env
AGENT_ENV=$CONFIG_DIR/agent.env
PATH=/usr/local/bin:$PATH
export PATH

fail() {
    echo "error: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

write_env() {
    name=$1
    value=$2
    escaped=$(printf '%s' "$value" | sed "s/'/'\"'\"'/g")
    printf "%s='%s'\n" "$name" "$escaped"
}

read_config() {
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        key=${line%%=*}
        [ "$key" != "$line" ] || fail "Invalid configuration line: $line"
        value=${line#*=}
        case "$key" in
            SERVER_NAME) SERVER_NAME=$value ;;
            TLS_CERT) TLS_CERT=$value ;;
            TLS_KEY) TLS_KEY=$value ;;
            ADMIN_EMAILS) ADMIN_EMAILS=$value ;;
            ADMIN_USER) ADMIN_USER=$value ;;
            SITE_TIMEZONE) SITE_TIMEZONE=$value ;;
            SMTP_HOST) SMTP_HOST=$value ;;
            SMTP_PORT) SMTP_PORT=$value ;;
            SMTP_USER) SMTP_USER=$value ;;
            SMTP_PASSWORD) SMTP_PASSWORD=$value ;;
            SMTP_USE_TLS) SMTP_USE_TLS=$value ;;
            DEFAULT_FROM_EMAIL) DEFAULT_FROM_EMAIL=$value ;;
            INSTALL_SYSTEM_PACKAGES) INSTALL_SYSTEM_PACKAGES=$value ;;
            *) fail "Unknown configuration key: $key" ;;
        esac
    done < "$1"
}

if [ "$(id -u)" -ne 0 ]; then
    fail "Run this bootstrap as root."
fi
if [ "$#" -ne 1 ]; then
    fail "Usage: sudo ./deploy/bootstrap.sh /path/to/gpu-booking.conf"
fi

CONFIG_FILE=$1
[ -f "$CONFIG_FILE" ] || fail "Configuration file not found: $CONFIG_FILE"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
[ -f "$SOURCE_DIR/pyproject.toml" ] || fail "Could not find the application source tree."

SERVER_NAME=
TLS_CERT=
TLS_KEY=
ADMIN_EMAILS=
ADMIN_USER=
SITE_TIMEZONE=UTC
SMTP_HOST=localhost
SMTP_PORT=25
SMTP_USER=
SMTP_PASSWORD=
SMTP_USE_TLS=false
DEFAULT_FROM_EMAIL=
INSTALL_SYSTEM_PACKAGES=true
read_config "$CONFIG_FILE"

[ -n "$SERVER_NAME" ] || fail "SERVER_NAME is required."
case "$SERVER_NAME" in
    *[!A-Za-z0-9.-]*) fail "SERVER_NAME must be a DNS hostname." ;;
esac
[ -n "$TLS_CERT" ] || fail "TLS_CERT is required."
[ -n "$TLS_KEY" ] || fail "TLS_KEY is required."
[ -r "$TLS_CERT" ] || fail "TLS certificate is not readable: $TLS_CERT"
[ -r "$TLS_KEY" ] || fail "TLS key is not readable: $TLS_KEY"
[ -n "$ADMIN_EMAILS" ] || fail "ADMIN_EMAILS is required."
if [ -n "$ADMIN_USER" ]; then
    id "$ADMIN_USER" >/dev/null 2>&1 || fail "ADMIN_USER does not exist: $ADMIN_USER"
fi
case "$SMTP_PORT" in
    ''|*[!0-9]*) fail "SMTP_PORT must be a positive integer." ;;
esac
[ "$SMTP_PORT" -gt 0 ] || fail "SMTP_PORT must be a positive integer."
case "$SMTP_USE_TLS" in
    true|false) ;;
    *) fail "SMTP_USE_TLS must be true or false." ;;
esac
case "$INSTALL_SYSTEM_PACKAGES" in
    true|false) ;;
    *) fail "INSTALL_SYSTEM_PACKAGES must be true or false." ;;
esac
if [ -z "$DEFAULT_FROM_EMAIL" ]; then
    DEFAULT_FROM_EMAIL="Hephaestus <$ADMIN_EMAILS>"
fi

bootstrap_state_exists=false
if [ -f "$SECRETS_FILE" ]; then
    bootstrap_state_exists=true
fi
if [ "$SOURCE_DIR" != "$APP_DIR" ] \
    && [ -e "$APP_DIR/pyproject.toml" ] \
    && [ "$bootstrap_state_exists" = false ]; then
    fail "$APP_DIR already exists and was not created by this bootstrap."
fi

if [ "$INSTALL_SYSTEM_PACKAGES" = true ]; then
    require_command apt-get
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y \
        acl \
        ca-certificates \
        libpam0g \
        libpam-modules \
        libpam-runtime \
        nginx \
        openssl \
        pipx \
        postgresql \
        postgresql-client \
        python3 \
        python3-venv \
        rsync
fi

for command in getfacl nginx nvidia-smi openssl psql rsync runuser setfacl systemctl; do
    require_command "$command"
done

if ! command -v uv >/dev/null 2>&1; then
    require_command pipx
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install uv
fi
require_command uv

openssl x509 -in "$TLS_CERT" -noout >/dev/null 2>&1 || fail "TLS_CERT is not a valid certificate."
openssl pkey -in "$TLS_KEY" -noout >/dev/null 2>&1 || fail "TLS_KEY is not a valid private key."
systemctl enable --now postgresql nginx

if [ "$bootstrap_state_exists" = false ]; then
    database_probe="SELECT rolname FROM pg_roles
WHERE rolname IN ('gpu_booking', 'gpu_booking_agent')
UNION ALL
SELECT datname FROM pg_database WHERE datname = 'gpu_booking'"
    existing_database_objects=$(runuser -u postgres -- psql \
        --dbname=postgres \
        --tuples-only \
        --no-align \
        --command="$database_probe")
    if [ -n "$existing_database_objects" ]; then
        fail "PostgreSQL already contains Hephaestus roles or a database; use the manual installer."
    fi
fi

install -d -o root -g root -m 0755 "$CONFIG_DIR"
if [ ! -f "$SECRETS_FILE" ]; then
    umask 077
    {
        write_env WEB_DATABASE_PASSWORD "$(openssl rand -hex 32)"
        write_env AGENT_DATABASE_PASSWORD "$(openssl rand -hex 32)"
        write_env DJANGO_SECRET_KEY "$(openssl rand -hex 32)"
    } > "$SECRETS_FILE"
fi
chmod 0600 "$SECRETS_FILE"
chown root:root "$SECRETS_FILE"
# This file is generated by this script, owned by root, and not user-editable.
# shellcheck disable=SC1090
. "$SECRETS_FILE"
case "$WEB_DATABASE_PASSWORD:$AGENT_DATABASE_PASSWORD:$DJANGO_SECRET_KEY" in
    *[!0-9a-f:]*) fail "$SECRETS_FILE contains an invalid generated secret." ;;
esac
[ "${#WEB_DATABASE_PASSWORD}" -eq 64 ] \
    && [ "${#AGENT_DATABASE_PASSWORD}" -eq 64 ] \
    && [ "${#DJANGO_SECRET_KEY}" -eq 64 ] \
    || fail "$SECRETS_FILE contains an invalid generated secret."

{
    printf '\\set web_password %s\n' "$WEB_DATABASE_PASSWORD"
    printf '\\set agent_password %s\n' "$AGENT_DATABASE_PASSWORD"
    cat <<'SQL'
SELECT 'CREATE ROLE gpu_booking LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gpu_booking') \gexec
ALTER ROLE gpu_booking LOGIN PASSWORD :'web_password';

SELECT 'CREATE ROLE gpu_booking_agent LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gpu_booking_agent') \gexec
ALTER ROLE gpu_booking_agent LOGIN PASSWORD :'agent_password';

SELECT 'CREATE DATABASE gpu_booking OWNER gpu_booking'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'gpu_booking') \gexec
SQL
} | runuser -u postgres -- psql --dbname=postgres --set=ON_ERROR_STOP=1

umask 077
{
    write_env DJANGO_SECRET_KEY "$DJANGO_SECRET_KEY"
    write_env DJANGO_DEBUG false
    write_env DJANGO_ALLOWED_HOSTS "$SERVER_NAME"
    write_env DATABASE_URL "postgresql://gpu_booking:$WEB_DATABASE_PASSWORD@127.0.0.1/gpu_booking"
    write_env SITE_URL "https://$SERVER_NAME"
    write_env SITE_TIMEZONE "$SITE_TIMEZONE"
    write_env PAM_SERVICE gpu-booking
    write_env GPU_AUTH_SOCKET /run/gpu-booking/auth.sock
    write_env ADMIN_GROUP gpu-booking-admins
    write_env MIN_INTERACTIVE_UID 1000
    write_env SMTP_HOST "$SMTP_HOST"
    write_env SMTP_PORT "$SMTP_PORT"
    write_env SMTP_USER "$SMTP_USER"
    write_env SMTP_PASSWORD "$SMTP_PASSWORD"
    write_env SMTP_USE_TLS "$SMTP_USE_TLS"
    write_env DEFAULT_FROM_EMAIL "$DEFAULT_FROM_EMAIL"
    write_env GPU_AGENT_POLL_SECONDS 2
    write_env GPU_RUNTIME_DIR /run/gpu-booking
    write_env GPU_AGENT_CACHE /var/lib/gpu-booking/agent-state.json
    write_env GPU_ALLOWED_ROOT_PIDS ''
    write_env GPU_ADMIN_EMAILS "$ADMIN_EMAILS"
} > "$WEB_ENV"
{
    write_env DATABASE_URL "postgresql://gpu_booking_agent:$AGENT_DATABASE_PASSWORD@127.0.0.1/gpu_booking"
    write_env GPU_AGENT_POLL_SECONDS 2
    write_env GPU_RUNTIME_DIR /run/gpu-booking
    write_env GPU_AGENT_CACHE /var/lib/gpu-booking/agent-state.json
    write_env GPU_ALLOWED_ROOT_PIDS ''
    write_env GPU_ADMIN_EMAILS "$ADMIN_EMAILS"
    write_env NVIDIA_SMI "$(command -v nvidia-smi)"
    write_env SETFACL "$(command -v setfacl)"
    write_env GETFACL "$(command -v getfacl)"
} > "$AGENT_ENV"
chmod 0600 "$WEB_ENV" "$AGENT_ENV"
chown root:root "$WEB_ENV" "$AGENT_ENV"

if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
    install -d -o root -g root -m 0755 "$APP_DIR"
    rsync -a --delete \
        --exclude='.git/' \
        --exclude='.mypy_cache/' \
        --exclude='.pytest_cache/' \
        --exclude='.ruff_cache/' \
        --exclude='.uv-cache/' \
        --exclude='.venv/' \
        --exclude='development.sqlite3' \
        --exclude='deploy/install.conf' \
        --exclude='staticfiles/' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        "$SOURCE_DIR/" "$APP_DIR/"
    chown -R root:root "$APP_DIR"
fi

GPU_BOOKING_SERVER_NAME=$SERVER_NAME \
GPU_BOOKING_TLS_CERT=$TLS_CERT \
GPU_BOOKING_TLS_KEY=$TLS_KEY \
    "$APP_DIR/deploy/install.sh"

rollback_armed=true
rollback_on_exit() {
    status=$?
    trap - 0 HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$rollback_armed" = true ]; then
        echo "Bootstrap failed; restoring the pre-install GPU state." >&2
        if ! /usr/local/sbin/gpu-booking-rollback; then
            echo "Automatic GPU rollback was incomplete; run gpu-booking-rollback manually." >&2
        fi
    fi
    exit "$status"
}
trap rollback_on_exit 0
trap 'exit 1' HUP INT TERM

runuser -u postgres -- psql \
    --dbname=gpu_booking \
    --set=ON_ERROR_STOP=1 \
    --file="$APP_DIR/deploy/post_migrate_grants.sql"

if [ -n "$ADMIN_USER" ]; then
    usermod -a -G gpu-booking-admins "$ADMIN_USER"
fi

set -a
# This file is generated by this script, owned by root, and not user-editable.
# shellcheck disable=SC1090
. "$AGENT_ENV"
set +a
/usr/local/sbin/gpu-accessd discover
if ! /usr/local/sbin/gpu-accessd doctor; then
    echo "Installation completed, but GPU enforcement remains disabled because doctor failed." >&2
    exit 1
fi
systemctl enable gpu-booking-agent
systemctl restart gpu-booking-agent

rollback_armed=false
trap - 0 HUP INT TERM
echo
echo "Hephaestus is installed at https://$SERVER_NAME"
echo "Generated secrets are stored in $CONFIG_DIR with mode 0600."
if [ -n "$ADMIN_USER" ]; then
    echo "$ADMIN_USER must start a new login session before its administrator group applies."
fi
