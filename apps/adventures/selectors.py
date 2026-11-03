from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Min

from . import conditions, grades, weather
from .grades import ClimbingLevel
from .models import Activity, Area, Focus, ForecastCache, Goal, Tick, Trip, Weekend
from .weather import DayForecast

VERDICT_RANK = {"go": 2, "marginal": 1, "no": 0}
# How far back a rule may look (e.g. rain on sandstone).
LOOKBACK_DAYS = 3


@dataclass
class DayRating:
    forecast: DayForecast
    rating: conditions.Rating


@dataclass
class Option:
    """One area + activity, rated over a set of days."""

    area: Area
    activity: str
    days: list[DayRating]
    score: int
    goals: list[Goal] = field(default_factory=list)
    priority_goals: list[Goal] = field(default_factory=list)
    # Routes in the climber's volume band, when the area has OpenBeta counts.
    volume_count: int | None = None
    # How well this option serves the weekend's training focus; higher ranks first.
    focus_fit: int = 0

    @property
    def verdict(self) -> str:
        return conditions.Rating(score=self.score).verdict

    @property
    def activity_label(self) -> str:
        return dict(Activity.choices).get(self.activity, self.activity)

    @property
    def reasons(self) -> list[str]:
        seen: list[str] = []
        for day in self.days:
            for reason in day.rating.reasons:
                if reason not in seen:
                    seen.append(reason)
        return seen


@dataclass
class TripOutlook:
    trip: Trip
    option: Option | None  # None when no trip day is inside the forecast yet
    alternatives: list[Option] = field(default_factory=list)


@dataclass
class WeekendOutlook:
    saturday: date
    priority: AbstractBaseUser | None
    options: list[Option]
    trips: list[TripOutlook]
    focus: str = Focus.NONE
    # Whose level and projects the focus is judged against.
    climber: AbstractBaseUser | None = None
    level: ClimbingLevel | None = None

    @property
    def sunday(self) -> date:
        return self.saturday + timedelta(days=1)


def weekend_saturdays(today: date, horizon_days: int = weather.FORECAST_DAYS) -> list[date]:
    """Saturdays of every weekend that's at least partly inside the forecast horizon.

    On a Sunday, the current weekend (yesterday's Saturday) still counts.
    """
    if today.weekday() == 6:
        saturday = today - timedelta(days=1)
    else:
        saturday = today + timedelta(days=5 - today.weekday())
    last_day = today + timedelta(days=horizon_days - 1)
    saturdays = []
    while saturday <= last_day:
        saturdays.append(saturday)
        saturday += timedelta(days=7)
    return saturdays


def rate_days(
    area: Area, activity: str, forecast: list[DayForecast], days: list[date]
) -> list[DayRating]:
    index = {day.date: i for i, day in enumerate(forecast)}
    ratings = []
    for day in days:
        i = index.get(day)
        if i is None:
            continue
        prior = forecast[max(0, i - LOOKBACK_DAYS) : i]
        ratings.append(
            DayRating(forecast[i], conditions.rate(activity, forecast[i], prior, area.sandstone))
        )
    return ratings


def _focus_fit(option: Option, focus: str, climber_goals: list[Goal]) -> int:
    if focus == Focus.REST:
        return int(option.activity in (Activity.HIKING, Activity.SKIING))
    if option.activity != Activity.CLIMBING:
        return 0
    if focus == Focus.VOLUME:
        return option.volume_count or 0
    if focus == Focus.TRY_HARD:
        return len(climber_goals)
    return 0


def _rank_key(option: Option) -> tuple:
    # Weather always comes first; the focus only reorders within a verdict.
    # A priority person's goal only lifts an option it's actually worth going for.
    boosted = bool(option.priority_goals) and option.verdict != "no"
    return (VERDICT_RANK[option.verdict], option.focus_fit, boosted, option.score)


def build_weekend(
    saturday: date,
    priority: AbstractBaseUser | None,
    areas: list[Area],
    forecasts: dict[int, list[DayForecast]],
    goals: list[Goal],
    trips: list[Trip],
    focus: str = Focus.NONE,
    climber: AbstractBaseUser | None = None,
    level: ClimbingLevel | None = None,
) -> WeekendOutlook:
    days = [saturday, saturday + timedelta(days=1)]
    band = level.volume_band() if level else None
    goals_by_spot: dict[tuple[int, str], list[Goal]] = defaultdict(list)
    for goal in goals:
        goals_by_spot[(goal.area_id, goal.activity)].append(goal)

    options = []
    for area in areas:
        forecast = forecasts.get(area.id)
        if not forecast:
            continue
        for activity in area.activities:
            rated = rate_days(area, activity, forecast, days)
            if not rated:
                continue
            spot_goals = goals_by_spot[(area.id, activity)]
            option = Option(
                area=area,
                activity=activity,
                days=rated,
                # Rank by the better day - you can always pick which day to go.
                score=max(d.rating.score for d in rated),
                goals=spot_goals,
                priority_goals=[g for g in spot_goals if priority and g.owner_id == priority.pk],
            )
            if band and activity == Activity.CLIMBING and area.grade_counts:
                option.volume_count = grades.count_in_band(area.grade_counts, band)
            climber_goals = [g for g in spot_goals if climber and g.owner_id == climber.pk]
            option.focus_fit = _focus_fit(option, focus, climber_goals)
            options.append(option)
    options.sort(key=_rank_key, reverse=True)

    outlooks = []
    for trip in trips:
        if trip.start_date > days[-1] or trip.end_date < days[0]:
            continue
        trip_days = [
            trip.start_date + timedelta(days=n)
            for n in range((trip.end_date - trip.start_date).days + 1)
        ]
        rated = rate_days(trip.area, trip.activity, forecasts.get(trip.area_id, []), trip_days)
        option = None
        alternatives: list[Option] = []
        if rated:
            # A trip has to work across all its days, so average rather than best-day.
            score = round(sum(d.rating.score for d in rated) / len(rated))
            option = Option(area=trip.area, activity=trip.activity, days=rated, score=score)
            if option.verdict != "go":
                alternatives = [
                    o
                    for o in options
                    if o.verdict == "go"
                    and (o.area.id, o.activity) != (trip.area_id, trip.activity)
                ][:3]
        outlooks.append(TripOutlook(trip=trip, option=option, alternatives=alternatives))

    return WeekendOutlook(
        saturday=saturday,
        priority=priority,
        options=options,
        trips=outlooks,
        focus=focus,
        climber=climber,
        level=level,
    )


def build_weekends(
    saturdays: list[date], today: date, viewer: AbstractBaseUser
) -> list[WeekendOutlook]:
    areas = list(Area.objects.all())
    forecasts = weather.get_forecasts(areas)
    goals = list(Goal.objects.filter(is_done=False).select_related("owner", "area"))
    trips = list(Trip.objects.filter(end_date__gte=today).select_related("area"))
    weekends = {
        w.saturday: w
        for w in Weekend.objects.filter(saturday__in=saturdays).select_related("priority")
    }
    levels: dict[int, ClimbingLevel] = {}
    outlooks = []
    for saturday in saturdays:
        weekend = weekends.get(saturday)
        priority = weekend.priority if weekend else None
        climber = priority or viewer
        if climber.pk not in levels:
            levels[climber.pk] = grades.climbing_level(
                Tick.objects.filter(owner=climber, grade__isnull=False), today
            )
        outlooks.append(
            build_weekend(
                saturday,
                priority,
                areas,
                forecasts,
                goals,
                trips,
                focus=weekend.focus if weekend else Focus.NONE,
                climber=climber,
                level=levels[climber.pk],
            )
        )
    return outlooks


def forecast_updated_at():
    return ForecastCache.objects.aggregate(oldest=Min("fetched_at"))["oldest"]
