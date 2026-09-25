from __future__ import annotations

import json
import logging
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .controller import AccessError, GPUController
from .repository import RepositoryError
from .types import GPUDevice, Policy, ReservationState

logger = logging.getLogger(__name__)


class Repository(Protocol):
    def policy(self) -> Policy: ...
    def reservations(self) -> list[ReservationState]: ...
    def transition(
        self,
        reservation_id: int,
        status: str,
        *,
        expected_statuses: tuple[str, ...],
        **timestamps: datetime,
    ) -> bool: ...
    def event(
        self,
        event_type: str,
        message: str,
        *,
        reservation_id: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None: ...
    def alert(self, subject: str, body: str) -> None: ...
    def active_gpu_uuids(self, uid: int) -> list[str]: ...


class Reconciler:
    def __init__(
        self,
        repository: Repository,
        controller: GPUController,
        *,
        runtime_dir: Path,
        cache_path: Path,
        dry_run: bool = False,
    ) -> None:
        self.repository = repository
        self.controller = controller
        self.runtime_dir = runtime_dir
        self.cache_path = cache_path
        self.dry_run = dry_run
        self._ensured_reservations: set[int] = set()

    def tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        try:
            policy = self.repository.policy()
            reservations = self.repository.reservations()
        except RepositoryError:
            logger.exception("Database unavailable; applying cached expiry policy")
            self._expire_cached(current)
            return

        self._save_cache(reservations)
        for reservation in reservations:
            if (
                reservation.access_granted_at
                and not reservation.access_released_at
                and reservation.ends_at <= current
            ):
                self._release(reservation, current)

        for reservation in reservations:
            if (
                reservation.status in {"active", "stopping"}
                and reservation.ends_at > current
                and reservation.id not in self._ensured_reservations
            ):
                self._restore_active(reservation)

        for reservation in reservations:
            stopping_at = reservation.ends_at - timedelta(minutes=policy.grace_minutes)
            if (
                reservation.status == "active"
                and reservation.kind == "user"
                and reservation.term_sent_at is None
                and stopping_at <= current < reservation.ends_at
            ):
                self._begin_stopping(reservation, current)

        for reservation in reservations:
            if (
                reservation.status == "confirmed"
                and reservation.starts_at <= current < reservation.ends_at
            ):
                self._activate(reservation, current)

    def _activate(self, reservation: ReservationState, now: datetime) -> None:
        if not reservation.devices:
            self._fail(reservation, "No healthy enabled GPUs remain in this reservation")
            return
        granted: list[GPUDevice] = []
        try:
            if reservation.kind == "user":
                if reservation.uid is None:
                    raise AccessError("Reservation has no Linux UID")
                self.controller.prepare_for_owner(reservation.devices, reservation.uid)
                for device in reservation.devices:
                    self.controller.grant(device, reservation.uid)
                    granted.append(device)
            else:
                self.controller.prepare_for_owner(reservation.devices, uid=-1)
            if not self.dry_run:
                changed = self.repository.transition(
                    reservation.id,
                    "active",
                    expected_statuses=("confirmed",),
                    access_granted_at=now,
                )
                if not changed:
                    if reservation.uid is not None:
                        for device in granted:
                            self.controller.revoke(device, reservation.uid)
                    logger.info(
                        "Reservation %s changed while activation was in flight", reservation.id
                    )
                    return
                self.repository.event(
                    "agent.activated",
                    "GPU access activated",
                    reservation_id=reservation.id,
                    metadata={"gpu_uuids": [item.uuid for item in reservation.devices]},
                )
                if reservation.uid is not None:
                    self._sync_runtime(reservation.uid)
                self._ensured_reservations.add(reservation.id)
        except AccessError as exc:
            if reservation.uid is not None:
                for device in granted:
                    try:
                        self.controller.revoke(device, reservation.uid)
                    except AccessError:
                        logger.exception("Activation rollback failed for %s", device.uuid)
            self._fail(reservation, str(exc))

    def _begin_stopping(self, reservation: ReservationState, now: datetime) -> None:
        if reservation.uid is None:
            return
        try:
            processes = self.controller.owner_processes(reservation.devices, reservation.uid)
            self.controller.signal_processes(processes, signal.SIGTERM)
            if not self.dry_run:
                changed = self.repository.transition(
                    reservation.id,
                    "stopping",
                    expected_statuses=("active",),
                    term_sent_at=now,
                )
                if not changed:
                    return
                self.repository.event(
                    "agent.termination_started",
                    "Reservation processes received SIGTERM",
                    reservation_id=reservation.id,
                    metadata={"pids": [item.pid for item in processes]},
                )
        except AccessError as exc:
            self._fail(reservation, str(exc))

    def _release(self, reservation: ReservationState, now: datetime) -> None:
        try:
            if reservation.uid is not None:
                for device in reservation.devices:
                    self.controller.revoke(device, reservation.uid)
                processes = self.controller.owner_processes(reservation.devices, reservation.uid)
                self.controller.signal_processes(processes, signal.SIGKILL)
            if not self.dry_run:
                changed = self.repository.transition(
                    reservation.id,
                    "completed",
                    expected_statuses=("active", "stopping"),
                    access_released_at=now,
                )
                if not changed:
                    return
                self.repository.event(
                    "agent.released",
                    "GPU access released",
                    reservation_id=reservation.id,
                )
                if reservation.uid is not None:
                    self._sync_runtime(reservation.uid)
                self._ensured_reservations.discard(reservation.id)
        except AccessError as exc:
            self._fail(reservation, f"Release failed: {exc}")

    def _fail(self, reservation: ReservationState, reason: str) -> None:
        logger.error("Reservation %s enforcement failed: %s", reservation.id, reason)
        if self.dry_run:
            return
        changed = self.repository.transition(
            reservation.id,
            "activation_failed",
            expected_statuses=(reservation.status,),
        )
        if not changed:
            return
        self._ensured_reservations.discard(reservation.id)
        self.repository.event(
            "agent.activation_failed",
            "GPU enforcement failed",
            reservation_id=reservation.id,
            metadata={"reason": reason},
        )
        self.repository.alert(
            f"GPU reservation {reservation.id} needs attention",
            f"The enforcement agent could not reconcile reservation {reservation.id}: {reason}",
        )

    def _sync_runtime(self, uid: int) -> None:
        uuids = self.repository.active_gpu_uuids(uid)
        users_dir = self.runtime_dir / "users"
        users_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        target = users_dir / str(uid)
        temporary = target.with_suffix(".tmp")
        if uuids:
            temporary.write_text(",".join(uuids) + "\n")
            temporary.chmod(0o444)
            temporary.replace(target)
        else:
            target.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

    def _restore_active(self, reservation: ReservationState) -> None:
        restored: list[GPUDevice] = []
        try:
            if not reservation.devices:
                raise AccessError("Active reservation has no healthy enabled GPUs")
            if reservation.kind == "user":
                if reservation.uid is None:
                    raise AccessError("Active reservation has no Linux UID")
                for device in reservation.devices:
                    self.controller.grant(device, reservation.uid)
                    restored.append(device)
                if not self.dry_run:
                    self._sync_runtime(reservation.uid)
            self._ensured_reservations.add(reservation.id)
            logger.info("Restored enforcement for active reservation %s", reservation.id)
        except AccessError as exc:
            if reservation.uid is not None:
                for device in restored:
                    try:
                        self.controller.revoke(device, reservation.uid)
                    except AccessError:
                        logger.exception("Active restoration rollback failed for %s", device.uuid)
            self._fail(reservation, f"Active access restoration failed: {exc}")

    def _save_cache(self, reservations: list[ReservationState]) -> None:
        if self.dry_run:
            return
        active = [
            {
                "id": item.id,
                "uid": item.uid,
                "ends_at": item.ends_at.isoformat(),
                "devices": [device.__dict__ for device in item.devices],
            }
            for item in reservations
            if item.access_granted_at and not item.access_released_at
        ]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(active))
        temporary.chmod(0o600)
        temporary.replace(self.cache_path)

    def _expire_cached(self, now: datetime) -> None:
        try:
            cached = json.loads(self.cache_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        retained = []
        for item in cached:
            if datetime.fromisoformat(item["ends_at"]) > now:
                retained.append(item)
                continue
            uid = item.get("uid")
            if uid is None:
                continue
            devices = tuple(GPUDevice(**device) for device in item["devices"])
            for device in devices:
                try:
                    self.controller.revoke(device, int(uid))
                except AccessError:
                    logger.exception("Cached ACL expiry failed for %s", device.uuid)
            processes = self.controller.owner_processes(devices, int(uid))
            self.controller.signal_processes(processes, signal.SIGKILL)
            (self.runtime_dir / "users" / str(uid)).unlink(missing_ok=True)
        self.cache_path.write_text(json.dumps(retained))
