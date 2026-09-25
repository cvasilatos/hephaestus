from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gpu_access.nvidia import NvidiaClient


def test_inventory_parses_stable_uuid_and_display_state() -> None:
    output = "0, GPU-abc, NVIDIA A100, 81920, Disabled\n"
    with patch("subprocess.run", return_value=SimpleNamespace(stdout=output)):
        devices = NvidiaClient(Path("/usr/bin/nvidia-smi")).discover()
    assert devices[0].uuid == "GPU-abc"
    assert devices[0].device_path == "/dev/nvidia0"
    assert not devices[0].display_active


def test_process_query_filters_gpu_and_reads_proc_owner() -> None:
    output = "GPU-abc, 42, python\nGPU-other, 51, worker\n"
    with (
        patch("subprocess.run", return_value=SimpleNamespace(stdout=output)),
        patch("os.stat", return_value=SimpleNamespace(st_uid=21001)),
    ):
        processes = NvidiaClient(Path("/usr/bin/nvidia-smi")).processes({"GPU-abc"})
    assert [(item.pid, item.uid) for item in processes] == [(42, 21001)]
