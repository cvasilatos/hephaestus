from __future__ import annotations

from django.conf import settings
from django.core import signing

SALT = "gpu-booking-email-verification"


def make_email_token(username: str, email: str) -> str:
    return signing.dumps({"username": username, "email": email}, salt=SALT, compress=True)


def read_email_token(token: str) -> dict[str, str]:
    value = signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
    if not isinstance(value, dict) or not isinstance(value.get("username"), str):
        raise signing.BadSignature("Invalid verification payload")
    if not isinstance(value.get("email"), str):
        raise signing.BadSignature("Invalid verification payload")
    return value
