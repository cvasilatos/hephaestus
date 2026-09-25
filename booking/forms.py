from __future__ import annotations

from datetime import timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (
    GPU,
    FixedGPUAccessGrant,
    ResearchGroup,
    Reservation,
    SiteConfiguration,
    UserProfile,
)
from .services import available_gpus_for_user, validate_reservation_policy


class PAMLoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class EmailProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["email"]
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email"})}


class ResearchGroupForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        queryset=UserProfile.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    borrowed_gpus = forms.ModelMultipleChoiceField(
        queryset=GPU.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Fixed GPUs this group has permission to borrow.",
    )
    fixed_gpus = forms.ModelMultipleChoiceField(
        queryset=GPU.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Discovered GPUs owned by this group. Selecting one makes it a fixed GPU.",
    )

    class Meta:
        model = ResearchGroup
        fields = ["name", "monthly_shared_gpu_hours", "enabled", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = UserProfile.objects.select_related("user").order_by(
            "user__username"
        )
        self.fields["fixed_gpus"].queryset = GPU.objects.all()
        borrowed = GPU.objects.filter(allocation_type=GPU.AllocationType.FIXED)
        if self.instance.pk:
            borrowed = borrowed.exclude(owner_group=self.instance)
            self.initial["members"] = self.instance.members.values_list("pk", flat=True)
            self.initial["fixed_gpus"] = self.instance.fixed_gpus.values_list("pk", flat=True)
            self.initial["borrowed_gpus"] = self.instance.fixed_gpu_grants.values_list(
                "gpu_id", flat=True
            )
        self.fields["borrowed_gpus"].queryset = borrowed

    def save(self, commit: bool = True) -> ResearchGroup:
        group = cast(ResearchGroup, super().save(commit=commit))
        if not commit:
            return group
        members = cast(list[UserProfile], list(self.cleaned_data["members"]))
        member_ids = [profile.pk for profile in members]
        group.members.exclude(pk__in=member_ids).update(research_group=None)
        UserProfile.objects.filter(pk__in=member_ids).update(research_group=group)
        Reservation.objects.filter(
            owner__profile__pk__in=member_ids,
            research_group__isnull=True,
        ).update(research_group=group)

        fixed_gpus = cast(list[GPU], list(self.cleaned_data["fixed_gpus"]))
        fixed_ids = [gpu.pk for gpu in fixed_gpus]
        released = list(group.fixed_gpus.exclude(pk__in=fixed_ids))
        FixedGPUAccessGrant.objects.filter(gpu__in=released).delete()
        GPU.objects.filter(pk__in=[gpu.pk for gpu in released]).update(
            allocation_type=GPU.AllocationType.UNMANAGED,
            owner_group=None,
        )
        GPU.objects.filter(pk__in=fixed_ids).update(
            allocation_type=GPU.AllocationType.FIXED,
            owner_group=group,
        )
        FixedGPUAccessGrant.objects.filter(research_group=group, gpu_id__in=fixed_ids).delete()

        borrowed_gpus = cast(list[GPU], list(self.cleaned_data["borrowed_gpus"]))
        borrowed_ids = [gpu.pk for gpu in borrowed_gpus]
        group.fixed_gpu_grants.exclude(gpu_id__in=borrowed_ids).delete()
        for gpu in borrowed_gpus:
            FixedGPUAccessGrant.objects.get_or_create(research_group=group, gpu=gpu)
        return group


class ReservationForm(forms.Form):
    gpus = forms.ModelMultipleChoiceField(
        queryset=GPU.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    starts_at = forms.DateTimeField(widget=forms.HiddenInput)
    ends_at = forms.DateTimeField(widget=forms.HiddenInput)
    description = forms.CharField(max_length=200, required=False)

    def __init__(
        self,
        *args: object,
        user: User | None = None,
        instance: Reservation | None = None,
        maintenance: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance
        self.maintenance = maintenance
        self.fields["gpus"].queryset = available_gpus_for_user(user, maintenance=maintenance)
        configuration = SiteConfiguration.get_solo()
        self.fields["gpus"].help_text = (
            f"Select up to {configuration.max_gpus_per_reservation}."
            if configuration.max_gpus_per_reservation and not maintenance
            else "Select one or more GPUs."
        )
        if instance and not self.is_bound:
            self.initial.update(
                {
                    "gpus": instance.allocations.values_list("gpu_id", flat=True),
                    "starts_at": instance.starts_at.isoformat(),
                    "ends_at": instance.ends_at.isoformat(),
                    "description": instance.description,
                }
            )

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        configuration = SiteConfiguration.get_solo()
        if (
            self.user
            and not self.maintenance
            and configuration.require_verified_email
            and not self.user.profile.email_is_verified
        ):
            raise ValidationError("Verify your email address before making a reservation.")
        if (
            self.user
            and not self.maintenance
            and configuration.require_group_membership
            and self.user.profile.research_group is None
        ):
            raise ValidationError("Ask an administrator to assign you to a research group first.")
        gpus = cleaned.get("gpus")
        if (
            gpus is not None
            and not self.maintenance
            and configuration.max_gpus_per_reservation
            and len(gpus) > configuration.max_gpus_per_reservation
        ):
            gpu_label = "GPU" if configuration.max_gpus_per_reservation == 1 else "GPUs"
            raise ValidationError(
                "Select no more than "
                f"{configuration.max_gpus_per_reservation} {gpu_label} per reservation."
            )
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        if not starts_at or not ends_at:
            return cleaned
        if starts_at >= ends_at:
            raise ValidationError("The end must be later than the start.")
        now = timezone.now()
        if not self.instance and starts_at < now:
            raise ValidationError("A reservation cannot start in the past.")
        duration = ends_at - starts_at
        if duration < timedelta(minutes=configuration.minimum_reservation_minutes):
            raise ValidationError(
                f"The minimum reservation is {configuration.minimum_reservation_minutes} minutes."
            )
        if duration > timedelta(minutes=configuration.max_reservation_minutes):
            raise ValidationError("The reservation exceeds the configured maximum duration.")
        if not self.maintenance:
            earliest_start = now + timedelta(minutes=configuration.booking_notice_minutes)
            if starts_at < earliest_start:
                raise ValidationError(
                    "The reservation does not meet the configured minimum advance notice."
                )
            if configuration.booking_horizon_days and starts_at > now + timedelta(
                days=configuration.booking_horizon_days
            ):
                raise ValidationError("The reservation starts beyond the booking horizon.")
            if self.user and configuration.max_open_reservations_per_user:
                open_reservations = self.user.reservations.filter(
                    Q(ends_at__gt=now),
                    status__in=[
                        Reservation.Status.CONFIRMED,
                        Reservation.Status.ACTIVE,
                        Reservation.Status.STOPPING,
                        Reservation.Status.ACTIVATION_FAILED,
                    ],
                )
                if self.instance:
                    open_reservations = open_reservations.exclude(pk=self.instance.pk)
                if open_reservations.count() >= configuration.max_open_reservations_per_user:
                    raise ValidationError(
                        "You have reached the configured limit for open reservations."
                    )
        try:
            site_zone = ZoneInfo(configuration.site_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("The configured site timezone is invalid.") from exc
        for label, value in (("Start", starts_at), ("End", ends_at)):
            local = value.astimezone(site_zone)
            minute_of_day = local.hour * 60 + local.minute
            if local.second or local.microsecond or minute_of_day % configuration.slot_minutes:
                raise ValidationError(
                    f"{label} must align to a {configuration.slot_minutes}-minute boundary."
                )
        if self.user and not self.maintenance and gpus is not None:
            validate_reservation_policy(
                self.user,
                gpus,
                starts_at,
                ends_at,
                exclude_reservation_id=self.instance.pk if self.instance else None,
            )
        return cleaned


class SiteConfigurationForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = [
            "site_timezone",
            "slot_minutes",
            "minimum_reservation_minutes",
            "default_reservation_minutes",
            "max_reservation_minutes",
            "booking_notice_minutes",
            "booking_horizon_days",
            "max_gpus_per_reservation",
            "max_open_reservations_per_user",
            "require_verified_email",
            "allow_user_edits",
            "allow_user_cancellations",
            "allow_early_release",
            "cancellation_cutoff_minutes",
            "warning_minutes",
            "termination_grace_minutes",
            "calendar_days",
            "show_usernames_on_schedule",
            "require_group_membership",
            "enforce_gpu_allocation_rules",
            "enforce_group_quotas",
            "require_fixed_gpu_permission",
            "policy_name",
            "policy_intro",
            "administrator_contact",
            "cluster_hostname",
            "cluster_ssh_port",
            "shared_gpu_instructions",
            "monitoring_instructions",
            "storage_instructions",
            "fair_use_instructions",
            "policy_checklist",
        ]
        widgets = {
            "policy_intro": forms.Textarea(attrs={"rows": 3}),
            "shared_gpu_instructions": forms.Textarea(attrs={"rows": 3}),
            "monitoring_instructions": forms.Textarea(attrs={"rows": 3}),
            "storage_instructions": forms.Textarea(attrs={"rows": 3}),
            "fair_use_instructions": forms.Textarea(attrs={"rows": 3}),
            "policy_checklist": forms.Textarea(attrs={"rows": 7}),
        }

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        warning = cleaned.get("warning_minutes")
        grace = cleaned.get("termination_grace_minutes")
        slot = cleaned.get("slot_minutes")
        minimum = cleaned.get("minimum_reservation_minutes")
        default = cleaned.get("default_reservation_minutes")
        maximum = cleaned.get("max_reservation_minutes")
        calendar_days = cleaned.get("calendar_days")
        if not slot or slot > 24 * 60:
            raise ValidationError("The slot length must be between 1 and 1,440 minutes.")
        if not minimum or not default or not maximum:
            raise ValidationError("Reservation durations must be greater than zero.")
        if warning and grace is not None and warning <= grace:
            raise ValidationError("The warning must occur before the termination grace begins.")
        if slot and slot <= 24 * 60:
            durations = {
                "minimum duration": minimum,
                "default duration": default,
                "maximum duration": maximum,
            }
            for label, value in durations.items():
                if value and value % slot:
                    raise ValidationError(f"The {label} must be a multiple of the slot length.")
        if minimum and maximum and minimum > maximum:
            raise ValidationError("The minimum duration cannot exceed the maximum duration.")
        if minimum and default and default < minimum:
            raise ValidationError("The default duration cannot be shorter than the minimum.")
        if default and maximum and default > maximum:
            raise ValidationError("The default duration cannot exceed the maximum.")
        if calendar_days is not None and not 1 <= calendar_days <= 31:
            raise ValidationError("The schedule horizon must be between 1 and 31 days.")
        zone = cleaned.get("site_timezone")
        if zone:
            try:
                ZoneInfo(str(zone))
            except ZoneInfoNotFoundError as exc:
                raise ValidationError("Enter a valid IANA timezone name.") from exc
        return cleaned


class GPUSettingsForm(forms.ModelForm):
    class Meta:
        model = GPU
        fields = ["enabled", "allocation_type", "owner_group"]

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        allocation_type = cleaned.get("allocation_type")
        owner_group = cleaned.get("owner_group")
        if allocation_type == GPU.AllocationType.FIXED and owner_group is None:
            raise ValidationError("A fixed GPU must have an owning research group.")
        if allocation_type != GPU.AllocationType.FIXED and owner_group is not None:
            raise ValidationError("Only fixed GPUs can have an owning research group.")
        return cleaned
