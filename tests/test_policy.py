from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from booking.forms import ResearchGroupForm, ReservationForm
from booking.models import GPU, FixedGPUAccessGrant, ResearchGroup
from booking.services import create_reservation
from booking.statistics import current_month_window, usage_statistics


@pytest.mark.django_db
def test_user_and_group_statistics_count_gpu_hours(
    user, research_group, gpu, gpu_two, future_window
) -> None:
    starts_at, ends_at = future_window
    create_reservation(
        owner=user,
        gpus=[gpu, gpu_two],
        starts_at=starts_at,
        ends_at=ends_at,
        description="two GPUs",
        actor=user,
    )
    month_start, month_end = current_month_window(starts_at)

    user_usage = usage_statistics(
        starts_at=month_start,
        ends_at=month_end,
        profile=user.profile,
    )
    group_usage = usage_statistics(
        starts_at=month_start,
        ends_at=month_end,
        research_group=research_group,
    )

    assert user_usage["reservation_count"] == 1
    assert user_usage["shared_gpu_hours"] == Decimal("2.00")
    assert group_usage["total_gpu_hours"] == Decimal("2.00")


@pytest.mark.django_db
def test_statistics_preserve_gpu_classification_at_booking_time(
    user, research_group, gpu, future_window
) -> None:
    starts_at, ends_at = future_window
    create_reservation(
        owner=user,
        gpus=[gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="shared when booked",
        actor=user,
    )
    gpu.allocation_type = GPU.AllocationType.FIXED
    gpu.owner_group = research_group
    gpu.save()
    month_start, month_end = current_month_window(starts_at)

    usage = usage_statistics(
        starts_at=month_start,
        ends_at=month_end,
        research_group=research_group,
    )

    assert usage["shared_gpu_hours"] == Decimal("1.00")
    assert usage["fixed_gpu_hours"] == Decimal("0.00")


@pytest.mark.django_db
def test_fixed_gpu_requires_recorded_borrowing_permission(user, future_window) -> None:
    other_group = ResearchGroup.objects.get(name="Haven")
    fixed_gpu = GPU.objects.create(
        uuid="GPU-FIXED-OTHER-GROUP",
        current_index=5,
        name="NVIDIA A100",
        allocation_type=GPU.AllocationType.FIXED,
        owner_group=other_group,
    )
    starts_at, ends_at = future_window

    with pytest.raises(ValidationError, match="recorded permission"):
        create_reservation(
            owner=user,
            gpus=[fixed_gpu],
            starts_at=starts_at,
            ends_at=ends_at,
            description="borrow",
            actor=user,
        )

    FixedGPUAccessGrant.objects.create(
        research_group=user.profile.research_group,
        gpu=fixed_gpu,
    )
    reservation = create_reservation(
        owner=user,
        gpus=[fixed_gpu],
        starts_at=starts_at,
        ends_at=ends_at,
        description="approved borrow",
        actor=user,
    )
    assert reservation.pk is not None


@pytest.mark.django_db
def test_monthly_shared_gpu_hour_quota_is_enforced(user, gpu, future_window) -> None:
    group = user.profile.research_group
    group.monthly_shared_gpu_hours = Decimal("0.50")
    group.save()
    starts_at, ends_at = future_window
    form = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "over quota",
        },
        user=user,
    )
    assert not form.is_valid()
    assert "shared GPU-hours" in str(form.non_field_errors())


@pytest.mark.django_db
def test_group_membership_is_required(user, gpu, future_window) -> None:
    user.profile.research_group = None
    user.profile.save()
    starts_at, ends_at = future_window
    form = ReservationForm(
        data={
            "gpus": [gpu.pk],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "description": "no group",
        },
        user=user,
    )
    assert not form.is_valid()
    assert "assign you to a research group" in str(form.non_field_errors())


@pytest.mark.django_db
def test_admin_can_assign_any_discovered_gpu_to_a_group(user, research_group, gpu, gpu_two) -> None:
    form = ResearchGroupForm(
        data={
            "name": research_group.name,
            "monthly_shared_gpu_hours": "500.00",
            "enabled": "on",
            "notes": "Flexible deployment",
            "members": [user.profile.pk],
            "fixed_gpus": [gpu.pk, gpu_two.pk],
            "borrowed_gpus": [],
        },
        instance=research_group,
    )
    assert form.is_valid(), form.errors
    form.save()

    gpu.refresh_from_db()
    gpu_two.refresh_from_db()
    assert gpu.owner_group == research_group
    assert gpu_two.owner_group == research_group
    assert gpu.allocation_type == GPU.AllocationType.FIXED
    assert gpu_two.allocation_type == GPU.AllocationType.FIXED
