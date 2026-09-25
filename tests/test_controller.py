from __future__ import annotations

import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gpu_access.controller import GPUController, ProtectedProcessError
from gpu_access.types import GPUProcess


def controller() -> GPUController:
    return GPUController(Mock(), setfacl=Path("/usr/bin/setfacl"))


def test_signal_skips_pid_that_changed_owner() -> None:
    process = GPUProcess(gpu_uuid="GPU-one", pid=42, name="python", uid=21001)
    with (
        patch("os.stat", return_value=SimpleNamespace(st_uid=21002)),
        patch("os.kill") as kill,
    ):
        controller().signal_processes([process], signal.SIGTERM)
    kill.assert_not_called()


def test_signal_refuses_root_process() -> None:
    process = GPUProcess(gpu_uuid="GPU-one", pid=42, name="service", uid=0)
    with (
        patch("os.stat", return_value=SimpleNamespace(st_uid=0)),
        pytest.raises(ProtectedProcessError),
    ):
        controller().signal_processes([process], signal.SIGTERM)
