from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest

from .models import SiteConfiguration
from .system_accounts import is_os_admin


def site_context(request: HttpRequest) -> dict[str, Any]:
    configuration = SiteConfiguration.get_solo()
    admin = False
    if request.user.is_authenticated:
        admin = is_os_admin(request.user.username)
        if settings.DEBUG and getattr(settings, "GPU_BOOKING_DEV_AUTH", False):
            admin = admin or request.user.is_superuser
    return {"site_configuration": configuration, "is_os_admin": admin}
