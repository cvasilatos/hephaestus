from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from booking.models import GPU, ResearchGroup, UserProfile


@pytest.fixture
def research_group(db) -> ResearchGroup:
    group, _ = ResearchGroup.objects.get_or_create(name="MoMA")
    return group


@pytest.fixture
def user(db, research_group) -> User:
    result = User.objects.create_user(username="alice", password="unused")
    UserProfile.objects.create(
        user=result,
        linux_uid=21001,
        email="alice@example.edu",
        email_verified_at=timezone.now(),
        research_group=research_group,
    )
    return result


@pytest.fixture
def second_user(db, research_group) -> User:
    result = User.objects.create_user(username="bob", password="unused")
    UserProfile.objects.create(
        user=result,
        linux_uid=21002,
        email="bob@example.edu",
        email_verified_at=timezone.now(),
        research_group=research_group,
    )
    return result


@pytest.fixture
def gpu(db) -> GPU:
    return GPU.objects.create(
        uuid="GPU-11111111-1111-1111-1111-111111111111",
        current_index=0,
        device_path="/dev/nvidia0",
        name="NVIDIA A100",
        memory_mb=81920,
        allocation_type=GPU.AllocationType.SHARED,
    )


@pytest.fixture
def gpu_two(db) -> GPU:
    return GPU.objects.create(
        uuid="GPU-22222222-2222-2222-2222-222222222222",
        current_index=1,
        device_path="/dev/nvidia1",
        name="NVIDIA A100",
        memory_mb=81920,
        allocation_type=GPU.AllocationType.SHARED,
    )


@pytest.fixture
def future_window() -> tuple[object, object]:
    now = timezone.now().replace(second=0, microsecond=0)
    minute_delta = (30 - now.minute % 30) % 30
    start = now + timedelta(minutes=minute_delta or 30)
    return start, start + timedelta(hours=1)
