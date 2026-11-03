import django.db.models.deletion
from django.db import migrations, models


# Separate from 0003 so the data copy and these ALTERs run in different
# transactions - Postgres refuses to alter a table with pending trigger events.
class Migration(migrations.Migration):
    dependencies = [
        ("adventures", "0003_climbing_group"),
    ]

    operations = [
        migrations.RemoveField(model_name="tick", name="owner"),
        migrations.AlterField(
            model_name="tick",
            name="climber",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ticks",
                to="adventures.climber",
            ),
        ),
        migrations.DeleteModel(name="Goal"),
        migrations.RemoveField(model_name="weekend", name="priority"),
        migrations.RemoveField(model_name="area", name="activities"),
        migrations.RemoveField(model_name="trip", name="activity"),
        migrations.AlterField(
            model_name="weekend",
            name="focus",
            field=models.CharField(
                blank=True,
                choices=[("", "No focus"), ("volume", "Volume"), ("try_hard", "Try hard")],
                default="",
                max_length=20,
            ),
        ),
    ]
