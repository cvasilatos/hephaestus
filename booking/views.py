from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import record_event
from .decorators import os_admin_required
from .forms import (
    EmailProfileForm,
    GPUSettingsForm,
    PAMLoginForm,
    ResearchGroupForm,
    ReservationForm,
    SiteConfigurationForm,
)
from .models import (
    GPU,
    AuditEvent,
    GPUAllocation,
    ResearchGroup,
    Reservation,
    SiteConfiguration,
    UserProfile,
)
from .notifications import queue_email_verification
from .services import (
    ReservationConflict,
    cancel_reservation,
    create_reservation,
    retry_activation,
    update_reservation,
)
from .statistics import current_month_window, usage_statistics
from .throttling import clear_failures, is_locked, register_failure
from .tokens import read_email_token


def _client_address(request: HttpRequest) -> str:
    return request.META.get("REMOTE_ADDR", "unknown")


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = PAMLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username = str(form.cleaned_data["username"])
        address = _client_address(request)
        if is_locked(username, address):
            form.add_error(None, "Too many failed attempts. Try again later.")
        else:
            user = authenticate(
                request,
                username=username,
                password=form.cleaned_data["password"],
            )
            if user is None:
                register_failure(username, address)
                form.add_error(None, "Invalid credentials or account is not eligible.")
            else:
                clear_failures(username, address)
                login(request, user)
                record_event("session.login", "User signed in", actor=user)
                return redirect("dashboard")
    return render(request, "booking/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    actor = request.user if request.user.is_authenticated else None
    if actor:
        record_event("session.logout", "User signed out", actor=actor)
    logout(request)
    return redirect("login")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    configuration = SiteConfiguration.get_solo()
    schedule_end = now + timedelta(days=configuration.calendar_days)
    gpus = list(GPU.objects.all())
    allocations = list(
        GPUAllocation.objects.filter(
            blocking=True,
            starts_at__lt=schedule_end,
            ends_at__gt=now,
        ).select_related("gpu", "reservation__owner")
    )
    by_gpu: dict[int, list[GPUAllocation]] = {gpu.pk: [] for gpu in gpus}
    for allocation in allocations:
        by_gpu.setdefault(allocation.gpu_id, []).append(allocation)

    cards: list[dict[str, Any]] = []
    for gpu in gpus:
        entries = sorted(by_gpu.get(gpu.pk, []), key=lambda item: item.starts_at)
        active = next((item for item in entries if item.starts_at <= now < item.ends_at), None)
        upcoming = next((item for item in entries if item.starts_at > now), None)
        active_owner = None
        if active:
            active_owner = _schedule_owner_label(
                active.reservation,
                viewer_id=request.user.pk,
                show_usernames=configuration.show_usernames_on_schedule,
            )
        cards.append(
            {
                "gpu": gpu,
                "active": active,
                "active_owner": active_owner,
                "upcoming": upcoming,
            }
        )

    events = []
    seen: set[int] = set()
    for allocation in allocations:
        reservation = allocation.reservation
        if reservation.pk in seen:
            continue
        seen.add(reservation.pk)
        gpu_labels = [str(item.gpu) for item in reservation.allocations.select_related("gpu")]
        owner = _schedule_owner_label(
            reservation,
            viewer_id=request.user.pk,
            show_usernames=configuration.show_usernames_on_schedule,
        )
        events.append(
            {
                "id": reservation.pk,
                "owner": owner,
                "gpus": gpu_labels,
                "startsAt": reservation.starts_at.isoformat(),
                "endsAt": reservation.ends_at.isoformat(),
                "status": reservation.status,
                "kind": reservation.kind,
            }
        )
    my_active = request.user.reservations.filter(
        status__in=[Reservation.Status.ACTIVE, Reservation.Status.STOPPING]
    ).first()
    month_start, month_end = current_month_window(now)
    profile = request.user.profile
    user_usage = usage_statistics(
        starts_at=month_start,
        ends_at=month_end,
        profile=profile,
    )
    group_usage = None
    if profile.research_group:
        group_usage = usage_statistics(
            starts_at=month_start,
            ends_at=month_end,
            research_group=profile.research_group,
        )
    return render(
        request,
        "booking/dashboard.html",
        {
            "cards": cards,
            "events": events,
            "my_active": my_active,
            "now": now,
            "calendar_days": configuration.calendar_days,
            "user_usage": user_usage,
            "group_usage": group_usage,
            "usage_month": month_start,
        },
    )


@login_required
def policy(request: HttpRequest) -> HttpResponse:
    configuration = SiteConfiguration.get_solo()
    return render(
        request,
        "booking/policy.html",
        {
            "groups": ResearchGroup.objects.prefetch_related("fixed_gpus").all(),
            "shared_gpus": GPU.objects.filter(allocation_type=GPU.AllocationType.SHARED),
            "policy_checklist": [
                item.strip() for item in configuration.policy_checklist.splitlines() if item.strip()
            ],
        },
    )


def _schedule_owner_label(
    reservation: Reservation,
    *,
    viewer_id: int,
    show_usernames: bool,
) -> str:
    if reservation.owner is None:
        return "Maintenance"
    if reservation.owner_id == viewer_id and not show_usernames:
        return "You"
    if show_usernames:
        return reservation.owner.username
    return "Reserved"


@login_required
def profile_email(request: HttpRequest) -> HttpResponse:
    previous_email = request.user.profile.email
    form = EmailProfileForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save(commit=False)
        if profile.email != previous_email or request.user.profile.email_verified_at is None:
            profile.email_verified_at = None
        profile.save()
        queue_email_verification(request.user)
        record_event("profile.email_changed", "Verification email queued", actor=request.user)
        messages.success(request, "Verification email queued. Check your inbox.")
        return redirect("profile-email")
    return render(request, "booking/profile_email.html", {"form": form})


def verify_email(request: HttpRequest, token: str) -> HttpResponse:
    try:
        payload = read_email_token(token)
        profile = UserProfile.objects.select_related("user").get(
            user__username=payload["username"],
            email=payload["email"],
        )
    except (signing.BadSignature, UserProfile.DoesNotExist):
        return render(request, "booking/verification_result.html", {"verified": False}, status=400)
    profile.email_verified_at = timezone.now()
    profile.save(update_fields=["email_verified_at", "updated_at"])
    record_event("profile.email_verified", "Email address verified", actor=profile.user)
    return render(request, "booking/verification_result.html", {"verified": True})


def _save_reservation_form(
    request: HttpRequest,
    *,
    instance: Reservation | None = None,
    maintenance: bool = False,
) -> tuple[ReservationForm, Reservation | None]:
    form = ReservationForm(
        request.POST or None,
        user=request.user,
        instance=instance,
        maintenance=maintenance,
    )
    saved = None
    if request.method == "POST" and form.is_valid():
        try:
            if instance:
                saved = update_reservation(
                    instance,
                    gpus=form.cleaned_data["gpus"],
                    starts_at=form.cleaned_data["starts_at"],
                    ends_at=form.cleaned_data["ends_at"],
                    description=str(form.cleaned_data["description"]),
                    actor=request.user,
                )
            else:
                saved = create_reservation(
                    owner=None if maintenance else request.user,
                    gpus=form.cleaned_data["gpus"],
                    starts_at=form.cleaned_data["starts_at"],
                    ends_at=form.cleaned_data["ends_at"],
                    description=str(form.cleaned_data["description"]),
                    kind=Reservation.Kind.MAINTENANCE if maintenance else Reservation.Kind.USER,
                    actor=request.user,
                )
        except (ReservationConflict, ValidationError, IntegrityError) as exc:
            form.add_error(None, exc)
    return form, saved


@login_required
def reservation_create(request: HttpRequest) -> HttpResponse:
    form, reservation = _save_reservation_form(request)
    if reservation:
        messages.success(request, "Reservation confirmed.")
        return redirect("my-reservations")
    return render(request, "booking/reservation_form.html", {"form": form, "title": "Book GPUs"})


@login_required
def reservation_edit(request: HttpRequest, reservation_id: int) -> HttpResponse:
    reservation = get_object_or_404(Reservation, pk=reservation_id, owner=request.user)
    configuration = SiteConfiguration.get_solo()
    if not configuration.allow_user_edits or not reservation.can_be_edited:
        raise PermissionDenied
    form, saved = _save_reservation_form(request, instance=reservation)
    if saved:
        messages.success(request, "Reservation updated.")
        return redirect("my-reservations")
    return render(
        request,
        "booking/reservation_form.html",
        {"form": form, "title": "Edit reservation", "reservation": reservation},
    )


@login_required
def my_reservations(request: HttpRequest) -> HttpResponse:
    configuration = SiteConfiguration.get_solo()
    now = timezone.now()
    reservations = list(request.user.reservations.prefetch_related("allocations__gpu").all())
    for reservation in reservations:
        reservation.user_can_edit = configuration.allow_user_edits and reservation.can_be_edited
        can_cancel_future = (
            configuration.allow_user_cancellations
            and reservation.status == Reservation.Status.CONFIRMED
            and reservation.starts_at
            >= now + timedelta(minutes=configuration.cancellation_cutoff_minutes)
        )
        reservation.user_can_release = (
            can_cancel_future or configuration.allow_early_release and reservation.can_end_early
        )
    return render(request, "booking/my_reservations.html", {"reservations": reservations})


@login_required
@require_POST
def reservation_cancel(request: HttpRequest, reservation_id: int) -> HttpResponse:
    reservation = get_object_or_404(Reservation, pk=reservation_id, owner=request.user)
    try:
        cancel_reservation(reservation, actor=request.user)
        messages.success(request, "Reservation release requested.")
    except ValidationError as exc:
        messages.error(request, exc.message)
    return redirect("my-reservations")


@login_required
@os_admin_required
def admin_overview(request: HttpRequest) -> HttpResponse:
    month_start, month_end = current_month_window()
    groups = list(ResearchGroup.objects.prefetch_related("members", "fixed_gpus").all())
    profiles = list(
        UserProfile.objects.select_related("user", "research_group").order_by("user__username")
    )
    group_stats = []
    for group in groups:
        usage = usage_statistics(
            starts_at=month_start,
            ends_at=month_end,
            research_group=group,
        )
        quota = group.monthly_shared_gpu_hours
        group_stats.append(
            {
                "group": group,
                "usage": usage,
                "remaining": max(Decimal(0), quota - usage["shared_gpu_hours"]) if quota else None,
            }
        )
    user_stats = [
        {
            "profile": profile,
            "usage": usage_statistics(
                starts_at=month_start,
                ends_at=month_end,
                profile=profile,
            ),
        }
        for profile in profiles
    ]
    return render(
        request,
        "booking/admin_overview.html",
        {
            "gpus": GPU.objects.select_related("owner_group").all(),
            "enabled_gpu_count": GPU.objects.filter(enabled=True, healthy=True).count(),
            "groups": group_stats,
            "users": user_stats,
            "usage_month": month_start,
            "reservations": Reservation.objects.select_related("owner")
            .prefetch_related("allocations__gpu")
            .order_by("-created_at")[:100],
            "events": AuditEvent.objects.select_related("actor")[:100],
        },
    )


@login_required
@os_admin_required
def admin_group_edit(request: HttpRequest, group_id: int | None = None) -> HttpResponse:
    group = get_object_or_404(ResearchGroup, pk=group_id) if group_id else None
    form = ResearchGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        record_event(
            "group.updated" if group else "group.created",
            "Research group updated" if group else "Research group created",
            actor=request.user,
            object_type="research_group",
            object_id=saved.pk,
        )
        messages.success(request, "Research group saved.")
        return redirect("admin-overview")
    return render(
        request,
        "booking/admin_group_form.html",
        {"form": form, "group": group},
    )


@login_required
@os_admin_required
def admin_gpu_settings(request: HttpRequest, gpu_id: int) -> HttpResponse:
    gpu = get_object_or_404(GPU, pk=gpu_id)
    form = GPUSettingsForm(request.POST or None, instance=gpu)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        record_event(
            "gpu.policy_updated",
            "GPU allocation policy updated",
            actor=request.user,
            object_type="gpu",
            object_id=saved.pk,
            metadata={
                "enabled": saved.enabled,
                "allocation_type": saved.allocation_type,
                "owner_group_id": saved.owner_group_id,
            },
        )
        messages.success(request, "GPU policy saved.")
        return redirect("admin-overview")
    return render(
        request,
        "booking/admin_gpu_settings.html",
        {"form": form, "gpu": gpu},
    )


@login_required
@os_admin_required
def admin_settings(request: HttpRequest) -> HttpResponse:
    configuration = SiteConfiguration.get_solo()
    form = SiteConfigurationForm(request.POST or None, instance=configuration)
    if request.method == "POST" and form.is_valid():
        changes = {
            field: {
                "from": form.initial.get(field),
                "to": form.cleaned_data[field],
            }
            for field in form.changed_data
        }
        form.save()
        record_event(
            "settings.updated",
            "Site settings updated",
            actor=request.user,
            object_type="site_configuration",
            object_id=configuration.pk,
            metadata={"changes": changes},
        )
        messages.success(request, "Settings saved.")
        return redirect("admin-settings")
    return render(request, "booking/admin_settings.html", {"form": form})


@login_required
@os_admin_required
def maintenance_create(request: HttpRequest) -> HttpResponse:
    form, reservation = _save_reservation_form(request, maintenance=True)
    if reservation:
        messages.success(request, "Maintenance block created.")
        return redirect("admin-overview")
    return render(
        request, "booking/reservation_form.html", {"form": form, "title": "Block maintenance"}
    )


@login_required
@os_admin_required
@require_POST
def admin_cancel_reservation(request: HttpRequest, reservation_id: int) -> HttpResponse:
    reservation = get_object_or_404(Reservation, pk=reservation_id)
    try:
        cancel_reservation(reservation, actor=request.user, admin=True)
        messages.success(request, "Reservation release requested.")
    except ValidationError as exc:
        messages.error(request, exc.message)
    return redirect("admin-overview")


@login_required
@os_admin_required
@require_POST
def admin_retry_reservation(request: HttpRequest, reservation_id: int) -> HttpResponse:
    reservation = get_object_or_404(Reservation, pk=reservation_id)
    try:
        retry_activation(reservation, actor=request.user)
        messages.success(request, "Activation retry queued.")
    except ValidationError as exc:
        messages.error(request, exc.message)
    return redirect("admin-overview")


@login_required
@os_admin_required
@require_POST
def admin_toggle_user(request: HttpRequest, profile_id: int) -> HttpResponse:
    profile = get_object_or_404(UserProfile, pk=profile_id)
    if profile.user_id == request.user.pk:
        messages.error(request, "You cannot suspend your own account.")
        return redirect("admin-overview")
    profile.suspended = not profile.suspended
    profile.save(update_fields=["suspended", "updated_at"])
    if profile.suspended:
        open_reservations = profile.user.reservations.filter(
            status__in=[
                Reservation.Status.CONFIRMED,
                Reservation.Status.ACTIVE,
                Reservation.Status.STOPPING,
                Reservation.Status.ACTIVATION_FAILED,
            ],
            ends_at__gt=timezone.now(),
        )
        for reservation in open_reservations:
            cancel_reservation(reservation, actor=request.user, admin=True)
    record_event(
        "user.suspension_changed",
        "User suspension changed",
        actor=request.user,
        object_type="user",
        object_id=profile.user_id,
        metadata={"suspended": profile.suspended},
    )
    return redirect("admin-overview")


@login_required
@os_admin_required
@require_POST
def admin_toggle_gpu(request: HttpRequest, gpu_id: int) -> HttpResponse:
    gpu = get_object_or_404(GPU, pk=gpu_id)
    if gpu.allocations.filter(blocking=True, ends_at__gt=timezone.now()).exists():
        messages.error(
            request,
            "A GPU with current or future reservations cannot be disabled. Release them first.",
        )
        return redirect("admin-overview")
    form = GPUSettingsForm(
        {
            "enabled": not gpu.enabled,
            "allocation_type": gpu.allocation_type,
            "owner_group": gpu.owner_group_id,
        },
        instance=gpu,
    )
    if form.is_valid():
        form.save()
        record_event(
            "gpu.enabled_changed",
            "GPU enabled state changed",
            actor=request.user,
            object_type="gpu",
            object_id=gpu.pk,
            metadata={"enabled": gpu.enabled},
        )
    return redirect("admin-overview")


def health(request: HttpRequest) -> HttpResponse:
    try:
        SiteConfiguration.get_solo()
    except Exception as exc:
        raise Http404 from exc
    return HttpResponse("ok", content_type="text/plain")
