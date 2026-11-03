import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def to_climbers(apps, schema_editor):
    """Carry per-user goals and ticks over to climber profiles; drop non-climbing data."""
    Area = apps.get_model("adventures", "Area")
    Climber = apps.get_model("adventures", "Climber")
    Goal = apps.get_model("adventures", "Goal")
    Tick = apps.get_model("adventures", "Tick")
    ToDo = apps.get_model("adventures", "ToDo")
    Weekend = apps.get_model("adventures", "Weekend")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    climbers = {}

    def climber_for(user_id):
        if user_id not in climbers:
            user = User.objects.get(pk=user_id)
            name = " ".join(filter(None, [user.first_name, user.last_name])) or user.username
            climbers[user_id], _ = Climber.objects.get_or_create(
                user=user, defaults={"name": name}
            )
        return climbers[user_id]

    goals = Goal.objects.filter(activity="climbing").order_by("owner_id", "is_done", "created_at")
    ranks = {}
    for goal in goals:
        climber = climber_for(goal.owner_id)
        ranks[climber.pk] = ranks.get(climber.pk, 0) + 1
        ToDo.objects.create(
            climber=climber,
            name=goal.name,
            area_id=goal.area_id,
            grade=goal.grade,
            notes=goal.notes,
            rank=ranks[climber.pk],
            sent_on=timezone.localdate() if goal.is_done else None,
        )
    for tick in Tick.objects.all():
        tick.climber = climber_for(tick.owner_id)
        tick.save(update_fields=["climber"])

    Weekend.objects.filter(focus="rest").update(focus="")
    for area in Area.objects.all():
        # Ski/ice-only crags go, unless something still points at them.
        if (
            "climbing" not in area.activities
            and not area.trips.exists()
            and not area.todos.exists()
        ):
            area.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("adventures", "0002_seed_areas"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Climber",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=80, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "user",
                    models.OneToOneField(
                        blank=True,
                        help_text="Optional: the login this climber uses.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="climber",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="ToDo",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("grade", models.CharField(blank=True, max_length=20)),
                (
                    "is_project",
                    models.BooleanField(
                        default=False,
                        help_text="Something to work over several sessions, not just onsight.",
                    ),
                ),
                ("rank", models.PositiveIntegerField(default=0)),
                ("sessions", models.PositiveIntegerField(default=0)),
                ("sent_on", models.DateField(blank=True, null=True)),
                ("notes", models.CharField(blank=True, max_length=200)),
                (
                    "area",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="todos",
                        to="adventures.area",
                    ),
                ),
                (
                    "climber",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="todos",
                        to="adventures.climber",
                    ),
                ),
            ],
            options={"ordering": ["climber__name", "rank", "pk"]},
        ),
        migrations.AddField(
            model_name="tick",
            name="climber",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ticks",
                to="adventures.climber",
            ),
        ),
        migrations.RunPython(to_climbers, migrations.RunPython.noop),
        migrations.AddField(
            model_name="weekend",
            name="attendees",
            field=models.ManyToManyField(
                blank=True, related_name="weekends", to="adventures.climber"
            ),
        ),
        migrations.AddField(
            model_name="weekend",
            name="outcome",
            field=models.CharField(
                blank=True,
                choices=[("", "Not logged"), ("climbed", "Climbed"), ("rained_out", "Rained out")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="weekend",
            name="area",
            field=models.ForeignKey(
                blank=True,
                help_text="Where the group actually climbed.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="weekends",
                to="adventures.area",
            ),
        ),
        migrations.CreateModel(
            name="Credit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("value", models.FloatField()),
                ("share", models.FloatField()),
                (
                    "climber",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credits",
                        to="adventures.climber",
                    ),
                ),
                (
                    "weekend",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credits",
                        to="adventures.weekend",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("weekend", "climber"), name="unique_credit")
                ]
            },
        ),
    ]
