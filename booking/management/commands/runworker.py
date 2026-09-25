from __future__ import annotations

import signal
import time
from datetime import timedelta
from typing import Any

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from booking.models import NotificationOutbox
from booking.notifications import queue_due_reservation_warnings


class Command(BaseCommand):
    help = "Send queued mail and enqueue reservation warnings"

    def add_arguments(self, parser: object) -> None:
        parser.add_argument("--once", action="store_true")  # type: ignore[attr-defined]
        parser.add_argument("--interval", type=float, default=15.0)  # type: ignore[attr-defined]

    def handle(self, *args: object, **options: Any) -> None:
        stopping = False

        def stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            queue_due_reservation_warnings()
            sent = self._send_one()
            if options["once"]:
                break
            if not sent:
                time.sleep(float(options["interval"]))

    def _send_one(self) -> bool:
        with transaction.atomic():
            notification = (
                NotificationOutbox.objects.select_for_update(skip_locked=True)
                .filter(sent_at__isnull=True, send_after__lte=timezone.now(), attempts__lt=8)
                .first()
            )
            if not notification:
                return False
            notification.attempts += 1
            notification.save(update_fields=["attempts"])
        try:
            send_mail(
                notification.subject,
                notification.body,
                None,
                [notification.recipient],
                fail_silently=False,
            )
        except Exception as exc:
            notification.last_error = f"{type(exc).__name__}: {exc}"[:500]
            delay = min(60, 2**notification.attempts)
            notification.send_after = timezone.now() + timedelta(minutes=delay)
            notification.save(update_fields=["last_error", "send_after"])
            self.stderr.write(f"Email delivery failed: {notification.last_error}")
        else:
            notification.sent_at = timezone.now()
            notification.last_error = ""
            notification.save(update_fields=["sent_at", "last_error"])
        return True
