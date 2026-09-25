from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from .nvidia import NvidiaClient
from .types import GPUDevice, GPUProcess

logger = logging.getLogger(__name__)


class AccessError(RuntimeError):
    pass


class ProtectedProcessError(AccessError):
    pass


class GPUController:
    def __init__(
        self,
        nvidia: NvidiaClient,
        *,
        setfacl: Path,
        allowed_root_pids: frozenset[int] = frozenset(),
        dry_run: bool = False,
    ) -> None:
        self.nvidia = nvidia
        self.setfacl = setfacl
        self.allowed_root_pids = allowed_root_pids
        self.dry_run = dry_run

    def _command(self, arguments: Sequence[str]) -> None:
        if self.dry_run:
            logger.info("dry-run command: %s", " ".join(arguments))
            return
        try:
            subprocess.run(  # noqa: S603 - absolute executable and validated arguments.
                list(arguments),
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AccessError(f"Command failed: {arguments[0]}: {exc}") from exc

    def grant(self, device: GPUDevice, uid: int) -> None:
        self._validate_device(device)
        self._command([str(self.setfacl), "-m", f"u:{uid}:rw", device.device_path])

    def revoke(self, device: GPUDevice, uid: int) -> None:
        self._validate_device(device)
        try:
            self._command([str(self.setfacl), "-x", f"u:{uid}", device.device_path])
        except AccessError:
            logger.debug("UID %s had no removable ACL on %s", uid, device.uuid)
        # setfacl leaves an extended ACL mask behind after removing the named user. Resetting the
        # mask returns the effective group-class bits to the locked baseline.
        self._command([str(self.setfacl), "-m", "m::---", device.device_path])

    def lock_device(self, device: GPUDevice, managed_uids: Iterable[int]) -> None:
        for uid in sorted(set(managed_uids)):
            try:
                self.revoke(device, uid)
            except AccessError:
                logger.debug("No ACL to remove for UID %s on %s", uid, device.uuid)

    def _validate_device(self, device: GPUDevice) -> None:
        path = Path(device.device_path)
        if (
            not str(path).startswith("/dev/nvidia")
            or not path.name.removeprefix("nvidia").isdigit()
        ):
            raise AccessError(f"Refusing unexpected device path: {path}")
        if not self.dry_run and not path.exists():
            raise AccessError(f"Device does not exist: {path}")

    def prepare_for_owner(self, devices: Iterable[GPUDevice], uid: int) -> None:
        uuids = {device.uuid for device in devices}
        processes = self.nvidia.processes(uuids)
        protected = [
            process
            for process in processes
            if process.uid == 0 or process.pid in self.allowed_root_pids
        ]
        if protected:
            details = ", ".join(f"PID {item.pid} ({item.name})" for item in protected)
            raise ProtectedProcessError(f"Protected process is using the GPU: {details}")
        stale = [process for process in processes if process.uid != uid]
        if stale:
            self.signal_processes(stale, signal.SIGTERM)
            if not self.dry_run:
                time.sleep(2)
            remaining_pids = {item.pid for item in self.nvidia.processes(uuids)}
            self.signal_processes(
                [item for item in stale if item.pid in remaining_pids],
                signal.SIGKILL,
            )

    def owner_processes(self, devices: Iterable[GPUDevice], uid: int) -> list[GPUProcess]:
        uuids = {device.uuid for device in devices}
        return [process for process in self.nvidia.processes(uuids) if process.uid == uid]

    def signal_processes(self, processes: Iterable[GPUProcess], action: signal.Signals) -> None:
        for process in {item.pid: item for item in processes}.values():
            try:
                current_uid = os.stat(f"/proc/{process.pid}").st_uid
            except FileNotFoundError:
                continue
            if current_uid != process.uid:
                logger.warning("PID %s changed owner before signal; skipping", process.pid)
                continue
            if current_uid == 0 or process.pid in self.allowed_root_pids:
                raise ProtectedProcessError(f"Refusing to signal protected PID {process.pid}")
            logger.info("Sending %s to PID %s (%s)", action.name, process.pid, process.name)
            if self.dry_run:
                continue
            try:
                os.kill(process.pid, action)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                raise AccessError(f"Cannot signal PID {process.pid}") from exc
