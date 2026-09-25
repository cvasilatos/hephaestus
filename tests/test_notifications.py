from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from booking.models import NotificationOutbox, Reservation, SiteConfiguration
from booking.notifications import queue_due_reservation_warnings, queue_email_verification
from booking.services import create_reservation


@pytest.mark.django_db
def test_email_verification_is_queued(user) -> None:
    notification = queue_email_verification(user)
    assert notification.recipient == "alice@example.edu"
    assert "/profile/email/verify/" in notification.body


@pytest.mark.django_db
def test_warning_is_queued_once(user, gpu) -> None:
    starts_at = timezone.now() - timedelta(minutes=20)
    ends_at = timezone.now() + timedelta(minutes=10)
    reservation = create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="soon",
        actor=user,
    )
    reservation.status = Reservation.Status.ACTIVE
    reservation.save()

    assert queue_due_reservation_warnings() == 1
    assert queue_due_reservation_warnings() == 0
    assert NotificationOutbox.objects.filter(kind=NotificationOutbox.Kind.ENDING_SOON).count() == 1


@pytest.mark.django_db
def test_warning_can_be_disabled(user, gpu) -> None:
    configuration = SiteConfiguration.get_solo()
    configuration.warning_minutes = 0
    configuration.save()
    starts_at = timezone.now() - timedelta(minutes=20)
    ends_at = timezone.now() + timedelta(minutes=1)
    reservation = create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="soon",
        actor=user,
    )
    reservation.status = Reservation.Status.ACTIVE
    reservation.save()

    assert queue_due_reservation_warnings() == 0
    assert not NotificationOutbox.objects.exists()
