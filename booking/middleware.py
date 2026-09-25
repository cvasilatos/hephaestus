from __future__ import annotations

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse

from .system_accounts import get_interactive_identity


class AccountEligibilityMiddleware:
    def __init__(self, get_response: object) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated and not (
            settings.DEBUG and getattr(settings, "GPU_BOOKING_DEV_AUTH", False)
        ):
            identity = get_interactive_identity(request.user.username)
            profile = getattr(request.user, "profile", None)
            if identity is None or profile is None:
                account_invalid = True
            else:
                account_invalid = profile.linux_uid != identity.uid or profile.suspended
            if account_invalid:
                logout(request)
        return self.get_response(request)  # type: ignore[operator]
