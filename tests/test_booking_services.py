from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from booking.forms import ReservationForm, SiteConfigurationForm
from booking.models import AuditEvent, GPUAllocation, Reservation, SiteConfiguration
from booking.services import ReservationConflict, cancel_reservation, create_reservation


@pytest.mark.django_db
def test_multi_gpu_reservation_is_created_atomically(user, gpu, gpu_two, future_window) -> None:
    starts_at, ends_at = future_window
    reservation = create_reservation(
        owner=user,
        gpus=[gpu, gpu_two],
        starts_at=starts_at,
        ends_at=ends_at,
        description="training",
        actor=user,
    )

    assert reservation.status == Reservation.Status.CONFIRMED
    assert set(reservation.allocations.values_list("gpu_id", flat=True)) == {gpu.id, gpu_two.id}
    assert AuditEvent.objects.filter(event_type="reservation.created").exists()


@pytest.mark.django_db
def test_overlap_is_rejected_but_adjacent_booking_is_allowed(
    user, second_user, gpu, future_window
) -> None:
    starts_at, ends_at = future_window
    create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="first",
        actor=user,
    )
    with pytest.raises(ReservationConflict):
        create_reservation(
            owner=second_user,
            gpus=[gpu],
            starts_at=starts_at + timedelta(minutes=30),
            ends_at=ends_at + timedelta(minutes=30),
            description="overlap",
            actor=second_user,
        )

    adjacent = create_reservation(
        owner=second_user,
        gpus=[gpu],
        starts_at=ends_at,
        ends_at=ends_at + timedelta(hours=1),
        description="adjacent",
        actor=second_user,
    )
    assert adjacent.pk is not None


@pytest.mark.django_db
def test_cancel_future_reservation_releases_blocking_allocation(user, gpu, future_window) -> None:
    starts_at, ends_at = future_window
    reservation = create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="cancel me",
        actor=user,
    )
    cancel_reservation(reservation, actor=user)
    reservation.refresh_from_db()

    assert reservation.status == Reservation.Status.CANCELLED
    assert not GPUAllocation.objects.get(reservation=reservation).blocking


@pytest.mark.django_db
def test_form_requires_verified_email(user, gpu, future_window) -> None:
    user.profile.email_verified_at = None
    user.profile.save()
    starts_at, ends_at = future_window
    form = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "test",
        },
        user=user,
    )
    assert not form.is_valid()
    assert "Verify your email" in str(form.non_field_errors())


@pytest.mark.django_db
def test_form_enforces_maximum_seven_days(user, gpu, future_window) -> None:
    starts_at, _ = future_window
    form = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(days=7, minutes=30)).isoformat(),
            "description": "too long",
        },
        user=user,
    )
    assert not form.is_valid()
    assert "maximum duration" in str(form.non_field_errors())


@pytest.mark.django_db
def test_active_early_release_shortens_allocation(user, gpu) -> None:
    starts_at = timezone.now() - timedelta(hours=1)
    ends_at = timezone.now() + timedelta(hours=1)
    reservation = Reservation.objects.create(
        owner=user,
        starts_at=starts_at,
        ends_at=ends_at,
        status=Reservation.Status.ACTIVE,
        access_granted_at=starts_at,
    )
    allocation = GPUAllocation.objects.create(
        reservation=reservation,
        gpu=gpu,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    cancel_reservation(reservation, actor=user)
    allocation.refresh_from_db()
    assert allocation.ends_at < ends_at


@pytest.mark.django_db
def test_disabled_gpu_is_rejected(user, gpu, future_window) -> None:
    gpu.enabled = False
    gpu.save()
    starts_at, ends_at = future_window
    with pytest.raises(ValidationError):
        create_reservation(
            owner=user,
            gpus=[gpu],
            starts_at=starts_at,
            ends_at=ends_at,
            description="nope",
            actor=user,
        )


@pytest.mark.django_db
def test_form_enforces_configurable_gpu_and_open_reservation_limits(
    user, gpu, gpu_two, future_window
) -> None:
    configuration = SiteConfiguration.get_solo()
    configuration.max_gpus_per_reservation = 1
    configuration.max_open_reservations_per_user = 1
    configuration.save()
    starts_at, ends_at = future_window

    too_many_gpus = ReservationForm(
        data={
            "gpus": [gpu.pk, gpu_two.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "too many",
        },
        user=user,
    )
    assert not too_many_gpus.is_valid()
    assert "no more than 1 GPU" in str(too_many_gpus.non_field_errors())

    create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="existing",
        actor=user,
    )
    quota_reached = ReservationForm(
        data={
            "gpus": [gpu_two.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "another",
        },
        user=user,
    )
    assert not quota_reached.is_valid()
    assert "limit for open reservations" in str(quota_reached.non_field_errors())


@pytest.mark.django_db
def test_form_enforces_advance_notice_and_booking_horizon(user, gpu, future_window) -> None:
    configuration = SiteConfiguration.get_solo()
    configuration.booking_notice_minutes = 60
    configuration.booking_horizon_days = 2
    configuration.save()
    starts_at, ends_at = future_window

    too_soon = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "too soon",
        },
        user=user,
    )
    assert not too_soon.is_valid()
    assert "minimum advance notice" in str(too_soon.non_field_errors())

    far_start = starts_at + timedelta(days=3)
    too_far = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": far_start.isoformat(),
            "ends_at": (far_start + timedelta(hours=1)).isoformat(),
            "description": "too far",
        },
        user=user,
    )
    assert not too_far.is_valid()
    assert "beyond the booking horizon" in str(too_far.non_field_errors())


@pytest.mark.django_db
def test_verified_email_requirement_can_be_disabled(user, gpu, future_window) -> None:
    configuration = SiteConfiguration.get_solo()
    configuration.require_verified_email = False
    configuration.save()
    user.profile.email_verified_at = None
    user.profile.save()
    starts_at, ends_at = future_window
    form = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "allowed",
        },
        user=user,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_configuration_requires_coherent_durations() -> None:
    configuration = SiteConfiguration.get_solo()
    form = SiteConfigurationForm(
        data={
            "site_timezone": "UTC",
            "slot_minutes": 30,
            "minimum_reservation_minutes": 60,
            "default_reservation_minutes": 30,
            "max_reservation_minutes": 45,
            "booking_notice_minutes": 0,
            "booking_horizon_days": 0,
            "max_gpus_per_reservation": 0,
            "max_open_reservations_per_user": 0,
            "require_verified_email": "on",
            "allow_user_edits": "on",
            "allow_user_cancellations": "on",
            "allow_early_release": "on",
            "cancellation_cutoff_minutes": 0,
            "warning_minutes": 15,
            "termination_grace_minutes": 5,
            "calendar_days": 7,
            "show_usernames_on_schedule": "on",
        },
        instance=configuration,
    )
    assert not form.is_valid()
    assert "multiple of the slot length" in str(form.non_field_errors())


@pytest.mark.django_db
def test_user_cancellation_and_early_release_follow_policy(user, gpu, future_window) -> None:
    starts_at, ends_at = future_window
    reservation = create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="protected",
        actor=user,
    )
    configuration = SiteConfiguration.get_solo()
    configuration.allow_user_cancellations = False
    configuration.save()

    with pytest.raises(ValidationError, match="cancellation is disabled"):
        cancel_reservation(reservation, actor=user)

    reservation.status = Reservation.Status.ACTIVE
    reservation.starts_at = timezone.now() - timedelta(minutes=5)
    reservation.save()
    configuration.allow_early_release = False
    configuration.save()
    with pytest.raises(ValidationError, match="Early release is disabled"):
        cancel_reservation(reservation, actor=user)
