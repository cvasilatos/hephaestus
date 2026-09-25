# Ubuntu/Debian deployment

Production deployment is host-native: PAM and `/dev/nvidia*` access do not cross a container
boundary. Test this on a non-production GPU host before enabling the device lock.

## Quick installation

The bootstrap reduces a new installation to one configuration file and one command. It installs
the operating-system packages, copies a clean root-owned application tree to `/opt/gpu-booking`,
installs `uv` with `pipx`, creates random application and database secrets, provisions PostgreSQL,
runs migrations and grants, and configures PAM, Nginx, udev, and systemd. GPU enforcement is
enabled only when discovery and the safety doctor both pass.

The host must already have a working NVIDIA driver, private DNS, an existing TLS certificate and
key, and access to an SMTP relay. From a downloaded or cloned source tree:

```console
sudo cp deploy/install.conf.example /root/gpu-booking.conf
sudoedit /root/gpu-booking.conf
sudo ./deploy/bootstrap.sh /root/gpu-booking.conf
```

The configuration format is literal `KEY=value` text, not shell syntax; do not add quotes. At a
minimum, set `SERVER_NAME`, `TLS_CERT`, `TLS_KEY`, and `ADMIN_EMAILS`, and replace the example SMTP
settings with the server's relay details. Set `ADMIN_USER` to add an existing Linux account to the
administrator group. Database passwords and the Django secret are generated automatically in
`/etc/gpu-booking/bootstrap-secrets.env` with mode `0600`.

The bootstrap currently supports apt-based Ubuntu and Debian hosts and is intended for a new
installation; it refuses to take over pre-existing database roles or a database. It is safe to
rerun after a partial bootstrap failure: generated secrets are reused, database roles are updated
idempotently, and the application release is refreshed from the source tree. It never enables the
privileged agent after a failed `doctor` check.

### GPU rollback

Before changing device permissions, the installer records the ACLs, owner, and mode of every
`/dev/nvidia*` character device. It also saves an existing Hephaestus udev-rule file, or records
that the file did not exist. These root-only files remain under `/var/lib/gpu-booking`.

Any installer or bootstrap failure after that checkpoint automatically stops the privileged agent,
restores the previous udev rule and device state, and verifies the result against the saved ACL
snapshot. A power loss cannot run the automatic handler, so the same standalone recovery command
is installed for operators:

```console
sudo gpu-booking-rollback --dry-run
sudo gpu-booking-rollback
```

Rollback leaves the web application and database in place, but disables GPU enforcement. It exits
with an error instead of claiming success if the agent cannot be stopped, a saved device is
missing, `setfacl` fails, or the post-restore ACL snapshot differs from the baseline. After fixing
the underlying problem, rerun the bootstrap to validate the host and re-enable enforcement. This
restores only state that Hephaestus changes: device-node access metadata and its udev rule. It
cannot resurrect workloads that were terminated while enforcement was previously running.

## Manual installation

Use the steps below when packages, PostgreSQL roles, secrets, or application files are managed by
another provisioning system.

### 1. Prerequisites

- Ubuntu 24.04+ or current Debian with a working NVIDIA driver
- PostgreSQL 15+, Nginx, `acl`, PAM development/runtime packages, and `uv`
- Private DNS plus a TLS certificate/key readable by Nginx
- A working SMTP relay
- A root-owned checkout at `/opt/gpu-booking`

Create the database and roles with values appropriate for your password policy:

```sql
CREATE ROLE gpu_booking LOGIN PASSWORD 'WEB_PASSWORD';
CREATE DATABASE gpu_booking OWNER gpu_booking;
CREATE ROLE gpu_booking_agent LOGIN PASSWORD 'AGENT_PASSWORD';
```

Copy `.env.example` to `/etc/gpu-booking/web.env`, set the web-role `DATABASE_URL`, and create
`/etc/gpu-booking/agent.env` from `deploy/agent.env.example`, using the agent-role URL.
Do not make either file group- or world-readable. Run migrations with the web role, then apply
`deploy/post_migrate_grants.sql` as the database owner to restrict the agent role.

### 2. Install and verify

Set these installer variables before running the script:

```console
export GPU_BOOKING_SERVER_NAME=gpu-booking.example.edu
export GPU_BOOKING_TLS_CERT=/etc/ssl/certs/gpu-booking.pem
export GPU_BOOKING_TLS_KEY=/etc/ssl/private/gpu-booking.key
sudo --preserve-env=GPU_BOOKING_SERVER_NAME,GPU_BOOKING_TLS_CERT,GPU_BOOKING_TLS_KEY \
  /opt/gpu-booking/deploy/install.sh
```

The installer captures the existing NVIDIA device ACLs before changing them, installs systemd,
PAM, udev, profile, and Nginx integration, and starts only the web and notification services.
It intentionally does **not** start enforcement until these checks pass:

```console
sudo --preserve-env=DATABASE_URL /opt/gpu-booking/bin/gpu-accessd discover
sudo --preserve-env=DATABASE_URL /opt/gpu-booking/bin/gpu-accessd doctor
sudo systemctl enable --now gpu-booking-agent
```

The root-owned PAM broker lets the unprivileged web process authenticate local accounts without
granting it access to `/etc/shadow`. Its Unix socket accepts requests only from root or the
`gpu-booking` service UID. `doctor` refuses display-active GPUs, missing device nodes, permissive
numeric device nodes, a missing database, or a non-root invocation. Validate access with two
disposable Linux users before onboarding real users.

### 3. Operational commands

```console
sudo systemctl status gpu-booking-authd gpu-booking-web gpu-booking-worker gpu-booking-agent
sudo journalctl -u gpu-booking-agent
sudo /opt/gpu-booking/bin/gpu-accessd reconcile --dry-run
sudo /opt/gpu-booking/bin/gpu-accessd restore-acls --dry-run
sudo gpu-booking-rollback --dry-run
```

Users should start workloads with `gpu-shell` or `gpu-run COMMAND`. A profile hook also sets
`CUDA_VISIBLE_DEVICES` at SSH login, but an already-open shell must use one of the helpers after
the reservation begins.

### 4. Uninstall

`sudo /opt/gpu-booking/deploy/uninstall.sh` stops and disables the services, runs the verified GPU
rollback, reloads udev/Nginx/systemd, and removes only the installed integration files. It preserves
the database, application checkout, service account, logs, and audit history. The completed
baseline is moved under `/var/lib/gpu-booking/rollback-history/`, allowing a later reinstall to
capture the then-current GPU state instead of reusing a stale baseline.
