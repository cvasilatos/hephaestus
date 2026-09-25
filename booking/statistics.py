from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict
from zoneinfo import ZoneInfo

from django.db.models import QuerySet

from .models import GPUAllocation, ResearchGroup, Reservation, SiteConfiguration, UserProfile

COUNTED_STATUSES = (
    Reservation.Status.CONFIRMED,
    Reservation.Status.ACTIVE,
    Reservation.Status.STOPPING,
    Reservation.Status.COMPLETED,
)
DISPLAY_PRECISION = Decimal("0.01")


class UsageStatistics(TypedDict):
    reservation_count: int
    total_gpu_hours: Decimal
    shared_gpu_hours: Decimal
    fixed_gpu_hours: Decimal


def current_month_window(reference: datetime | None = None) -> tuple[datetime, datetime]:
    configuration = SiteConfiguration.get_solo()
    zone = ZoneInfo(configuration.site_timezone)
    local = (reference or datetime.now(UTC)).astimezone(zone)
    start_local = datetime(local.year, local.month, 1, tzinfo=zone)
    if local.month == 12:
        end_local = datetime(local.year + 1, 1, 1, tzinfo=zone)
    else:
        end_local = datetime(local.year, local.month + 1, 1, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def iter_month_windows(starts_at: datetime, ends_at: datetime) -> list[tuple[datetime, datetime]]:
    configuration = SiteConfiguration.get_solo()
    zone = ZoneInfo(configuration.site_timezone)
    cursor = starts_at.astimezone(zone)
    result: list[tuple[datetime, datetime]] = []
    while cursor < ends_at.astimezone(zone):
        month_start = datetime(cursor.year, cursor.month, 1, tzinfo=zone)
        if cursor.month == 12:
            next_month = datetime(cursor.year + 1, 1, 1, tzinfo=zone)
        else:
            next_month = datetime(cursor.year, cursor.month + 1, 1, tzinfo=zone)
        result.append(
            (
                max(starts_at, month_start.astimezone(UTC)),
                min(ends_at, next_month.astimezone(UTC)),
            )
        )
        cursor = next_month
    return result


def _hours(starts_at: datetime, ends_at: datetime) -> Decimal:
    seconds = Decimal(str((ends_at - starts_at).total_seconds()))
    return seconds / Decimal(3600)


def _allocations_for_window(
    *,
    starts_at: datetime,
    ends_at: datetime,
    profile: UserProfile | None = None,
    research_group: ResearchGroup | None = None,
    shared_only: bool = False,
    exclude_reservation_id: int | None = None,
) -> QuerySet[GPUAllocation]:
    allocations = GPUAllocation.objects.filter(
        reservation__status__in=COUNTED_STATUSES,
        reservation__kind=Reservation.Kind.USER,
        reservation__owner__isnull=False,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).select_related("gpu", "reservation")
    if profile is not None:
        allocations = allocations.filter(reservation__owner=profile.user)
    if research_group is not None:
        allocations = allocations.filter(reservation__research_group=research_group)
    if shared_only:
        allocations = allocations.filter(allocation_type="shared")
    if exclude_reservation_id is not None:
        allocations = allocations.exclude(reservation_id=exclude_reservation_id)
    return allocations


def usage_statistics(
    *,
    starts_at: datetime,
    ends_at: datetime,
    profile: UserProfile | None = None,
    research_group: ResearchGroup | None = None,
) -> UsageStatistics:
    allocations = _allocations_for_window(
        starts_at=starts_at,
        ends_at=ends_at,
        profile=profile,
        research_group=research_group,
    )
    total = Decimal(0)
    shared = Decimal(0)
    fixed = Decimal(0)
    reservation_ids: set[int] = set()
    for allocation in allocations:
        duration = _hours(max(starts_at, allocation.starts_at), min(ends_at, allocation.ends_at))
        total += duration
        reservation_ids.add(allocation.reservation_id)
        if allocation.allocation_type == "shared":
            shared += duration
        elif allocation.allocation_type == "fixed":
            fixed += duration
    return {
        "reservation_count": len(reservation_ids),
        "total_gpu_hours": total.quantize(DISPLAY_PRECISION),
        "shared_gpu_hours": shared.quantize(DISPLAY_PRECISION),
        "fixed_gpu_hours": fixed.quantize(DISPLAY_PRECISION),
    }


def shared_gpu_hours_for_group(
    research_group: ResearchGroup,
    starts_at: datetime,
    ends_at: datetime,
    *,
    exclude_reservation_id: int | None = None,
) -> Decimal:
    allocations = _allocations_for_window(
        starts_at=starts_at,
        ends_at=ends_at,
        research_group=research_group,
        shared_only=True,
        exclude_reservation_id=exclude_reservation_id,
    )
    total = sum(
        (
            _hours(max(starts_at, allocation.starts_at), min(ends_at, allocation.ends_at))
            for allocation in allocations
        ),
        Decimal(0),
    )
    return total
