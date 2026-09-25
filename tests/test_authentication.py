from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from booking.auth_backend import PAMBackend
from booking.models import UserProfile
from booking.system_accounts import SystemIdentity
from gpu_access.authd import AuthConfig, authenticate_payload


@pytest.mark.django_db
def test_pam_backend_uses_broker_and_never_stores_password() -> None:
    identity = SystemIdentity("alice", 21001, 21001, "/bin/bash")
    with (
        patch("booking.auth_backend.get_interactive_identity", return_value=identity),
        patch("booking.auth_backend.PAMBrokerClient.authenticate", return_value=21001),
    ):
        user = PAMBackend().authenticate(object(), username="alice", password="secret")

    assert user is not None
    assert not user.has_usable_password()
    assert UserProfile.objects.get(user=user).linux_uid == 21001


def test_auth_broker_rejects_system_accounts(tmp_path) -> None:
    config = AuthConfig(
        socket_path=tmp_path / "auth.sock",
        web_uid=2000,
        web_gid=2000,
        pam_service="gpu-booking",
        minimum_uid=1000,
    )
    system_identity = SimpleNamespace(pw_uid=0, pw_shell="/bin/bash")
    with patch("gpu_access.authd.pwd.getpwnam", return_value=system_identity):
        result = authenticate_payload({"username": "root", "password": "secret"}, config)
    assert result == {"ok": False}


def test_auth_broker_returns_only_uid_after_pam_success(tmp_path) -> None:
    config = AuthConfig(
        socket_path=tmp_path / "auth.sock",
        web_uid=2000,
        web_gid=2000,
        pam_service="gpu-booking",
        minimum_uid=1000,
    )
    identity = SimpleNamespace(pw_uid=21001, pw_shell="/bin/bash")
    with (
        patch("gpu_access.authd.pwd.getpwnam", return_value=identity),
        patch("gpu_access.authd.pamela.authenticate", return_value=None),
    ):
        result = authenticate_payload({"username": "alice", "password": "secret"}, config)
    assert result == {"ok": True, "uid": 21001}
