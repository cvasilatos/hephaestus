from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GPUDevice:
    uuid: str
    index: int
    name: str
    memory_mb: int
    device_path: str
    display_active: bool = False


@dataclass(frozen=True)
class GPUProcess:
    gpu_uuid: str
    pid: int
    name: str
    uid: int


@dataclass(frozen=True)
class Policy:
    warning_minutes: int = 15
    grace_minutes: int = 5


@dataclass(frozen=True)
class ReservationState:
    id: int
    username: str | None
    uid: int | None
    kind: str
    status: str
    starts_at: datetime
    ends_at: datetime
    access_granted_at: datetime | None
    access_released_at: datetime | None
    term_sent_at: datetime | None
    devices: tuple[GPUDevice, ...] = field(default_factory=tuple)
