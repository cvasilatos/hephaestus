from __future__ import annotations

import os
import sys
from pathlib import Path


def _visible_devices() -> str:
    runtime = Path(os.getenv("GPU_RUNTIME_DIR", "/run/gpu-booking")) / "users" / str(os.getuid())
    try:
        return runtime.read_text().strip()
    except FileNotFoundError:
        return ""


def run_main() -> None:
    if len(sys.argv) < 2:
        print("usage: gpu-run COMMAND [ARG ...]", file=sys.stderr)
        raise SystemExit(2)
    devices = _visible_devices()
    if not devices:
        print("No active GPU reservation for this user.", file=sys.stderr)
        raise SystemExit(1)
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = devices
    os.execvpe(sys.argv[1], sys.argv[1:], environment)


def shell_main() -> None:
    devices = _visible_devices()
    if not devices:
        print("No active GPU reservation for this user.", file=sys.stderr)
        raise SystemExit(1)
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = devices
    shell = environment.get("SHELL", "/bin/bash")
    os.execvpe(shell, [shell, "-l"], environment)
