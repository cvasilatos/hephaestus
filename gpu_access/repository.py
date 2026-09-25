from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .types import GPUDevice, Policy, ReservationState


class RepositoryError(RuntimeError):
    pass


class PostgresRepository:
    def __init__(self, database_url: str, admin_emails: tuple[str, ...] = ()) -> None:
        self.database_url = database_url
        self.admin_emails = admin_emails

    def _connect(self) -> psycopg.Connection[Any]:
        try:
            return psycopg.connect(self.database_url, row_factory=dict_row, autocommit=False)
        except psycopg.Error as exc:
            raise RepositoryError(str(exc)) from exc

    def policy(self) -> Policy:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT warning_minutes, termination_grace_minutes "
                "FROM booking_siteconfiguration WHERE singleton = 1"
            )
            row = cursor.fetchone()
            return Policy(
                warning_minutes=int(row["warning_minutes"]) if row else 15,
                grace_minutes=int(row["termination_grace_minutes"]) if row else 5,
            )

    def reservations(self) -> list[ReservationState]:
        query = """
            SELECT r.id, u.username, p.linux_uid, r.kind, r.status,
                   r.starts_at, r.ends_at, r.access_granted_at,
                   r.access_released_at, r.term_sent_at,
                   COALESCE(jsonb_agg(jsonb_build_object(
                       'uuid', g.uuid, 'index', g.current_index, 'name', g.name,
                       'memory_mb', g.memory_mb, 'device_path', g.device_path,
                       'healthy', g.healthy, 'enabled', g.enabled
                   ) ORDER BY g.current_index) FILTER (WHERE g.id IS NOT NULL), '[]') AS devices
            FROM booking_reservation r
            LEFT JOIN auth_user u ON u.id = r.owner_id
            LEFT JOIN booking_userprofile p ON p.user_id = u.id
            JOIN booking_gpuallocation a ON a.reservation_id = r.id
            JOIN booking_gpu g ON g.id = a.gpu_id
            WHERE r.status IN ('confirmed', 'active', 'stopping', 'activation_failed')
              AND a.blocking = TRUE
            GROUP BY r.id, u.username, p.linux_uid
            ORDER BY r.ends_at, r.id
        """
        with self._connect() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
            except psycopg.Error as exc:
                raise RepositoryError(str(exc)) from exc
        result = []
        for row in rows:
            devices = []
            for device in row["devices"]:
                if not device["enabled"] or not device["healthy"]:
                    continue
                devices.append(
                    GPUDevice(
                        uuid=device["uuid"],
                        index=int(device["index"]),
                        name=device["name"],
                        memory_mb=int(device["memory_mb"]),
                        device_path=device["device_path"],
                    )
                )
            result.append(
                ReservationState(
                    id=int(row["id"]),
                    username=row["username"],
                    uid=row["linux_uid"],
                    kind=row["kind"],
                    status=row["status"],
                    starts_at=row["starts_at"],
                    ends_at=row["ends_at"],
                    access_granted_at=row["access_granted_at"],
                    access_released_at=row["access_released_at"],
                    term_sent_at=row["term_sent_at"],
                    devices=tuple(devices),
                )
            )
        return result

    def transition(
        self,
        reservation_id: int,
        status: str,
        *,
        expected_statuses: tuple[str, ...],
        **timestamps: datetime,
    ) -> bool:
        allowed_columns = {"access_granted_at", "access_released_at", "term_sent_at"}
        if not set(timestamps).issubset(allowed_columns):
            raise ValueError("Unexpected timestamp column")
        assignments = ["status = %s", "updated_at = NOW()"]
        values: list[object] = [status]
        for column, value in timestamps.items():
            assignments.append(f"{column} = %s")
            values.append(value)
        values.extend((reservation_id, list(expected_statuses)))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE booking_reservation SET {', '.join(assignments)} "  # noqa: S608
                "WHERE id = %s AND status = ANY(%s)",
                values,
            )
            changed = cursor.rowcount == 1
            connection.commit()
            return changed

    def event(
        self,
        event_type: str,
        message: str,
        *,
        reservation_id: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO booking_auditevent "
                "(actor_id, event_type, object_type, object_id, message, metadata, created_at) "
                "VALUES (NULL, %s, %s, %s, %s, %s, NOW())",
                (
                    event_type,
                    "reservation" if reservation_id else "agent",
                    str(reservation_id or ""),
                    message,
                    json.dumps(metadata or {}),
                ),
            )
            connection.commit()

    def alert(self, subject: str, body: str) -> None:
        if not self.admin_emails:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            for recipient in self.admin_emails:
                cursor.execute(
                    "INSERT INTO booking_notificationoutbox "
                    "(kind, recipient, subject, body, send_after, attempts, "
                    "sent_at, last_error, created_at) "
                    "VALUES ('admin_alert', %s, %s, %s, NOW(), 0, NULL, '', NOW())",
                    (recipient, subject, body),
                )
            connection.commit()

    def active_gpu_uuids(self, uid: int) -> list[str]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT g.uuid FROM booking_reservation r "
                "JOIN booking_userprofile p ON p.user_id = r.owner_id "
                "JOIN booking_gpuallocation a ON a.reservation_id = r.id "
                "JOIN booking_gpu g ON g.id = a.gpu_id "
                "WHERE p.linux_uid = %s AND r.status IN ('active', 'stopping') "
                "AND r.access_released_at IS NULL ORDER BY g.uuid",
                (uid,),
            )
            return [row["uuid"] for row in cursor.fetchall()]

    def managed_uids(self) -> list[int]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT linux_uid FROM booking_userprofile")
            return [int(row["linux_uid"]) for row in cursor.fetchall()]

    def sync_devices(self, devices: list[GPUDevice]) -> None:
        seen = [device.uuid for device in devices]
        with self._connect() as connection, connection.cursor() as cursor:
            for device in devices:
                cursor.execute(
                    "INSERT INTO booking_gpu "
                    "(uuid, current_index, device_path, name, memory_mb, enabled, healthy, "
                    "allocation_type, last_seen_at, status_message, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, TRUE, TRUE, 'unmanaged', NOW(), '', NOW(), "
                    "NOW()) "
                    "ON CONFLICT (uuid) DO UPDATE SET current_index = EXCLUDED.current_index, "
                    "device_path = EXCLUDED.device_path, name = EXCLUDED.name, "
                    "memory_mb = EXCLUDED.memory_mb, healthy = TRUE, last_seen_at = NOW(), "
                    "status_message = '', updated_at = NOW()",
                    (device.uuid, device.index, device.device_path, device.name, device.memory_mb),
                )
            if seen:
                cursor.execute(
                    "UPDATE booking_gpu SET healthy = FALSE, status_message = 'Not detected', "
                    "updated_at = NOW() WHERE NOT (uuid = ANY(%s))",
                    (seen,),
                )
            else:
                cursor.execute(
                    "UPDATE booking_gpu SET healthy = FALSE, status_message = 'Not detected', "
                    "updated_at = NOW()"
                )
            connection.commit()
