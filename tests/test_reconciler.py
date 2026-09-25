from __future__ import annotations

import signal
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from gpu_access.controller import AccessError
from gpu_access.reconciler import Reconciler
from gpu_access.types import GPUDevice, GPUProcess, Policy, ReservationState


class FakeRepository:
    def __init__(self, reservations: list[ReservationState]) -> None:
        self.items = reservations
        self.transitions: list[tuple[int, str, dict[str, datetime]]] = []
        self.events: list[str] = []
        self.alerts: list[str] = []

    def policy(self) -> Policy:
        return Policy(grace_minutes=5)

    def reservations(self) -> list[ReservationState]:
        return self.items

    def transition(
        self,
        reservation_id: int,
        status: str,
        *,
        expected_statuses: tuple[str, ...],
        **timestamps: datetime,
    ) -> bool:
        self.transitions.append((reservation_id, status, timestamps))
        return True

    def event(self, event_type: str, message: str, **kwargs: object) -> None:
        self.events.append(event_type)

    def alert(self, subject: str, body: str) -> None:
        self.alerts.append(subject)

    def active_gpu_uuids(self, uid: int) -> list[str]:
        return ["GPU-ONE"] if any(status == "active" for _, status, _ in self.transitions) else []


class FakeController:
    def __init__(self, fail_grant: bool = False) -> None:
        self.fail_grant = fail_grant
        self.grants: list[tuple[str, int]] = []
        self.revokes: list[tuple[str, int]] = []
        self.signals: list[tuple[list[int], signal.Signals]] = []

    def prepare_for_owner(self, devices: tuple[GPUDevice, ...], uid: int) -> None:
        return None

    def grant(self, device: GPUDevice, uid: int) -> None:
        if self.fail_grant:
            raise AccessError("grant failed")
        self.grants.append((device.uuid, uid))

    def revoke(self, device: GPUDevice, uid: int) -> None:
        self.revokes.append((device.uuid, uid))

    def owner_processes(self, devices: tuple[GPUDevice, ...], uid: int) -> list[GPUProcess]:
        return [GPUProcess(gpu_uuid=devices[0].uuid, pid=99, name="python", uid=uid)]

    def signal_processes(self, processes: list[GPUProcess], action: signal.Signals) -> None:
        self.signals.append(([item.pid for item in processes], action))


def state(now: datetime, **changes: object) -> ReservationState:
    device = GPUDevice(
        uuid="GPU-ONE",
        index=0,
        name="A100",
        memory_mb=81920,
        device_path="/dev/nvidia0",
    )
    base = ReservationState(
        id=1,
        username="alice",
        uid=21001,
        kind="user",
        status="confirmed",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
        access_granted_at=None,
        access_released_at=None,
        term_sent_at=None,
        devices=(device,),
    )
    return replace(base, **changes)


def test_reconciler_activates_all_devices(tmp_path) -> None:
    now = datetime.now(UTC)
    repository = FakeRepository([state(now)])
    controller = FakeController()
    reconciler = Reconciler(
        repository,
        controller,  # type: ignore[arg-type]
        runtime_dir=tmp_path / "run",
        cache_path=tmp_path / "state.json",
    )
    reconciler.tick(now)
    assert controller.grants == [("GPU-ONE", 21001)]
    assert repository.transitions[0][1] == "active"


def test_reconciler_marks_failed_activation_and_rolls_back(tmp_path) -> None:
    now = datetime.now(UTC)
    repository = FakeRepository([state(now)])
    controller = FakeController(fail_grant=True)
    reconciler = Reconciler(
        repository,
        controller,  # type: ignore[arg-type]
        runtime_dir=tmp_path / "run",
        cache_path=tmp_path / "state.json",
    )
    reconciler.tick(now)
    assert repository.transitions[0][1] == "activation_failed"
    assert repository.alerts


def test_reconciler_sends_term_at_grace_boundary(tmp_path) -> None:
    now = datetime.now(UTC)
    reservation = state(
        now,
        status="active",
        ends_at=now + timedelta(minutes=5),
        access_granted_at=now - timedelta(hours=1),
    )
    repository = FakeRepository([reservation])
    controller = FakeController()
    reconciler = Reconciler(
        repository,
        controller,  # type: ignore[arg-type]
        runtime_dir=tmp_path / "run",
        cache_path=tmp_path / "state.json",
    )
    reconciler.tick(now)
    assert controller.signals == [([99], signal.SIGTERM)]
    assert repository.transitions[0][1] == "stopping"


def test_reconciler_restores_acl_for_active_reservation_after_restart(tmp_path) -> None:
    now = datetime.now(UTC)
    reservation = state(
        now,
        status="active",
        access_granted_at=now - timedelta(hours=1),
    )
    repository = FakeRepository([reservation])
    controller = FakeController()
    reconciler = Reconciler(
        repository,
        controller,  # type: ignore[arg-type]
        runtime_dir=tmp_path / "run",
        cache_path=tmp_path / "state.json",
    )
    reconciler.tick(now)
    assert controller.grants == [("GPU-ONE", 21001)]


def test_reconciler_revokes_before_kill_at_end(tmp_path) -> None:
    now = datetime.now(UTC)
    reservation = state(
        now,
        status="stopping",
        ends_at=now,
        access_granted_at=now - timedelta(hours=1),
        term_sent_at=now - timedelta(minutes=5),
    )
    repository = FakeRepository([reservation])
    controller = FakeController()
    reconciler = Reconciler(
        repository,
        controller,  # type: ignore[arg-type]
        runtime_dir=tmp_path / "run",
        cache_path=tmp_path / "state.json",
    )
    reconciler.tick(now)
    assert controller.revokes == [("GPU-ONE", 21001)]
    assert controller.signals == [([99], signal.SIGKILL)]
    assert repository.transitions[0][1] == "completed"
