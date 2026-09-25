from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    database_url: str
    poll_seconds: float
    runtime_dir: Path
    cache_path: Path
    nvidia_smi: Path
    setfacl: Path
    getfacl: Path
    allowed_root_pids: frozenset[int]
    admin_emails: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> AgentConfig:
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            raise ValueError("DATABASE_URL is required")
        root_pids = frozenset(
            int(value)
            for value in os.getenv("GPU_ALLOWED_ROOT_PIDS", "").split(",")
            if value.strip().isdigit()
        )
        return cls(
            database_url=database_url,
            poll_seconds=float(os.getenv("GPU_AGENT_POLL_SECONDS", "2")),
            runtime_dir=Path(os.getenv("GPU_RUNTIME_DIR", "/run/gpu-booking")),
            cache_path=Path(os.getenv("GPU_AGENT_CACHE", "/var/lib/gpu-booking/agent-state.json")),
            nvidia_smi=Path(os.getenv("NVIDIA_SMI", "/usr/bin/nvidia-smi")),
            setfacl=Path(os.getenv("SETFACL", "/usr/bin/setfacl")),
            getfacl=Path(os.getenv("GETFACL", "/usr/bin/getfacl")),
            allowed_root_pids=root_pids,
            admin_emails=tuple(
                address.strip()
                for address in os.getenv("GPU_ADMIN_EMAILS", "").split(",")
                if address.strip()
            ),
        )
