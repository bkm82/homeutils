from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Min

from . import conditions, grades, ledger, weather
from .grades import ClimbingLevel
from .models import Area, Climber, Credit, Focus, ForecastCache, ToDo, Trip, Weekend
from .weather import DayForecast

VERDICT_RANK = {"go": 2, "marginal": 1, "no": 0}
# How far back a rule may look (e.g. rain on sandstone).
LOOKBACK_DAYS = 3


@dataclass
class DayRating:
    forecast: DayForecast
    rating: conditions.Rating


@dataclass
class Served:
    """What one attendee would get out of a crag."""

    climber: Climber
    todos: list[ToDo]
    value: float
    need: float


@dataclass
class Option:
    """One crag, rated over a set of days and against the group's to-dos."""

    area: Area
    days: list[DayRating]
    score: int  # weather
    fair_score: float = 0.0
    served: list[Served] = field(default_factory=list)
    # Routes in attendees' volume bands, summed, when the crag has OpenBeta counts.
    volume_count: int | None = None

    @property
    def verdict(self) -> str:
        return conditions.Rating(score=self.score).verdict

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
    weekend: Weekend | None
    attendees: list[Climber]
    climbers: list[Climber]
    options: list[Option]
    trips: list[TripOutlook]
    focus: str = Focus.NONE

    @property
    def sunday(self) -> date:
        return self.saturday + timedelta(days=1)

    @property
    def attendee_ids(self) -> set[int]:
        return {c.pk for c in self.attendees}

    @property
    def is_logged(self) -> bool:
        return bool(self.weekend and self.weekend.outcome)


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


def rate_days(area: Area, forecast: list[DayForecast], days: list[date]) -> list[DayRating]:
    index = {day.date: i for i, day in enumerate(forecast)}
    ratings = []
    for day in days:
        i = index.get(day)
        if i is None:
            continue
        prior = forecast[max(0, i - LOOKBACK_DAYS) : i]
        ratings.append(DayRating(forecast[i], conditions.rate(forecast[i], prior, area.sandstone)))
    return ratings


def _rank_key(option: Option, focus: str) -> tuple:
    # Weather always comes first; volume weekends then chase mileage, and
    # otherwise the crag that best pays down what the group is owed wins.
    volume = (option.volume_count or 0) if focus == Focus.VOLUME else 0
    return (VERDICT_RANK[option.verdict], volume, option.fair_score, option.score)


def build_weekend(
    saturday: date,
    weekend: Weekend | None,
    climbers: list[Climber],
    attendees: list[Climber],
    areas: list[Area],
    forecasts: dict[int, list[DayForecast]],
    todos: list[ToDo],
    balances: dict[int, float],
    levels: dict[int, ClimbingLevel],
    trips: list[Trip],
) -> WeekendOutlook:
    days = [saturday, saturday + timedelta(days=1)]
    focus = weekend.focus if weekend else Focus.NONE
    values = ledger.crag_values(todos, projects_only=focus == Focus.TRY_HARD)
    todos_by_spot: dict[tuple[int, int], list[ToDo]] = defaultdict(list)
    for todo in todos:
        if todo.sent_on is None and (todo.is_project or focus != Focus.TRY_HARD):
            todos_by_spot[(todo.climber_id, todo.area_id)].append(todo)

    options = []
    for area in areas:
        rated = rate_days(area, forecasts.get(area.id, []), days)
        if not rated:
            continue
        # Rank by the better day - you can always pick which day to go.
        option = Option(area=area, days=rated, score=max(d.rating.score for d in rated))
        for climber in attendees:
            value = values.get((climber.pk, area.pk), 0.0)
            if not value:
                continue
            need = ledger.need_weight(balances.get(climber.pk, 0.0))
            option.fair_score += need * value
            spot = sorted(todos_by_spot[(climber.pk, area.pk)], key=lambda t: (t.rank, t.pk))
            option.served.append(Served(climber, spot, value, need))
        option.served.sort(key=lambda s: s.need * s.value, reverse=True)
        if area.grade_counts:
            bands = [levels[c.pk].volume_band() for c in attendees if c.pk in levels]
            bands = [b for b in bands if b]
            if bands:
                option.volume_count = sum(
                    grades.count_in_band(area.grade_counts, b) for b in bands
                )
        options.append(option)
    options.sort(key=lambda o: _rank_key(o, focus), reverse=True)

    outlooks = []
    for trip in trips:
        if trip.start_date > days[-1] or trip.end_date < days[0]:
            continue
        trip_days = [
            trip.start_date + timedelta(days=n)
            for n in range((trip.end_date - trip.start_date).days + 1)
        ]
        rated = rate_days(trip.area, forecasts.get(trip.area_id, []), trip_days)
        option = None
        alternatives: list[Option] = []
        if rated:
            # A trip has to work across all its days, so average rather than best-day.
            score = round(sum(d.rating.score for d in rated) / len(rated))
            option = Option(area=trip.area, days=rated, score=score)
            if option.verdict != "go":
                alternatives = [
                    o for o in options if o.verdict == "go" and o.area.pk != trip.area_id
                ][:3]
        outlooks.append(TripOutlook(trip=trip, option=option, alternatives=alternatives))

    return WeekendOutlook(
        saturday=saturday,
        weekend=weekend,
        attendees=attendees,
        climbers=climbers,
        options=options,
        trips=outlooks,
        focus=focus,
    )


def climber_levels(climbers: list[Climber]) -> dict[int, ClimbingLevel]:
    return {c.pk: grades.from_grades(c.onsight_grade, c.redpoint_grade) for c in climbers}


def season_balances() -> dict[int, float]:
    return ledger.balances(Credit.objects.all())


def build_weekends(saturdays: list[date], today: date) -> list[WeekendOutlook]:
    areas = list(Area.objects.all())
    forecasts = weather.get_forecasts(areas)
    climbers = list(Climber.objects.filter(is_active=True))
    todos = list(ToDo.objects.filter(climber__in=climbers, sent_on__isnull=True))
    balances = season_balances()
    levels = climber_levels(climbers)
    trips = list(Trip.objects.filter(end_date__gte=today).select_related("area"))
    weekends = {
        w.saturday: w
        for w in Weekend.objects.filter(saturday__in=saturdays).prefetch_related("attendees")
    }
    outlooks = []
    for saturday in saturdays:
        weekend = weekends.get(saturday)
        # Until someone changes it, everyone's in.
        attendees = (
            [c for c in weekend.attendees.all() if c.is_active] if weekend else list(climbers)
        )
        outlooks.append(
            build_weekend(
                saturday,
                weekend,
                climbers,
                attendees,
                areas,
                forecasts,
                todos,
                balances,
                levels,
                trips,
            )
        )
    return outlooks


def forecast_updated_at():
    return ForecastCache.objects.aggregate(oldest=Min("fetched_at"))["oldest"]
