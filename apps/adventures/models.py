from django.conf import settings
from django.db import models


class Focus(models.TextChoices):
    """What the coach wants a weekend to be for."""

    NONE = "", "No focus"
    VOLUME = "volume", "Volume"
    TRY_HARD = "try_hard", "Try hard"


class Area(models.Model):
    """A crag worth going to - shared by the whole group."""

    name = models.CharField(max_length=120, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
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


class Climber(models.Model):
    """Someone in the group. Doesn't need a login - anyone logged in can manage profiles."""

    name = models.CharField(max_length=80, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="climber",
        help_text="Optional: the login this climber uses.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ToDo(models.Model):
    """A climb someone wants to do. `rank` orders a climber's list - 1 is their biggest."""

    climber = models.ForeignKey(Climber, on_delete=models.CASCADE, related_name="todos")
    name = models.CharField(max_length=120)
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="todos")
    grade = models.CharField(max_length=20, blank=True)
    is_project = models.BooleanField(
        default=False, help_text="Something to work over several sessions, not just onsight."
    )
    rank = models.PositiveIntegerField(default=0)
    sessions = models.PositiveIntegerField(default=0)
    sent_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["climber__name", "rank", "pk"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_sent(self) -> bool:
        return self.sent_on is not None


class Weekend(models.Model):
    """One weekend of the season, keyed by its Saturday: who's in, the focus, what happened."""

    class Outcome(models.TextChoices):
        PLANNED = "", "Not logged"
        CLIMBED = "climbed", "Climbed"
        RAINED_OUT = "rained_out", "Rained out"

    saturday = models.DateField(unique=True)
    attendees = models.ManyToManyField(Climber, blank=True, related_name="weekends")
    focus = models.CharField(max_length=20, choices=Focus, default=Focus.NONE, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome, default=Outcome.PLANNED, blank=True)
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weekends",
        help_text="Where the group actually climbed.",
    )

    class Meta:
        ordering = ["saturday"]

    def __str__(self) -> str:
        return f"Weekend of {self.saturday}"


class Credit(models.Model):
    """What a climbed weekend was worth to one attendee, frozen when it's logged.

    Snapshotted rather than recomputed so later edits to to-do lists (sends,
    re-ranking) don't rewrite the season's history.
    """

    weekend = models.ForeignKey(Weekend, on_delete=models.CASCADE, related_name="credits")
    climber = models.ForeignKey(Climber, on_delete=models.CASCADE, related_name="credits")
    value = models.FloatField()
    # The attendee's equal share of the weekend's total value.
    share = models.FloatField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["weekend", "climber"], name="unique_credit")
        ]

    def __str__(self) -> str:
        return f"{self.climber} {self.weekend}: {self.value:.2f}/{self.share:.2f}"


class Trip(models.Model):
    """A locked-in plan (e.g. with friends) to keep an eye on as the forecast firms up."""

    title = models.CharField(max_length=120)
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="trips")
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

    climber = models.ForeignKey(Climber, on_delete=models.CASCADE, related_name="ticks")
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
