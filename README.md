# Hephaestus

Hephaestus is a private, single-server reservation system for whole NVIDIA GPUs. Existing
Linux users authenticate through PAM, reserve one or more cards in a web calendar, and use
their allocation from SSH through `gpu-shell` or `gpu-run`. The web service is unprivileged;
the separate `gpu-accessd` service owns all device ACL and process-lifecycle operations.

## Safety boundary

This is lab-safe scheduling for trusted Linux users. Root, passwordless sudo, unrestricted
Docker, or direct device-administration privileges can bypass it. The installer intentionally
refuses to manage display GPUs, MIG configurations, or a host that does not pass `doctor`.

## Development

```console
uv sync --dev
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver
```

Development defaults to SQLite and a deliberately unsafe development secret. Production
requires PostgreSQL, HTTPS, PAM, SMTP, and the host integration described in
[`deploy/README.md`](deploy/README.md).

On a new Ubuntu/Debian GPU server, the recommended production path is one configuration file and
one root command:

```console
sudo cp deploy/install.conf.example /root/gpu-booking.conf
sudoedit /root/gpu-booking.conf
sudo ./deploy/bootstrap.sh /root/gpu-booking.conf
```

The bootstrap generates application and database secrets, provisions PostgreSQL, and enables GPU
enforcement only after the host passes the safety checks. See the deployment guide for required
DNS, TLS, SMTP, and NVIDIA-driver preparation. It snapshots the original GPU device and udev state
before making changes, automatically restores that state on failure, and installs
`gpu-booking-rollback` for explicit recovery after an interruption or power loss.

The demo command creates two simulated GPUs and prints the development users it creates.
It is disabled unless `DJANGO_DEBUG=true`.

## Administrator policy

Members of the configured administrator group can open **Control room → Policy settings** to
change booking policy without restarting the web or enforcement services. The page covers:

- slot, minimum/default/maximum duration, advance notice, and booking horizon;
- per-reservation GPU and per-user open-reservation limits;
- verified-email, edit, cancellation, early-release, and cancellation-cutoff rules;
- warning and process-termination timing; and
- shared-schedule range and username privacy.

Zero means “unlimited”, “no cutoff”, or “disabled” only where the field help text says so.
Infrastructure and secrets—including PAM, SMTP, database, host paths, and the privileged agent
poll interval—remain environment-managed by design.

## Research groups and Hydra allocation

The application models the Hydra hybrid allocation policy directly:

- administrators assign every user to a research group;
- GPUs can be marked as shared-pool, group-fixed, or unassigned;
- administrators choose how many detected GPUs are available by enabling or disabling each card,
  and may assign any number of fixed GPUs to each group;
- each group has an editable monthly shared GPU-hour allowance (730 by default);
- fixed GPUs can be borrowed only through an administrator-recorded group permission; and
- the dashboard and control room report monthly reservation counts and shared, fixed, and total
  GPU-hours for each user and group.

The initial policy migration creates the eBRAIN, Haven, DFX, and MoMA groups, classifies GPUs
0–3 as shared, and assigns fixed GPUs 4–7 to those groups. Administrators can change all of
these assignments, quotas, enforcement switches, connection details, operational guidance, and
checklist text from the control room. Existing users must be assigned to a group before booking.
The inventory remains hardware-backed: `gpu-accessd discover` registers the GPUs actually present
on each server, while the control room determines which of those cards are bookable and how they
are allocated. Newly discovered GPUs start unassigned so an administrator must classify them
before users can book them.

## Quality checks

```console
uv run ruff check .
uv run ruff format --check .
uv run mypy booking gpu_access gpu_booking
uv run pytest
uv run python manage.py check
```

PostgreSQL concurrency and real NVIDIA enforcement checks are documented separately because
they require a Linux staging host with the production services installed.
