from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from .system_accounts import is_os_admin


def os_admin_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        allowed = request.user.is_authenticated and is_os_admin(request.user.username)
        if settings.DEBUG and getattr(settings, "GPU_BOOKING_DEV_AUTH", False):
            allowed = allowed or bool(request.user.is_authenticated and request.user.is_superuser)
        if not allowed:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped
