from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from booking.models import GPU, ResearchGroup, UserProfile


class Command(BaseCommand):
    help = "Create DEBUG-only sample users and simulated GPUs"

    def handle(self, *args: object, **options: object) -> None:
        if not settings.DEBUG or not settings.GPU_BOOKING_DEV_AUTH:
            raise CommandError("Set DJANGO_DEBUG=true and GPU_BOOKING_DEV_AUTH=true for demo data.")
        group, _ = ResearchGroup.objects.get_or_create(name="MoMA")
        users = [("demo", "demo-booking", False, 20001), ("gpuadmin", "admin-booking", True, 20002)]
        for username, password, is_superuser, uid in users:
            user, _ = User.objects.get_or_create(username=username)
            user.is_staff = is_superuser
            user.is_superuser = is_superuser
            user.set_password(password)
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"linux_uid": uid, "research_group": group},
            )
            self.stdout.write(f"{username} / {password}")
        for index, name, memory in (
            (0, "NVIDIA A100-SXM4-80GB", 81920),
            (1, "NVIDIA A100-SXM4-80GB", 81920),
        ):
            GPU.objects.update_or_create(
                uuid=f"GPU-DEMO-0000-0000-0000-{index:012d}",
                defaults={
                    "current_index": index,
                    "device_path": f"/dev/nvidia{index}",
                    "name": name,
                    "memory_mb": memory,
                    "enabled": True,
                    "healthy": True,
                    "allocation_type": GPU.AllocationType.SHARED,
                },
            )
        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
