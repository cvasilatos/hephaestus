from __future__ import annotations

import pytest
from django.test import override_settings

from booking.throttling import clear_failures, is_locked, register_failure


@pytest.mark.django_db
@override_settings(LOGIN_THROTTLE_ATTEMPTS=2)
def test_login_failures_lock_and_success_clears() -> None:
    register_failure("alice", "127.0.0.1")
    assert not is_locked("alice", "127.0.0.1")
    register_failure("alice", "127.0.0.1")
    assert is_locked("alice", "127.0.0.1")
    clear_failures("alice", "127.0.0.1")
    assert not is_locked("alice", "127.0.0.1")
