from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from .audit import record_event
from .models import GPU, FixedGPUAccessGrant, GPUAllocation, Reservation, SiteConfiguration
from .statistics import iter_month_windows, shared_gpu_hours_for_group


class ReservationConflict(ValidationError):
    pass


def available_gpus_for_user(
    user: User | None,
    *,
    maintenance: bool = False,
) -> QuerySet[GPU]:
    available = GPU.objects.filter(enabled=True, healthy=True)
    configuration = SiteConfiguration.get_solo()
    if maintenance or not user or not configuration.enforce_gpu_allocation_rules:
        return available
    group = user.profile.research_group
    if group is None or not group.enabled:
        if configuration.require_group_membership:
            return available.none()
        return available.filter(allocation_type=GPU.AllocationType.SHARED)
    allowed = Q(allocation_type=GPU.AllocationType.SHARED) | Q(
        allocation_type=GPU.AllocationType.FIXED,
        owner_group=group,
    )
    if configuration.require_fixed_gpu_permission:
        allowed |= Q(
            allocation_type=GPU.AllocationType.FIXED,
            access_grants__research_group=group,
        ) & (
            Q(access_grants__expires_at__isnull=True)
            | Q(access_grants__expires_at__gt=timezone.now())
        )
    else:
        allowed |= Q(allocation_type=GPU.AllocationType.FIXED)
    return available.filter(allowed).distinct()


def validate_reservation_policy(
    owner: User | None,
    gpus: Iterable[GPU],
    starts_at: datetime,
    ends_at: datetime,
    *,
    exclude_reservation_id: int | None = None,
    lock_group: bool = False,
) -> None:
    if owner is None:
        return
    configuration = SiteConfiguration.get_solo()
    group = owner.profile.research_group
    if configuration.require_group_membership and group is None:
        raise ValidationError("Ask an administrator to assign you to a research group first.")
    if group is None:
        return
    if lock_group:
        group = type(group).objects.select_for_update().get(pk=group.pk)
    if not group.enabled:
        raise ValidationError("Your research group is disabled.")

    selected = list(gpus)
    if configuration.enforce_gpu_allocation_rules:
        unmanaged = [gpu for gpu in selected if gpu.allocation_type == GPU.AllocationType.UNMANAGED]
        if unmanaged:
            raise ValidationError(
                "These GPUs have not been assigned by an administrator: "
                + ", ".join(str(gpu) for gpu in unmanaged)
            )
        foreign_fixed = [
            gpu
            for gpu in selected
            if gpu.allocation_type == GPU.AllocationType.FIXED and gpu.owner_group_id != group.pk
        ]
        if foreign_fixed and configuration.require_fixed_gpu_permission:
            permitted_ids = set(
                FixedGPUAccessGrant.objects.filter(
                    research_group=group,
                    gpu__in=foreign_fixed,
                )
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=ends_at))
                .values_list("gpu_id", flat=True)
            )
            forbidden = [gpu for gpu in foreign_fixed if gpu.pk not in permitted_ids]
            if forbidden:
                raise ValidationError(
                    "Your group needs recorded permission to use: "
                    + ", ".join(str(gpu) for gpu in forbidden)
                )

    shared_gpu_count = sum(gpu.allocation_type == GPU.AllocationType.SHARED for gpu in selected)
    quota = group.monthly_shared_gpu_hours
    if not configuration.enforce_group_quotas or not shared_gpu_count or quota == 0:
        return
    zone = ZoneInfo(configuration.site_timezone)
    for period_start, period_end in iter_month_windows(starts_at, ends_at):
        existing = shared_gpu_hours_for_group(
            group,
            period_start,
            period_end,
            exclude_reservation_id=exclude_reservation_id,
        )
        seconds = Decimal(str((period_end - period_start).total_seconds()))
        requested = seconds / Decimal(3600) * shared_gpu_count
        if existing + requested > quota:
            month = period_start.astimezone(zone).strftime("%B %Y")
            projected = (existing + requested).quantize(Decimal("0.01"))
            raise ValidationError(
                f"This reservation would use {projected} of {quota} shared GPU-hours "
                f"for {group.name} in {month}."
            )


def _lock_and_validate_gpus(gpus: Iterable[GPU]) -> list[GPU]:
    requested_ids = sorted({gpu.pk for gpu in gpus if gpu.pk is not None})
    locked = list(GPU.objects.select_for_update().filter(pk__in=requested_ids))
    if len(locked) != len(requested_ids):
        raise ValidationError("One or more selected GPUs no longer exist.")
    unavailable = [str(gpu) for gpu in locked if not gpu.enabled or not gpu.healthy]
    if unavailable:
        raise ValidationError(f"Unavailable GPUs: {', '.join(unavailable)}")
    return locked


def _check_conflicts(gpus: list[GPU], starts_at: datetime, ends_at: datetime) -> None:
    conflicts = list(
        GPUAllocation.objects.select_for_update()
        .filter(
            gpu__in=gpus,
            blocking=True,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        )
        .select_related("gpu", "reservation")
    )
    if conflicts:
        names = ", ".join(sorted({str(item.gpu) for item in conflicts}))
        raise ReservationConflict(f"The requested time conflicts on: {names}")


@transaction.atomic
def create_reservation(
    *,
    owner: User | None,
    gpus: Iterable[GPU],
    starts_at: datetime,
    ends_at: datetime,
    description: str,
    kind: str = "user",
    actor: User | None = None,
) -> Reservation:
    locked_gpus = _lock_and_validate_gpus(gpus)
    if not locked_gpus:
        raise ValidationError("Select at least one GPU.")
    validate_reservation_policy(owner, locked_gpus, starts_at, ends_at, lock_group=True)
    _check_conflicts(locked_gpus, starts_at, ends_at)
    reservation = Reservation(
        owner=owner,
        research_group=owner.profile.research_group if owner else None,
        kind=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        description=description.strip(),
    )
    reservation.full_clean()
    reservation.save()
    try:
        GPUAllocation.objects.bulk_create(
            [
                GPUAllocation(
                    reservation=reservation,
                    gpu=gpu,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    allocation_type=gpu.allocation_type,
                )
                for gpu in locked_gpus
            ]
        )
    except IntegrityError as exc:
        raise ReservationConflict("Another reservation claimed one of those GPUs.") from exc
    record_event(
        "reservation.created",
        "Reservation created",
        actor=actor,
        object_type="reservation",
        object_id=reservation.pk,
        metadata={"gpu_uuids": [gpu.uuid for gpu in locked_gpus], "kind": kind},
    )
    return reservation


@transaction.atomic
def update_reservation(
    reservation: Reservation,
    *,
    gpus: Iterable[GPU],
    starts_at: datetime,
    ends_at: datetime,
    description: str,
    actor: User,
) -> Reservation:
    locked_reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if not locked_reservation.can_be_edited:
        raise ValidationError("Only future confirmed reservations can be edited.")
    locked_gpus = _lock_and_validate_gpus(gpus)
    validate_reservation_policy(
        locked_reservation.owner,
        locked_gpus,
        starts_at,
        ends_at,
        exclude_reservation_id=locked_reservation.pk,
        lock_group=True,
    )
    old_gpu_ids = list(locked_reservation.allocations.values_list("gpu_id", flat=True))
    locked_reservation.allocations.update(blocking=False)
    _check_conflicts(locked_gpus, starts_at, ends_at)
    locked_reservation.allocations.all().delete()
    locked_reservation.starts_at = starts_at
    locked_reservation.ends_at = ends_at
    locked_reservation.description = description.strip()
    locked_reservation.research_group = (
        locked_reservation.owner.profile.research_group if locked_reservation.owner else None
    )
    locked_reservation.full_clean()
    locked_reservation.save()
    try:
        GPUAllocation.objects.bulk_create(
            [
                GPUAllocation(
                    reservation=locked_reservation,
                    gpu=gpu,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    allocation_type=gpu.allocation_type,
                )
                for gpu in locked_gpus
            ]
        )
    except IntegrityError as exc:
        raise ReservationConflict("Another reservation claimed one of those GPUs.") from exc
    record_event(
        "reservation.updated",
        "Reservation updated",
        actor=actor,
        object_type="reservation",
        object_id=locked_reservation.pk,
        metadata={"old_gpu_ids": old_gpu_ids, "gpu_uuids": [gpu.uuid for gpu in locked_gpus]},
    )
    return locked_reservation


@transaction.atomic
def cancel_reservation(reservation: Reservation, *, actor: User, admin: bool = False) -> None:
    locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if not admin and locked.owner_id != actor.pk:
        raise PermissionDenied
    if locked.status in {Reservation.Status.COMPLETED, Reservation.Status.CANCELLED}:
        raise ValidationError("This reservation has already ended.")
    now = timezone.now()
    if not admin:
        configuration = SiteConfiguration.get_solo()
        if locked.status == Reservation.Status.CONFIRMED and locked.starts_at > now:
            if not configuration.allow_user_cancellations:
                raise ValidationError("User cancellation is disabled by policy.")
            cutoff = locked.starts_at - timedelta(minutes=configuration.cancellation_cutoff_minutes)
            if now > cutoff:
                raise ValidationError("The cancellation cutoff for this reservation has passed.")
        elif not configuration.allow_early_release:
            raise ValidationError("Early release is disabled by policy.")
    if locked.status == Reservation.Status.CONFIRMED and locked.starts_at > now:
        locked.status = Reservation.Status.CANCELLED
        locked.cancelled_at = now
        locked.allocations.update(blocking=False)
    else:
        locked.ends_at = now
        locked.cancelled_at = now
        locked.allocations.update(ends_at=now)
    locked.save()
    record_event(
        "reservation.cancelled"
        if locked.status == Reservation.Status.CANCELLED
        else "reservation.ended",
        "Reservation cancelled"
        if locked.status == Reservation.Status.CANCELLED
        else "Early release requested",
        actor=actor,
        object_type="reservation",
        object_id=locked.pk,
        metadata={"admin": admin},
    )


@transaction.atomic
def retry_activation(reservation: Reservation, *, actor: User) -> None:
    locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status != Reservation.Status.ACTIVATION_FAILED or locked.ends_at <= timezone.now():
        raise ValidationError("This reservation cannot be retried.")
    locked.status = Reservation.Status.CONFIRMED
    locked.save(update_fields=["status", "updated_at"])
    record_event(
        "reservation.activation_retried",
        "Activation retry requested",
        actor=actor,
        object_type="reservation",
        object_id=locked.pk,
    )
