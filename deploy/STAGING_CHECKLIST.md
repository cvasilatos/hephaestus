# NVIDIA staging acceptance checklist

Complete this checklist on a disposable, headless Ubuntu/Debian GPU host before enabling the
service for real users. Record command output in your change ticket without copying passwords or
environment files.

## Baseline

- Confirm every managed GPU reports `Display Active: Disabled`.
- Confirm no user has root, passwordless sudo, or unrestricted Docker unless explicitly trusted.
- Back up the database and confirm `device-baseline.acl` was captured before applying udev rules.
- Run `gpu-booking-rollback --dry-run` and confirm it lists every expected NVIDIA device.
- Run `gpu-accessd discover`, `gpu-accessd doctor`, and `gpu-accessd reconcile --dry-run`.

## Two-user handover

1. Create two disposable interactive Linux users with verified app email addresses.
2. Book GPU 0 for user A and a back-to-back slot for user B.
3. During A's slot, verify A can run `gpu-run nvidia-smi` and B cannot open `/dev/nvidia0`.
4. Override `CUDA_VISIBLE_DEVICES` as B and confirm the device ACL still denies access.
5. Start a long CUDA process as A. Confirm the configured ending-soon notification and `SIGTERM`
   grace period, ACL removal and `SIGKILL` at T, then successful activation for B.
6. Reboot during a third active booking. Confirm udev starts locked and the agent restores only the
   active owner's ACL and runtime UUID file.
7. Reload the NVIDIA driver during a maintenance window and repeat the restoration check.

## Failure recovery

- Stop PostgreSQL during an active booking. Confirm no new booking activates and the cached end
  deadline still revokes access.
- Place a root-owned compute process on a GPU. Confirm activation fails without killing it and an
  administrator alert/audit event is produced.
- Force a partial multi-GPU ACL failure. Confirm already-granted ACLs are rolled back.
- Interrupt a staging installation after device locking. Confirm the automatic rollback reports a
  verified restore, or run `gpu-booking-rollback` after simulating a power-loss interruption.
- Run `deploy/uninstall.sh` and confirm its verified rollback matches the captured baseline.
