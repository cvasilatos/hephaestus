from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


HYDRA_GROUPS = ("eBRAIN", "Haven", "DFX", "MoMA")
FIXED_GPU_GROUPS = {4: "eBRAIN", 5: "Haven", 6: "DFX", 7: "MoMA"}


def seed_hydra_policy(apps, schema_editor):
    research_group = apps.get_model("booking", "ResearchGroup")
    gpu_model = apps.get_model("booking", "GPU")
    groups = {}
    for name in HYDRA_GROUPS:
        group, _ = research_group.objects.get_or_create(
            name=name,
            defaults={"monthly_shared_gpu_hours": Decimal("730.00")},
        )
        groups[name] = group
    for gpu in gpu_model.objects.all():
        if gpu.current_index is not None and 0 <= gpu.current_index <= 3:
            gpu.allocation_type = "shared"
            gpu.owner_group = None
        elif gpu.current_index in FIXED_GPU_GROUPS:
            gpu.allocation_type = "fixed"
            gpu.owner_group = groups[FIXED_GPU_GROUPS[gpu.current_index]]
        else:
            gpu.allocation_type = "unmanaged"
            gpu.owner_group = None
        gpu.save(update_fields=["allocation_type", "owner_group"])


def remove_hydra_policy(apps, schema_editor):
    research_group = apps.get_model("booking", "ResearchGroup")
    gpu_model = apps.get_model("booking", "GPU")
    gpu_model.objects.update(allocation_type="unmanaged", owner_group=None)
    research_group.objects.filter(name__in=HYDRA_GROUPS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0002_siteconfiguration_allow_early_release_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=120, unique=True)),
                (
                    "monthly_shared_gpu_hours",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("730.00"),
                        help_text="Use 0 for no monthly limit.",
                        max_digits=10,
                        verbose_name="Monthly shared GPU-hours",
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="gpu",
            name="allocation_type",
            field=models.CharField(
                choices=[
                    ("shared", "Shared pool"),
                    ("fixed", "Group fixed"),
                    ("unmanaged", "Unassigned"),
                ],
                db_index=True,
                default="unmanaged",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="administrator_contact",
            field=models.CharField(
                default="cv43@nyu.edu",
                max_length=254,
                verbose_name="Administrator contact",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="cluster_hostname",
            field=models.CharField(
                default="hydra.abudhabi.nyu.edu",
                max_length=253,
                verbose_name="Cluster hostname",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="cluster_ssh_port",
            field=models.PositiveIntegerField(default=4410, verbose_name="SSH port"),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="enforce_gpu_allocation_rules",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Limit users to shared GPUs, their group's fixed GPUs, and approved borrowing."
                ),
                verbose_name="Enforce shared and fixed GPU assignments",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="enforce_group_quotas",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Reject reservations that exceed a group's monthly shared GPU-hour allowance."
                ),
                verbose_name="Enforce monthly shared-pool quotas",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="fair_use_instructions",
            field=models.TextField(
                default=(
                    "Administrators may restrict access after quota overuse, missing or inaccurate "
                    "logging, unauthorized GPU use, or repeated policy violations. Report hardware "
                    "errors promptly with the GPU ID, time, command, and relevant log excerpt."
                ),
                verbose_name="Fair-use and enforcement instructions",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="monitoring_instructions",
            field=models.TextField(
                default=(
                    "Run nvidia-smi before launch and throughout the run. Use "
                    "CUDA_VISIBLE_DEVICES for every GPU job, never terminate another user's "
                    "process, and release the reservation as soon as the job completes."
                ),
                verbose_name="Monitoring and completion instructions",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="policy_checklist",
            field=models.TextField(
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
                verbose_name="User checklist",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="policy_intro",
            field=models.TextField(
                default=(
                    "Hydra uses a hybrid allocation model. Shared GPUs are charged to each "
                    "research group in GPU-hours, while fixed GPUs remain assigned to their "
                    "owning group."
                ),
                verbose_name="Policy introduction",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="policy_name",
            field=models.CharField(
                default="Hydra A100 GPU Use Policy",
                max_length=160,
                verbose_name="Policy title",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="require_fixed_gpu_permission",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "A recorded grant is required to use a fixed GPU owned by another group."
                ),
                verbose_name="Require permission to borrow fixed GPUs",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="require_group_membership",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Users without an enabled research group cannot create reservations."
                ),
                verbose_name="Require a research group to book",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="shared_gpu_instructions",
            field=models.TextField(
                default=(
                    "Reserve every shared GPU in this application before launch. Keep the "
                    "expected end time, status, and monthly group balance current."
                ),
                verbose_name="Shared GPU instructions",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="storage_instructions",
            field=models.TextField(
                default=(
                    "Keep code and small working files in the assigned home or project area. Use "
                    "/data for large datasets and outputs, respect quotas, remove obsolete files, "
                    "and do not place confidential data in shared locations without approved "
                    "access controls."
                ),
                verbose_name="Storage and data instructions",
            ),
        ),
        migrations.AddField(
            model_name="gpu",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="fixed_gpus",
                to="booking.researchgroup",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="research_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="members",
                to="booking.researchgroup",
            ),
        ),
        migrations.CreateModel(
            name="FixedGPUAccessGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gpu",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_grants",
                        to="booking.gpu",
                    ),
                ),
                (
                    "research_group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fixed_gpu_grants",
                        to="booking.researchgroup",
                    ),
                ),
            ],
            options={
                "ordering": ["research_group__name", "gpu__current_index"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("research_group", "gpu"),
                        name="one_fixed_gpu_grant_per_group",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_hydra_policy, remove_hydra_policy),
    ]
