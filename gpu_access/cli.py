from __future__ import annotations

import argparse
import logging
import os
import signal
import stat
import sys
import time
from pathlib import Path

from .config import AgentConfig
from .controller import GPUController
from .nvidia import NvidiaClient, NvidiaError
from .reconciler import Reconciler
from .repository import PostgresRepository, RepositoryError

logger = logging.getLogger(__name__)


def build_runtime(
    config: AgentConfig, dry_run: bool = False
) -> tuple[PostgresRepository, GPUController, Reconciler]:
    repository = PostgresRepository(config.database_url, config.admin_emails)
    nvidia = NvidiaClient(config.nvidia_smi)
    controller = GPUController(
        nvidia,
        setfacl=config.setfacl,
        allowed_root_pids=config.allowed_root_pids,
        dry_run=dry_run,
    )
    reconciler = Reconciler(
        repository,
        controller,
        runtime_dir=config.runtime_dir,
        cache_path=config.cache_path,
        dry_run=dry_run,
    )
    return repository, controller, reconciler


def discover(config: AgentConfig) -> int:
    repository, controller, _ = build_runtime(config)
    devices = controller.nvidia.discover()
    display_devices = [device for device in devices if device.display_active]
    if display_devices:
        for device in display_devices:
            print(f"Refusing display-active GPU: {device.uuid}", file=sys.stderr)
        return 1
    repository.sync_devices(devices)
    for device in devices:
        print(f"GPU {device.index}: {device.name} ({device.uuid})")
    return 0


def doctor(config: AgentConfig) -> int:
    problems = []
    for path in (config.nvidia_smi, config.setfacl, config.getfacl):
        if not path.is_file() or not os.access(path, os.X_OK):
            problems.append(f"Missing executable: {path}")
    try:
        devices = NvidiaClient(config.nvidia_smi).discover()
        for device in devices:
            path = Path(device.device_path)
            if device.display_active:
                problems.append(f"GPU {device.uuid} drives a display")
            if not path.exists():
                problems.append(f"Missing device: {path}")
            elif stat.S_IMODE(path.stat().st_mode) & 0o077:
                problems.append(f"Insecure baseline permissions on {path}; expected 0600")
    except NvidiaError as exc:
        problems.append(str(exc))
    try:
        PostgresRepository(config.database_url).policy()
    except RepositoryError as exc:
        problems.append(f"Database unavailable: {exc}")
    if os.geteuid() != 0:
        problems.append("gpu-accessd must run as root for real enforcement")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("All enforcement prerequisites passed.")
    return 0


def restore_acls(config: AgentConfig, dry_run: bool) -> int:
    repository, controller, _ = build_runtime(config, dry_run=dry_run)
    devices = controller.nvidia.discover()
    uids = repository.managed_uids()
    for device in devices:
        controller.lock_device(device, uids)
    print(f"Restored locked ACL baseline on {len(devices)} GPUs.")
    return 0


def run_service(config: AgentConfig) -> int:
    if os.geteuid() != 0:
        print("gpu-accessd run must execute as root", file=sys.stderr)
        return 1
    repository, controller, reconciler = build_runtime(config)
    devices = controller.nvidia.discover()
    if any(device.display_active for device in devices):
        print("Refusing to manage a display-active GPU", file=sys.stderr)
        return 1
    for device in devices:
        controller.lock_device(device, repository.managed_uids())
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            reconciler.tick()
        except Exception:
            logger.exception("Unexpected reconciliation error")
        time.sleep(config.poll_seconds)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="GPU booking enforcement agent")
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Run the continuous reconciliation service")
    subparsers.add_parser("discover", help="Discover and update PostgreSQL GPU inventory")
    subparsers.add_parser("doctor", help="Validate host enforcement prerequisites")
    reconcile = subparsers.add_parser("reconcile", help="Run one reconciliation cycle")
    reconcile.add_argument("--dry-run", action="store_true")
    restore = subparsers.add_parser("restore-acls", help="Remove managed user ACLs from all GPUs")
    restore.add_argument("--dry-run", action="store_true")
    return result


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    arguments = parser().parse_args()
    try:
        config = AgentConfig.from_environment()
        if arguments.command == "run":
            code = run_service(config)
        elif arguments.command == "discover":
            code = discover(config)
        elif arguments.command == "doctor":
            code = doctor(config)
        elif arguments.command == "restore-acls":
            code = restore_acls(config, arguments.dry_run)
        else:
            _, _, reconciler = build_runtime(config, dry_run=arguments.dry_run)
            reconciler.tick()
            code = 0
    except (ValueError, RepositoryError, NvidiaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
