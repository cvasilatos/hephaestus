from __future__ import annotations

import json
import logging
import socket
import struct

from django.conf import settings
from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.contrib.auth.models import User
from django.db import transaction

from .models import UserProfile
from .system_accounts import get_interactive_identity

logger = logging.getLogger(__name__)


class PAMBrokerClient:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def authenticate(self, username: str, password: str) -> int | None:
        payload = json.dumps({"username": username, "password": password}).encode()
        if len(payload) > 4096:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.socket_path)
                connection.sendall(struct.pack("!I", len(payload)) + payload)
                header = self._receive(connection, 4)
                size = struct.unpack("!I", header)[0]
                if size > 4096:
                    return None
                response = json.loads(self._receive(connection, size))
        except (OSError, ValueError, json.JSONDecodeError, struct.error):
            logger.exception("PAM broker request failed")
            return None
        uid = response.get("uid") if response.get("ok") else None
        return uid if isinstance(uid, int) else None

    @staticmethod
    def _receive(connection: socket.socket, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = connection.recv(size - len(result))
            if not chunk:
                raise OSError("PAM broker closed the connection")
            result.extend(chunk)
        return bytes(result)


class PAMBackend(BaseBackend):
    def authenticate(
        self,
        request: object,
        username: str | None = None,
        password: str | None = None,
        **kwargs: object,
    ) -> User | None:
        if not username or password is None:
            return None
        identity = get_interactive_identity(username)
        if identity is None:
            return None
        authenticated_uid = PAMBrokerClient(settings.GPU_AUTH_SOCKET).authenticate(
            username, password
        )
        if authenticated_uid != identity.uid:
            logger.info("PAM authentication failed for %s", username)
            return None

        with transaction.atomic():
            user, _ = User.objects.get_or_create(username=identity.username)
            user.set_unusable_password()
            user.save(update_fields=["password"])
            profile, created = UserProfile.objects.get_or_create(
                user=user,
                defaults={"linux_uid": identity.uid},
            )
            if not created and profile.linux_uid != identity.uid:
                logger.error("UID mismatch for %s", username)
                return None
            if profile.suspended:
                return None
        return user

    def get_user(self, user_id: int) -> User | None:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


class DevelopmentBackend(ModelBackend):
    """Password backend available only behind the explicit DEBUG-only setting guard."""
