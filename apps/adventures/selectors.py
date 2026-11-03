from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Min

from . import conditions, grades, ledger, weather
from .grades import ClimbingLevel
from .models import Climber, Crag, Credit, Focus, ForecastCache, ToDo, Trip, Weekend, Zone
from .weather import DayForecast

VERDICT_RANK = {"go": 2, "marginal": 1, "no": 0}
# How far back a rule may look (e.g. rain on sandstone).
LOOKBACK_DAYS = 3
# A second day at the same crag is worth less to the plan, so plans spread
# sessions around unless one crag is clearly the best for the group.
REPEAT_DISCOUNT = 0.5


@dataclass
class Served:
    """What one attendee would get out of a crag."""

    climber: Climber
    todos: list[ToDo]
    value: float
    need: float


@dataclass
class CragDay:
    """One crag on one day: its weather and how well it serves the attendees."""

    crag: Crag
    forecast: DayForecast
    rating: conditions.Rating
    fair_score: float = 0.0
    served: list[Served] = field(default_factory=list)
    # Routes in attendees' volume bands, summed, when the crag has OpenBeta counts.
    volume_count: int | None = None

    @property
    def verdict(self) -> str:
        return self.rating.verdict


@dataclass
class Plan:
    """A weekend: exactly one crag per day, from the local zones or from one trip zone."""

    label: str
    zone: Zone | None  # None for the local plan
    picks: list[CragDay]

    @property
    def is_local(self) -> bool:
        return self.zone is None

    @property
    def verdict(self) -> str:
        if not self.picks:
            return "no"
        score = round(sum(p.rating.score for p in self.picks) / len(self.picks))
        return conditions.Rating(score=score).verdict

    @property
    def reasons(self) -> list[str]:
        seen: list[str] = []
        for pick in self.picks:
            for reason in pick.rating.reasons:
                if reason not in seen:
                    seen.append(reason)
        return seen


@dataclass
class TripOutlook:
    trip: Trip
    plan: Plan | None  # None when no trip day is inside the forecast yet
    alternatives: list[Plan] = field(default_factory=list)


@dataclass
class WeekendOutlook:
    saturday: date
    weekend: Weekend | None
    attendees: list[Climber]
    climbers: list[Climber]
    plans: list[Plan]
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


def rate_day(
    forecast: list[DayForecast], day: date, sandstone: bool
) -> tuple[DayForecast, conditions.Rating] | None:
    for i, candidate in enumerate(forecast):
        if candidate.date == day:
            prior = forecast[max(0, i - LOOKBACK_DAYS) : i]
            return candidate, conditions.rate(candidate, prior, sandstone)
    return None


class _Context:
    """Everything needed to score crags for one weekend's attendees."""

    def __init__(
        self,
        attendees: list[Climber],
        focus: str,
        forecasts: dict[int, list[DayForecast]],
        todos: list[ToDo],
        balances: dict[int, float],
        levels: dict[int, ClimbingLevel],
    ):
        self.attendees = attendees
        self.focus = focus
        self.forecasts = forecasts
        self.balances = balances
        self.bands = [
            band for c in attendees if c.pk in levels and (band := levels[c.pk].volume_band())
        ]
        projects_only = focus == Focus.TRY_HARD
        self.values = ledger.crag_values(todos, projects_only=projects_only)
        self.todos_by_spot: dict[tuple[int, int], list[ToDo]] = defaultdict(list)
        for todo in sorted(todos, key=lambda t: (t.rank, t.pk)):
            if todo.sent_on is None and (todo.is_project or not projects_only):
                self.todos_by_spot[(todo.climber_id, todo.crag_id)].append(todo)
        self._cache: dict[tuple[int, date], CragDay | None] = {}

    def crag_day(self, crag: Crag, day: date) -> CragDay | None:
        key = (crag.pk, day)
        if key not in self._cache:
            self._cache[key] = self._score(crag, day)
        return self._cache[key]

    def _score(self, crag: Crag, day: date) -> CragDay | None:
        rated = rate_day(self.forecasts.get(crag.zone_id, []), day, crag.sandstone)
        if rated is None:
            return None
        crag_day = CragDay(crag, *rated)
        for climber in self.attendees:
            value = self.values.get((climber.pk, crag.pk), 0.0)
            if value:
                need = ledger.need_weight(self.balances.get(climber.pk, 0.0))
                crag_day.fair_score += need * value
                spot = self.todos_by_spot[(climber.pk, crag.pk)]
                crag_day.served.append(Served(climber, spot, value, need))
        crag_day.served.sort(key=lambda s: s.need * s.value, reverse=True)
        if crag.grade_counts and self.bands:
            crag_day.volume_count = sum(
                grades.count_in_band(crag.grade_counts, band) for band in self.bands
            )
        return crag_day

    def volume(self, crag_day: CragDay) -> int:
        return (crag_day.volume_count or 0) if self.focus == Focus.VOLUME else 0

    def plan(self, label: str, zone: Zone | None, crags: list[Crag], days: list[date]) -> Plan:
        picks: list[CragDay] = []
        for day in days:
            options = [cd for crag in crags if (cd := self.crag_day(crag, day))]
            if not options:
                continue
            picked = {p.crag.pk for p in picks}

            def key(cd: CragDay, picked=picked) -> tuple:
                # Weather first; volume weekends then chase mileage; otherwise
                # the crag that best pays down what the group is owed.
                discount = REPEAT_DISCOUNT if cd.crag.pk in picked else 1.0
                return (
                    VERDICT_RANK[cd.verdict],
                    self.volume(cd) * discount,
                    cd.fair_score * discount,
                    cd.rating.score,
                )

            picks.append(max(options, key=key))
        return Plan(label, zone, picks)

    def plan_key(self, plan: Plan) -> tuple:
        return (
            sum(VERDICT_RANK[p.verdict] for p in plan.picks),
            sum(self.volume(p) for p in plan.picks),
            sum(p.fair_score for p in plan.picks),
            sum(p.rating.score for p in plan.picks),
        )


def build_weekend(
    saturday: date,
    weekend: Weekend | None,
    climbers: list[Climber],
    attendees: list[Climber],
    zones: list[Zone],
    crags: list[Crag],
    forecasts: dict[int, list[DayForecast]],
    todos: list[ToDo],
    balances: dict[int, float],
    levels: dict[int, ClimbingLevel],
    trips: list[Trip],
) -> WeekendOutlook:
    days = [saturday, saturday + timedelta(days=1)]
    focus = weekend.focus if weekend else Focus.NONE
    ctx = _Context(attendees, focus, forecasts, todos, balances, levels)

    crags_by_zone: dict[int, list[Crag]] = defaultdict(list)
    for crag in crags:
        crags_by_zone[crag.zone_id].append(crag)
    local = [c for z in zones if z.is_local for c in crags_by_zone[z.pk]]

    plans = []
    if local:
        plans.append(ctx.plan("Local", None, local, days))
    for zone in zones:
        if not zone.is_local and crags_by_zone[zone.pk]:
            plans.append(ctx.plan(zone.name, zone, crags_by_zone[zone.pk], days))
    plans = [p for p in plans if p.picks]
    plans.sort(key=ctx.plan_key, reverse=True)

    outlooks = []
    for trip in trips:
        if trip.start_date > days[-1] or trip.end_date < days[0]:
            continue
        trip_days = [
            trip.start_date + timedelta(days=n)
            for n in range((trip.end_date - trip.start_date).days + 1)
        ]
        plan = ctx.plan(trip.zone.name, trip.zone, crags_by_zone[trip.zone_id], trip_days)
        alternatives: list[Plan] = []
        if not plan.picks:
            plan = None
        elif plan.verdict != "go":
            alternatives = [
                p
                for p in plans
                if p.verdict == "go" and (p.zone is None or p.zone.pk != trip.zone_id)
            ][:3]
        outlooks.append(TripOutlook(trip=trip, plan=plan, alternatives=alternatives))

    return WeekendOutlook(
        saturday=saturday,
        weekend=weekend,
        attendees=attendees,
        climbers=climbers,
        plans=plans,
        trips=outlooks,
        focus=focus,
    )


def climber_levels(climbers: list[Climber]) -> dict[int, ClimbingLevel]:
    return {c.pk: grades.from_grades(c.onsight_grade, c.redpoint_grade) for c in climbers}


def season_balances() -> dict[int, float]:
    return ledger.balances(Credit.objects.all())


def build_weekends(saturdays: list[date], today: date) -> list[WeekendOutlook]:
    zones = list(Zone.objects.all())
    crags = list(Crag.objects.select_related("zone"))
    forecasts = weather.get_forecasts(zones)
    climbers = list(Climber.objects.filter(is_active=True))
    todos = list(ToDo.objects.filter(climber__in=climbers, sent_on__isnull=True))
    balances = season_balances()
    levels = climber_levels(climbers)
    trips = list(Trip.objects.filter(end_date__gte=today).select_related("zone"))
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
                zones,
                crags,
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
