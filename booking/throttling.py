from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginThrottle


def throttle_key(username: str, address: str) -> str:
    value = f"{settings.SECRET_KEY}\0{username.casefold()}\0{address}"
    return hashlib.sha256(value.encode()).hexdigest()


def is_locked(username: str, address: str) -> bool:
    record = LoginThrottle.objects.filter(key_hash=throttle_key(username, address)).first()
    return bool(record and record.locked_until and record.locked_until > timezone.now())


@transaction.atomic
def register_failure(username: str, address: str) -> None:
    now = timezone.now()
    key = throttle_key(username, address)
    record, _ = LoginThrottle.objects.select_for_update().get_or_create(
        key_hash=key,
        defaults={"window_started_at": now},
    )
    window = timedelta(seconds=settings.LOGIN_THROTTLE_WINDOW_SECONDS)
    if record.window_started_at + window <= now:
        record.attempts = 0
        record.window_started_at = now
        record.locked_until = None
    record.attempts += 1
    if record.attempts >= settings.LOGIN_THROTTLE_ATTEMPTS:
        record.locked_until = now + timedelta(seconds=settings.LOGIN_THROTTLE_LOCK_SECONDS)
    record.save()


def clear_failures(username: str, address: str) -> None:
    LoginThrottle.objects.filter(key_hash=throttle_key(username, address)).delete()
