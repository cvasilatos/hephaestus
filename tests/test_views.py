from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from booking.models import GPU, AuditEvent, ResearchGroup, SiteConfiguration

DEV_AUTH = ["booking.auth_backend.DevelopmentBackend", "booking.auth_backend.PAMBackend"]


@pytest.mark.django_db
@override_settings(DEBUG=True, GPU_BOOKING_DEV_AUTH=True, AUTHENTICATION_BACKENDS=DEV_AUTH)
def test_dashboard_renders_gpu_state(client, user, gpu) -> None:
    client.force_login(user, backend="booking.auth_backend.DevelopmentBackend")
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert b"NVIDIA A100" in response.content
    assert b"Reservation calendar" in response.content


@pytest.mark.django_db
@override_settings(DEBUG=True, GPU_BOOKING_DEV_AUTH=True, AUTHENTICATION_BACKENDS=DEV_AUTH)
def test_booking_page_exposes_multi_gpu_form(client, user, gpu, gpu_two) -> None:
    client.force_login(user, backend="booking.auth_backend.DevelopmentBackend")
    response = client.get(reverse("reservation-create"))
    assert response.status_code == 200
    assert b"Atomic allocation" in response.content
    assert response.content.count(b'type="checkbox"') == 2


@pytest.mark.django_db
@override_settings(DEBUG=True, GPU_BOOKING_DEV_AUTH=True, AUTHENTICATION_BACKENDS=DEV_AUTH)
def test_non_admin_cannot_open_control_room(client, user) -> None:
    client.force_login(user, backend="booking.auth_backend.DevelopmentBackend")
    response = client.get(reverse("admin-overview"))
    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(DEBUG=True, GPU_BOOKING_DEV_AUTH=True, AUTHENTICATION_BACKENDS=DEV_AUTH)
def test_admin_can_update_complete_policy(client, user) -> None:
    user.is_superuser = True
    user.save()
    client.force_login(user, backend="booking.auth_backend.DevelopmentBackend")

    response = client.get(reverse("admin-settings"))
    assert response.status_code == 200
    assert b"Maximum GPUs per reservation" in response.content
    assert b"Show usernames on the shared schedule" in response.content

    response = client.post(
        reverse("admin-settings"),
        {
            "site_timezone": "Asia/Dubai",
            "slot_minutes": 30,
            "minimum_reservation_minutes": 30,
            "default_reservation_minutes": 60,
            "max_reservation_minutes": 240,
            "booking_notice_minutes": 60,
            "booking_horizon_days": 14,
            "max_gpus_per_reservation": 2,
            "max_open_reservations_per_user": 3,
            "require_verified_email": "on",
            "allow_user_edits": "on",
            "allow_user_cancellations": "on",
            "allow_early_release": "on",
            "cancellation_cutoff_minutes": 120,
            "warning_minutes": 20,
            "termination_grace_minutes": 5,
            "calendar_days": 14,
            "require_group_membership": "on",
            "enforce_gpu_allocation_rules": "on",
            "enforce_group_quotas": "on",
            "require_fixed_gpu_permission": "on",
            "policy_name": "Hydra policy",
            "policy_intro": "Shared and fixed allocation.",
            "administrator_contact": "admin@example.edu",
            "cluster_hostname": "hydra.example.edu",
            "cluster_ssh_port": 4410,
            "shared_gpu_instructions": "Reserve shared GPUs.",
            "monitoring_instructions": "Monitor jobs.",
            "storage_instructions": "Use shared storage responsibly.",
            "fair_use_instructions": "Use resources fairly.",
            "policy_checklist": "Check GPU\nRelease GPU",
        },
    )
    assert response.status_code == 302
    configuration = SiteConfiguration.get_solo()
    assert configuration.booking_notice_minutes == 60
    assert configuration.max_gpus_per_reservation == 2
    assert configuration.calendar_days == 14
    assert not configuration.show_usernames_on_schedule
    event = AuditEvent.objects.get(event_type="settings.updated")
    assert event.metadata["changes"]["booking_notice_minutes"] == {"from": 0, "to": 60}


@pytest.mark.django_db
@override_settings(DEBUG=True, GPU_BOOKING_DEV_AUTH=True, AUTHENTICATION_BACKENDS=DEV_AUTH)
def test_policy_and_group_admin_pages_render(client, user, gpu) -> None:
    user.is_superuser = True
    user.save()
    client.force_login(user, backend="booking.auth_backend.DevelopmentBackend")

    policy_response = client.get(reverse("policy"))
    assert policy_response.status_code == 200
    assert b"Hydra A100 GPU Use Policy" in policy_response.content
    assert b"CUDA_VISIBLE_DEVICES" in policy_response.content

    group_response = client.get(reverse("admin-group-edit", args=[user.profile.research_group_id]))
    assert group_response.status_code == 200
    assert b"Borrowing permissions" in group_response.content

    gpu.allocation_type = GPU.AllocationType.FIXED
    gpu.owner_group = ResearchGroup.objects.get(pk=user.profile.research_group_id)
    gpu.save()
    gpu_response = client.get(reverse("admin-gpu-settings", args=[gpu.pk]))
    assert gpu_response.status_code == 200
    assert b"Save GPU policy" in gpu_response.content
