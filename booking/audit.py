from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User

from .models import AuditEvent


def record_event(
    event_type: str,
    message: str,
    *,
    actor: User | None = None,
    object_type: str = "",
    object_id: object = "",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor,
        event_type=event_type,
        object_type=object_type,
        object_id=str(object_id) if object_id else "",
        message=message,
        metadata=metadata or {},
    )
