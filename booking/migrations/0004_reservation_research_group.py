import django.db.models.deletion
from django.db import migrations, models


def backfill_reservation_groups(apps, schema_editor):
    reservation_model = apps.get_model("booking", "Reservation")
    profile_model = apps.get_model("booking", "UserProfile")
    for profile in profile_model.objects.exclude(research_group=None):
        reservation_model.objects.filter(
            owner_id=profile.user_id,
            research_group=None,
        ).update(research_group_id=profile.research_group_id)


class Migration(migrations.Migration):
    dependencies = [("booking", "0003_research_groups_and_policy")]

    operations = [
        migrations.AddField(
            model_name="reservation",
            name="research_group",
            field=models.ForeignKey(
                blank=True,
                help_text="Group charged for this reservation at booking time.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reservations",
                to="booking.researchgroup",
            ),
        ),
        migrations.RunPython(backfill_reservation_groups, migrations.RunPython.noop),
    ]
