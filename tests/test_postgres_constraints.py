from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, transaction

from booking.models import GPU, GPUAllocation, Reservation
from booking.services import ReservationConflict, create_reservation


@pytest.mark.postgresql
@pytest.mark.django_db(transaction=True)
def test_concurrent_overlapping_requests_have_one_winner(
    user, second_user, gpu, future_window
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL exclusion constraint test")
    starts_at, ends_at = future_window
    barrier = Barrier(2)

    def attempt(user_id: int) -> str:
        close_old_connections()
        owner = User.objects.get(pk=user_id)
        selected_gpu = GPU.objects.get(pk=gpu.pk)
        barrier.wait(timeout=5)
        try:
            create_reservation(
                owner=owner,
                gpus=[selected_gpu],
                starts_at=starts_at,
                ends_at=ends_at + timedelta(minutes=30),
                description="race",
                actor=owner,
            )
        except ReservationConflict:
            return "conflict"
        finally:
            close_old_connections()
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, [user.pk, second_user.pk]))
    assert sorted(outcomes) == ["conflict", "created"]


@pytest.mark.postgresql
@pytest.mark.django_db(transaction=True)
def test_postgres_constraint_rejects_direct_overlap(user, second_user, gpu, future_window) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL exclusion constraint test")
    starts_at, ends_at = future_window
    first = Reservation.objects.create(owner=user, starts_at=starts_at, ends_at=ends_at)
    GPUAllocation.objects.create(
        reservation=first,
        gpu=gpu,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    second = Reservation.objects.create(owner=second_user, starts_at=starts_at, ends_at=ends_at)
    with pytest.raises(IntegrityError), transaction.atomic():
        GPUAllocation.objects.create(
            reservation=second,
            gpu=gpu,
            starts_at=starts_at + timedelta(minutes=30),
            ends_at=ends_at + timedelta(minutes=30),
        )
