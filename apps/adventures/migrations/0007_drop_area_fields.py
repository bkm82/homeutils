import django.db.models.deletion
from django.db import migrations, models


# Separate from 0006 so the data copy and these ALTERs run in different
# transactions - Postgres refuses to alter a table with pending trigger events.
class Migration(migrations.Migration):
    dependencies = [
        ("adventures", "0006_zones"),
    ]

    operations = [
        migrations.RemoveField(model_name="forecastcache", name="area"),
        migrations.AddField(
            model_name="forecastcache",
            name="zone",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="forecast",
                to="adventures.zone",
            ),
            preserve_default=False,
        ),
        migrations.RenameField(model_name="todo", old_name="area", new_name="crag"),
        migrations.RemoveField(model_name="weekend", name="area"),
        migrations.RemoveField(model_name="trip", name="area"),
        migrations.RemoveField(model_name="crag", name="latitude"),
        migrations.RemoveField(model_name="crag", name="longitude"),
        migrations.AlterField(
            model_name="crag",
            name="zone",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="crags",
                to="adventures.zone",
            ),
        ),
        migrations.AlterField(
            model_name="trip",
            name="zone",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="trips",
                to="adventures.zone",
            ),
        ),
        migrations.AlterField(
            model_name="crag",
            name="name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterModelOptions(
            name="crag",
            options={"ordering": ["zone__name", "name"]},
        ),
        migrations.AddConstraint(
            model_name="crag",
            constraint=models.UniqueConstraint(fields=("zone", "name"), name="unique_crag"),
        ),
    ]
