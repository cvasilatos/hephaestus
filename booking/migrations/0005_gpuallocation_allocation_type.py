from django.db import migrations, models


def add_allocation_type_column(apps, schema_editor):
    schema_editor.execute(
        "ALTER TABLE booking_gpuallocation ADD COLUMN allocation_type "
        "varchar(16) NOT NULL DEFAULT 'unmanaged'"
    )


def remove_allocation_type_column(apps, schema_editor):
    schema_editor.execute("ALTER TABLE booking_gpuallocation DROP COLUMN allocation_type")


def backfill_allocation_types(apps, schema_editor):
    allocation_model = apps.get_model("booking", "GPUAllocation")
    for allocation in allocation_model.objects.select_related("gpu"):
        allocation.allocation_type = allocation.gpu.allocation_type
        allocation.save(update_fields=["allocation_type"])


class Migration(migrations.Migration):
    dependencies = [("booking", "0004_reservation_research_group")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_allocation_type_column, remove_allocation_type_column)
            ],
            state_operations=[
                migrations.AddField(
                    model_name="gpuallocation",
                    name="allocation_type",
                    field=models.CharField(
                        choices=[
                            ("shared", "Shared pool"),
                            ("fixed", "Group fixed"),
                            ("unmanaged", "Unassigned"),
                        ],
                        default="unmanaged",
                        help_text="GPU classification captured when the reservation was saved.",
                        max_length=16,
                    ),
                )
            ],
        ),
        migrations.RunPython(backfill_allocation_types, migrations.RunPython.noop),
    ]
