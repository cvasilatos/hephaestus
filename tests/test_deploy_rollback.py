from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_gpu_rollback_restores_and_verifies_saved_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    state_dir.mkdir()
    fake_bin.mkdir()

    baseline = """# file: /dev/null
# owner: root
# group: root
user::rw-
group::rw-
other::rw-

"""
    baseline_file = state_dir / "device-baseline.acl"
    baseline_file.write_text(baseline, encoding="utf-8")
    (state_dir / "device-baseline.paths").write_text("/dev/null\n", encoding="utf-8")
    (state_dir / "udev-rule-was-absent").touch()

    fake_acl = tmp_path / "current.acl"
    fake_acl.write_text(baseline, encoding="utf-8")
    _write_command(fake_bin, "id", "echo 0")
    _write_command(
        fake_bin,
        "systemctl",
        'case "$1" in is-active|is-enabled) exit 1 ;; *) exit 0 ;; esac',
    )
    _write_command(fake_bin, "udevadm", "exit 0")
    _write_command(fake_bin, "setfacl", "exit 0")
    _write_command(fake_bin, "getfacl", 'cat "$FAKE_ACL"')
    _write_command(fake_bin, "find", "echo /dev/null")

    udev_rule = tmp_path / "70-gpu-booking.rules"
    udev_rule.write_text("installed rule\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "deploy" / "rollback-gpu-state.sh"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_ACL": str(fake_acl),
        "GPU_BOOKING_STATE_DIR": str(state_dir),
        "GPU_BOOKING_UDEV_RULE": str(udev_rule),
        "GPU_BOOKING_DEVICE_PREFIX": "/dev/",
    }

    result = subprocess.run(  # noqa: S603
        [str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert not udev_rule.exists()
    assert (state_dir / "gpu-state-restored").is_file()
    assert "restored and verified" in result.stdout

    (state_dir / "gpu-state-restored").unlink()
    udev_rule.write_text("installed rule\n", encoding="utf-8")
    fake_acl.write_text(baseline.replace("other::rw-", "other::---"), encoding="utf-8")
    mismatch = subprocess.run(  # noqa: S603
        [str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert mismatch.returncode != 0
    assert not (state_dir / "gpu-state-restored").exists()
    assert "does not match" in mismatch.stderr
