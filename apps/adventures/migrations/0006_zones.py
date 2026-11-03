import django.db.models.deletion
from django.db import migrations, models

LOCAL_ZONES = {"Flagstaff", "Sedona"}


def areas_to_zones(apps, schema_editor):
    """Each existing area becomes a zone holding one crag of the same name - split from there."""
    Crag = apps.get_model("adventures", "Crag")
    Zone = apps.get_model("adventures", "Zone")
    Trip = apps.get_model("adventures", "Trip")
    Weekend = apps.get_model("adventures", "Weekend")
    ForecastCache = apps.get_model("adventures", "ForecastCache")

    for crag in Crag.objects.all():
        crag.zone, _ = Zone.objects.get_or_create(
            name=crag.name,
            defaults={
                "latitude": crag.latitude,
                "longitude": crag.longitude,
                "is_local": crag.name in LOCAL_ZONES,
            },
        )
        crag.save(update_fields=["zone"])
    for trip in Trip.objects.select_related("area"):
        trip.zone_id = trip.area.zone_id
        trip.save(update_fields=["zone"])
    # A logged weekend was one area for both days.
    for weekend in Weekend.objects.exclude(area=None):
        weekend.saturday_crag_id = weekend.sunday_crag_id = weekend.area_id
        weekend.save(update_fields=["saturday_crag", "sunday_crag"])
    # Forecasts move to zones; they're refetched on the next page load.
    ForecastCache.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("adventures", "0005_climber_grades"),
    ]

    operations = [
        migrations.CreateModel(
            name="Zone",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=120, unique=True)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
                (
                    "is_local",
                    models.BooleanField(
                        default=False,
                        help_text="Close enough to day-trip, so days can mix local zones.",
                    ),
                ),
                ("notes", models.CharField(blank=True, max_length=200)),
            ],
            options={"ordering": ["-is_local", "name"]},
        ),
        migrations.RenameModel(old_name="Area", new_name="Crag"),
        migrations.AddField(
            model_name="crag",
            name="zone",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="crags",
                to="adventures.zone",
            ),
        ),
        migrations.AddField(
            model_name="trip",
            name="zone",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="trips",
                to="adventures.zone",
            ),
        ),
        migrations.AddField(
            model_name="weekend",
            name="saturday_crag",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="adventures.crag",
            ),
        ),
        migrations.AddField(
            model_name="weekend",
            name="sunday_crag",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="adventures.crag",
            ),
        ),
        migrations.RunPython(areas_to_zones, migrations.RunPython.noop),
    ]
