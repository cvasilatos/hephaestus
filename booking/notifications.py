from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import NotificationOutbox, Reservation, SiteConfiguration
from .tokens import make_email_token


def queue_email_verification(user: User) -> NotificationOutbox:
    profile = user.profile
    token = make_email_token(user.username, profile.email)
    link = f"{settings.SITE_URL}{reverse('verify-email', kwargs={'token': token})}"
    return NotificationOutbox.objects.create(
        kind=NotificationOutbox.Kind.VERIFY_EMAIL,
        recipient=profile.email,
        subject="Verify your Hephaestus email",
        body=(
            f"Hello {user.username},\n\n"
            "Verify this address before creating GPU reservations:\n"
            f"{link}\n\n"
            "The link expires in 24 hours. If you did not request it, ignore this message.\n"
        ),
    )


@transaction.atomic
def queue_due_reservation_warnings(now: datetime | None = None) -> int:
    current = now or timezone.now()
    configuration = SiteConfiguration.get_solo()
    if not configuration.warning_minutes:
        return 0
    threshold = current + timedelta(minutes=configuration.warning_minutes)
    reservations = list(
        Reservation.objects.select_for_update(skip_locked=True)
        .select_related("owner__profile")
        .prefetch_related("allocations__gpu")
        .filter(
            kind=Reservation.Kind.USER,
            status__in=[Reservation.Status.CONFIRMED, Reservation.Status.ACTIVE],
            warning_sent_at__isnull=True,
            ends_at__gt=current,
            ends_at__lte=threshold,
        )
    )
    queued = 0
    for reservation in reservations:
        if not reservation.owner or not reservation.owner.profile.email_is_verified:
            continue
        gpu_names = ", ".join(str(item.gpu) for item in reservation.allocations.all())
        NotificationOutbox.objects.create(
            kind=NotificationOutbox.Kind.ENDING_SOON,
            recipient=reservation.owner.profile.email,
            subject="Your GPU reservation is ending soon",
            body=(
                f"Hello {reservation.owner.username},\n\n"
                f"Your reservation for {gpu_names} ends at {reservation.ends_at.isoformat()}.\n"
                f"Processes receive SIGTERM {configuration.termination_grace_minutes} minutes "
                "before the end and are force-stopped at the end. Save your work now.\n"
            ),
        )
        reservation.warning_sent_at = current
        reservation.save(update_fields=["warning_sent_at", "updated_at"])
        queued += 1
    return queued
