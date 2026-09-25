from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import F, Func, Q, Value
from django.utils import timezone


class TstzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


def allocation_exclusion_constraint() -> ExclusionConstraint:
    return ExclusionConstraint(
        name="exclude_overlapping_gpu_allocations",
        expressions=(
            ("gpu", RangeOperators.EQUAL),
            (TstzRange("starts_at", "ends_at", Value("[)")), RangeOperators.OVERLAPS),
        ),
        condition=Q(blocking=True),
    )


class SiteConfiguration(models.Model):
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    site_timezone = models.CharField(
        "Site timezone",
        max_length=64,
        default=settings.TIME_ZONE,
        help_text="IANA name used to validate slot boundaries, for example Asia/Dubai.",
    )
    slot_minutes = models.PositiveSmallIntegerField(
        "Slot length (minutes)",
        default=30,
        help_text="Start and end times must align to this interval.",
    )
    minimum_reservation_minutes = models.PositiveIntegerField(
        "Minimum reservation (minutes)",
        default=30,
    )
    default_reservation_minutes = models.PositiveIntegerField(
        "Default reservation (minutes)",
        default=60,
        help_text="Initial duration shown in the booking form.",
    )
    max_reservation_minutes = models.PositiveIntegerField(
        "Maximum reservation (minutes)",
        default=7 * 24 * 60,
    )
    booking_notice_minutes = models.PositiveIntegerField(
        "Minimum advance notice (minutes)",
        default=0,
        help_text="Use 0 to allow a booking at the next slot boundary.",
    )
    booking_horizon_days = models.PositiveIntegerField(
        "Booking horizon (days)",
        default=0,
        help_text="How far ahead a reservation may start. Use 0 for no limit.",
    )
    max_gpus_per_reservation = models.PositiveSmallIntegerField(
        "Maximum GPUs per reservation",
        default=0,
        help_text="Use 0 for no limit.",
    )
    max_open_reservations_per_user = models.PositiveSmallIntegerField(
        "Maximum open reservations per user",
        default=0,
        help_text="Confirmed, active, stopping, and failed reservations count. Use 0 for no limit.",
    )
    require_verified_email = models.BooleanField(
        "Require verified email to book",
        default=True,
    )
    allow_user_edits = models.BooleanField(
        "Allow users to edit future reservations",
        default=True,
    )
    allow_user_cancellations = models.BooleanField(
        "Allow users to cancel future reservations",
        default=True,
    )
    allow_early_release = models.BooleanField(
        "Allow users to end active reservations early",
        default=True,
    )
    cancellation_cutoff_minutes = models.PositiveIntegerField(
        "Cancellation cutoff (minutes)",
        default=0,
        help_text=(
            "Prevent user cancellation this long before a reservation starts. Use 0 for none."
        ),
    )
    warning_minutes = models.PositiveSmallIntegerField(
        "Ending-soon warning (minutes)",
        default=15,
        help_text="Use 0 to disable ending-soon email notifications.",
    )
    termination_grace_minutes = models.PositiveSmallIntegerField(
        "SIGTERM grace period (minutes)",
        default=5,
        help_text="Use 0 to force-stop processes only when the reservation ends.",
    )
    calendar_days = models.PositiveSmallIntegerField(
        "Schedule horizon (days)",
        default=7,
        help_text="Number of days shown on the user schedule (1–31).",
    )
    show_usernames_on_schedule = models.BooleanField(
        "Show usernames on the shared schedule",
        default=True,
        help_text="When disabled, other users' reservations are labelled Reserved.",
    )
    require_group_membership = models.BooleanField(
        "Require a research group to book",
        default=True,
        help_text="Users without an enabled research group cannot create reservations.",
    )
    enforce_gpu_allocation_rules = models.BooleanField(
        "Enforce shared and fixed GPU assignments",
        default=True,
        help_text="Limit users to shared GPUs, their group's fixed GPUs, and approved borrowing.",
    )
    enforce_group_quotas = models.BooleanField(
        "Enforce monthly shared-pool quotas",
        default=True,
        help_text="Reject reservations that exceed a group's monthly shared GPU-hour allowance.",
    )
    require_fixed_gpu_permission = models.BooleanField(
        "Require permission to borrow fixed GPUs",
        default=True,
        help_text="A recorded grant is required to use a fixed GPU owned by another group.",
    )
    policy_name = models.CharField(
        "Policy title",
        max_length=160,
        default="Hydra A100 GPU Use Policy",
    )
    policy_intro = models.TextField(
        "Policy introduction",
        default=(
            "Hydra uses a hybrid allocation model. Shared GPUs are charged to each research "
            "group in GPU-hours, while fixed GPUs remain assigned to their owning group."
        ),
    )
    administrator_contact = models.CharField(
        "Administrator contact",
        max_length=254,
        default="cv43@nyu.edu",
    )
    cluster_hostname = models.CharField(
        "Cluster hostname",
        max_length=253,
        default="hydra.abudhabi.nyu.edu",
    )
    cluster_ssh_port = models.PositiveIntegerField("SSH port", default=4410)
    shared_gpu_instructions = models.TextField(
        "Shared GPU instructions",
        default=(
            "Reserve every shared GPU in this application before launch. Keep the expected end "
            "time, status, and monthly group balance current."
        ),
    )
    monitoring_instructions = models.TextField(
        "Monitoring and completion instructions",
        default=(
            "Run nvidia-smi before launch and throughout the run. Use CUDA_VISIBLE_DEVICES for "
            "every GPU job, never terminate another user's process, and release the reservation "
            "as soon as the job completes."
        ),
    )
    storage_instructions = models.TextField(
        "Storage and data instructions",
        default=(
            "Keep code and small working files in the assigned home or project area. Use /data "
            "for large datasets and outputs, respect quotas, remove obsolete files, and do not "
            "place confidential data in shared locations without approved access controls."
        ),
    )
    fair_use_instructions = models.TextField(
        "Fair-use and enforcement instructions",
        default=(
            "Administrators may restrict access after quota overuse, missing or inaccurate "
            "logging, unauthorized GPU use, or repeated policy violations. Report hardware "
            "errors promptly with the GPU ID, time, command, and relevant log excerpt."
        ),
    )
    policy_checklist = models.TextField(
        "User checklist",
        default=(
            "Correct fixed, shared, or approved borrowed GPU selected\n"
            "Reservation details and expected end time are accurate\n"
            "Monthly shared-pool balance checked\n"
            "nvidia-smi checked before launch\n"
            "CUDA_VISIBLE_DEVICES matches approved GPU IDs\n"
            "Permission recorded for any borrowed fixed GPU\n"
            "Reservation released when work ends"
        ),
        help_text="Enter one checklist item per line.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> SiteConfiguration:
        instance, _ = cls.objects.get_or_create(singleton=1)
        return instance

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.singleton = 1
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return "GPU booking configuration"


class ResearchGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    monthly_shared_gpu_hours = models.DecimalField(
        "Monthly shared GPU-hours",
        max_digits=10,
        decimal_places=2,
        default=Decimal("730.00"),
        help_text="Use 0 for no monthly limit.",
    )
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    linux_uid = models.PositiveIntegerField(unique=True)
    email = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    research_group = models.ForeignKey(
        ResearchGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    suspended = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def email_is_verified(self) -> bool:
        return bool(self.email and self.email_verified_at)

    def __str__(self) -> str:
        return self.user.username


class GPU(models.Model):
    class AllocationType(models.TextChoices):
        SHARED = "shared", "Shared pool"
        FIXED = "fixed", "Group fixed"
        UNMANAGED = "unmanaged", "Unassigned"

    uuid = models.CharField(max_length=96, unique=True)
    current_index = models.PositiveSmallIntegerField(null=True, blank=True)
    device_path = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=160)
    memory_mb = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    healthy = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    status_message = models.CharField(max_length=255, blank=True)
    allocation_type = models.CharField(
        max_length=16,
        choices=AllocationType.choices,
        default=AllocationType.UNMANAGED,
        db_index=True,
    )
    owner_group = models.ForeignKey(
        ResearchGroup,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fixed_gpus",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["current_index", "uuid"]

    @property
    def short_uuid(self) -> str:
        return self.uuid[:18]

    def __str__(self) -> str:
        index = "?" if self.current_index is None else self.current_index
        return f"GPU {index}: {self.name}"


class FixedGPUAccessGrant(models.Model):
    research_group = models.ForeignKey(
        ResearchGroup,
        on_delete=models.CASCADE,
        related_name="fixed_gpu_grants",
    )
    gpu = models.ForeignKey(
        GPU,
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["research_group__name", "gpu__current_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_group", "gpu"],
                name="one_fixed_gpu_grant_per_group",
            )
        ]

    def __str__(self) -> str:
        return f"{self.research_group} may use {self.gpu}"


class Reservation(models.Model):
    class Kind(models.TextChoices):
        USER = "user", "User booking"
        MAINTENANCE = "maintenance", "Maintenance"

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        ACTIVE = "active", "Active"
        STOPPING = "stopping", "Stopping"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        ACTIVATION_FAILED = "activation_failed", "Activation failed"

    owner = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    research_group = models.ForeignKey(
        ResearchGroup,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reservations",
        help_text="Group charged for this reservation at booking time.",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.USER)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    description = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.CONFIRMED,
        db_index=True,
    )
    warning_sent_at = models.DateTimeField(null=True, blank=True)
    term_sent_at = models.DateTimeField(null=True, blank=True)
    access_granted_at = models.DateTimeField(null=True, blank=True)
    access_released_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="reservation_end_after_start",
            )
        ]

    @property
    def is_future(self) -> bool:
        return self.starts_at > timezone.now()

    @property
    def can_be_edited(self) -> bool:
        return self.status == self.Status.CONFIRMED and self.is_future

    @property
    def can_end_early(self) -> bool:
        return self.status in {self.Status.ACTIVE, self.Status.STOPPING}

    @property
    def duration(self) -> timedelta:
        return self.ends_at - self.starts_at

    def __str__(self) -> str:
        owner = self.owner.username if self.owner else "maintenance"
        return f"{owner}: {self.starts_at.isoformat()} – {self.ends_at.isoformat()}"


class GPUAllocation(models.Model):
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    gpu = models.ForeignKey(GPU, on_delete=models.PROTECT, related_name="allocations")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    blocking = models.BooleanField(default=True)
    allocation_type = models.CharField(
        max_length=16,
        choices=GPU.AllocationType.choices,
        default=GPU.AllocationType.UNMANAGED,
        help_text="GPU classification captured when the reservation was saved.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "gpu"],
                name="one_allocation_per_reservation_gpu",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="allocation_end_after_start",
            ),
            allocation_exclusion_constraint(),
        ]

    def __str__(self) -> str:
        return f"{self.reservation_id} / {self.gpu.uuid}"


class AuditEvent(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    message = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class NotificationOutbox(models.Model):
    class Kind(models.TextChoices):
        VERIFY_EMAIL = "verify_email", "Verify email"
        ENDING_SOON = "ending_soon", "Reservation ending soon"
        ADMIN_ALERT = "admin_alert", "Administrator alert"

    kind = models.CharField(max_length=32, choices=Kind.choices)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    send_after = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["send_after", "id"]


class LoginThrottle(models.Model):
    key_hash = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
