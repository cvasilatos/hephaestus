from __future__ import annotations

import csv
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .types import GPUDevice, GPUProcess


class NvidiaError(RuntimeError):
    pass


class NvidiaClient:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def _run(self, arguments: Sequence[str]) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed executable and structured arguments.
                [str(self.executable), *arguments],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NvidiaError(f"nvidia-smi failed: {exc}") from exc
        return result.stdout

    def discover(self) -> list[GPUDevice]:
        output = self._run(
            [
                "--query-gpu=index,uuid,name,memory.total,display_active",
                "--format=csv,noheader,nounits",
            ]
        )
        devices = []
        for row in csv.reader(output.splitlines(), skipinitialspace=True):
            if not row:
                continue
            if len(row) != 5:
                raise NvidiaError(f"Unexpected GPU inventory row: {row!r}")
            index, uuid, name, memory, display_active = (item.strip() for item in row)
            devices.append(
                GPUDevice(
                    uuid=uuid,
                    index=int(index),
                    name=name,
                    memory_mb=int(memory),
                    device_path=f"/dev/nvidia{int(index)}",
                    display_active=display_active.lower() in {"enabled", "active", "yes"},
                )
            )
        return devices

    def processes(self, gpu_uuids: set[str] | None = None) -> list[GPUProcess]:
        output = self._run(
            [
                "--query-compute-apps=gpu_uuid,pid,process_name",
                "--format=csv,noheader,nounits",
            ]
        )
        processes = []
        for row in csv.reader(output.splitlines(), skipinitialspace=True):
            if not row:
                continue
            if len(row) != 3:
                raise NvidiaError(f"Unexpected process row: {row!r}")
            gpu_uuid, pid_text, name = (item.strip() for item in row)
            if gpu_uuids is not None and gpu_uuid not in gpu_uuids:
                continue
            try:
                pid = int(pid_text)
                uid = os.stat(f"/proc/{pid}").st_uid
            except (ValueError, FileNotFoundError, ProcessLookupError):
                continue
            processes.append(GPUProcess(gpu_uuid=gpu_uuid, pid=pid, name=name, uid=uid))
        return processes
