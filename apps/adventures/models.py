from django.conf import settings
from django.db import models


class Activity(models.TextChoices):
    CLIMBING = "climbing", "Climbing"
    SKIING = "skiing", "Skiing"
    ICE = "ice", "Ice climbing"
    HIKING = "hiking", "Hiking"


class Focus(models.TextChoices):
    """What the coach wants a weekend to be for."""

    NONE = "", "No focus"
    VOLUME = "volume", "Volume"
    TRY_HARD = "try_hard", "Try hard"
    REST = "rest", "Rest"


class Area(models.Model):
    """A place worth going - shared by the whole household, not per-user."""

    name = models.CharField(max_length=120, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    # List of Activity values this area supports.
    activities = models.JSONField(default=list)
    sandstone = models.BooleanField(
        default=False,
        help_text="Soft rock that shouldn't be climbed for a day or two after rain.",
    )
    notes = models.CharField(max_length=200, blank=True)
    # OpenBeta areas whose climbs count toward this area; their grade
    # histograms are summed into grade_counts ({"5.10a": 12, ...}).
    openbeta_uuids = models.JSONField(default=list, blank=True)
    grade_counts = models.JSONField(default=dict, blank=True)
    grade_counts_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def route_total(self) -> int:
        return sum(self.grade_counts.values())

    def get_activities_display(self) -> list[str]:
        labels = dict(Activity.choices)
        return [labels[a] for a in self.activities if a in labels]


class Goal(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="adventure_goals"
    )
    name = models.CharField(max_length=120)
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="goals")
    activity = models.CharField(max_length=20, choices=Activity, default=Activity.CLIMBING)
    grade = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    is_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_done", "area__name", "name"]

    def __str__(self) -> str:
        return self.name


class Weekend(models.Model):
    """Per-weekend household choices, keyed by the weekend's Saturday."""

    saturday = models.DateField(unique=True)
    # Whose goals get first pick this weekend; null means nobody's in particular.
    priority = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    focus = models.CharField(max_length=20, choices=Focus, default=Focus.NONE, blank=True)

    class Meta:
        ordering = ["saturday"]

    def __str__(self) -> str:
        return f"Weekend of {self.saturday}"


class Trip(models.Model):
    """A locked-in plan (e.g. with friends) to keep an eye on as the forecast firms up."""

    title = models.CharField(max_length=120)
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="trips")
    activity = models.CharField(max_length=20, choices=Activity, default=Activity.CLIMBING)
    start_date = models.DateField()
    end_date = models.DateField()
    with_whom = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["start_date"]

    def __str__(self) -> str:
        return self.title


class Tick(models.Model):
    """One row of a Mountain Project tick export."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ticks"
    )
    date = models.DateField()
    route = models.CharField(max_length=200)
    rating = models.CharField(max_length=40, blank=True)
    # Numeric YDS (5.10a = 10.0, 5.10b = 10.25, ...) - null for boulders, ice,
    # aid, and anything else that isn't a roped free-climbing grade.
    grade = models.FloatField(null=True, blank=True)
    style = models.CharField(max_length=40, blank=True)
    lead_style = models.CharField(max_length=40, blank=True)
    route_type = models.CharField(max_length=60, blank=True)
    location = models.CharField(max_length=300, blank=True)
    url = models.URLField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.route} ({self.date})"


class ForecastCache(models.Model):
    """Last Open-Meteo daily payload for an area, so page loads don't hit the API every time."""

    area = models.OneToOneField(Area, on_delete=models.CASCADE, related_name="forecast")
    # Coordinates the payload was fetched for - editing the area's location
    # invalidates the cache.
    latitude = models.FloatField()
    longitude = models.FloatField()
    fetched_at = models.DateTimeField()
    daily = models.JSONField()

    def __str__(self) -> str:
        return f"{self.area} @ {self.fetched_at}"
