"""Rules that turn a day's forecast into a go / marginal / no rating per activity.

Each rule starts from a score and docks (or adds) points with a human-readable
reason, so the planner can show *why* something is rated the way it is. The
thresholds are opinionated defaults for a desert-southwest climber - tune them
here.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .models import Activity
from .weather import STORM_CODES, DayForecast

GO = 70
MARGINAL = 45


@dataclass
class Rating:
    score: int = 100
    reasons: list[str] = field(default_factory=list)

    def adjust(self, points: int, reason: str) -> None:
        self.score += points
        self.reasons.append(reason)

    @property
    def verdict(self) -> str:
        if self.score >= GO:
            return "go"
        if self.score >= MARGINAL:
            return "marginal"
        return "no"


def _rained_on(day: DayForecast) -> bool:
    return day.precip >= 0.1 and day.snowfall < 0.1


def _climbing(day: DayForecast, prior: Sequence[DayForecast], sandstone: bool) -> Rating:
    rating = Rating()
    if day.code in STORM_CODES:
        rating.adjust(-60, "Thunderstorms")
    elif day.snowfall >= 0.1:
        rating.adjust(-70, f'Snow ({day.snowfall:.1f}")')
    elif day.precip >= 0.1 or day.precip_chance >= 60:
        rating.adjust(-60, f"Rain likely ({day.precip_chance}%)")
    elif day.precip_chance >= 30:
        rating.adjust(-20, f"Chance of showers ({day.precip_chance}%)")

    if sandstone:
        recent = sum(d.precip for d in prior[-2:])
        if recent >= 0.05:
            rating.adjust(-70, f'Wet sandstone ({recent:.2f}" in the last 48h)')

    if day.high < 35:
        rating.adjust(-60, f"Too cold (high {day.high:.0f}°F)")
    elif day.high < 45:
        rating.adjust(-30, f"Chilly (high {day.high:.0f}°F)")
    elif day.high > 95:
        rating.adjust(-60, f"Too hot (high {day.high:.0f}°F)")
    elif day.high > 85:
        rating.adjust(-25, f"Hot (high {day.high:.0f}°F)")

    if day.wind >= 30:
        rating.adjust(-30, f"Windy ({day.wind:.0f} mph)")
    elif day.wind >= 20:
        rating.adjust(-10, f"Breezy ({day.wind:.0f} mph)")
    return rating


def _skiing(day: DayForecast, prior: Sequence[DayForecast], sandstone: bool) -> Rating:
    # The forecast can't tell whether there's a base or the resort is open, so
    # a plain day starts below "marginal" - it takes cold or new snow to rate.
    rating = Rating(score=40)
    fresh = day.snowfall + sum(d.snowfall for d in prior[-3:])
    if fresh >= 6:
        rating.adjust(45, f'Powder ({fresh:.0f}" new)')
    elif fresh >= 2:
        rating.adjust(25, f'Fresh snow ({fresh:.0f}" new)')

    if day.high <= 32:
        rating.adjust(15, f"Cold, snow holds (high {day.high:.0f}°F)")
    elif day.high > 50:
        rating.adjust(-40, f"Warm and slushy (high {day.high:.0f}°F)")
    elif day.high > 40:
        rating.adjust(-15, f"Soft snow (high {day.high:.0f}°F)")

    if _rained_on(day):
        rating.adjust(-50, "Rain on snow")
    if day.wind >= 35:
        rating.adjust(-30, f"Lift wind holds likely ({day.wind:.0f} mph)")
    return rating


def _ice(day: DayForecast, prior: Sequence[DayForecast], sandstone: bool) -> Rating:
    rating = Rating(score=30)
    window = [*prior[-3:], day]
    warmest = max(d.high for d in window)
    if warmest <= 32:
        rating.adjust(50, "Sustained freeze")
    elif warmest <= 38:
        rating.adjust(25, f"Mostly freezing (warmest {warmest:.0f}°F)")
    if day.high > 40:
        rating.adjust(-40, f"Too warm, ice degrading (high {day.high:.0f}°F)")
    if _rained_on(day):
        rating.adjust(-40, "Rain")
    return rating


def _hiking(day: DayForecast, prior: Sequence[DayForecast], sandstone: bool) -> Rating:
    # The rainy-day fallback: tolerant of showers, not of storms.
    rating = Rating()
    if day.code in STORM_CODES:
        rating.adjust(-60, "Thunderstorms")
    elif day.precip >= 0.25 or day.precip_chance >= 70:
        rating.adjust(-45, f"Wet ({day.precip_chance}%)")
    elif day.precip >= 0.05 or day.precip_chance >= 40:
        rating.adjust(-15, f"Showers possible ({day.precip_chance}%)")

    if day.snowfall >= 2:
        rating.adjust(-30, f'Snowy trails ({day.snowfall:.0f}")')
    if day.high > 95:
        rating.adjust(-50, f"Too hot (high {day.high:.0f}°F)")
    elif day.high > 85:
        rating.adjust(-20, f"Hot (high {day.high:.0f}°F)")
    elif day.high < 25:
        rating.adjust(-30, f"Bitter cold (high {day.high:.0f}°F)")
    if day.wind >= 35:
        rating.adjust(-20, f"Windy ({day.wind:.0f} mph)")
    return rating


RULES: dict[str, Callable[[DayForecast, Sequence[DayForecast], bool], Rating]] = {
    Activity.CLIMBING: _climbing,
    Activity.SKIING: _skiing,
    Activity.ICE: _ice,
    Activity.HIKING: _hiking,
}


def rate(
    activity: str, day: DayForecast, prior: Sequence[DayForecast], sandstone: bool = False
) -> Rating:
    """Rate `day` for `activity`; `prior` is the days leading up to it, oldest first."""
    rating = RULES[activity](day, prior, sandstone)
    rating.score = max(0, min(100, rating.score))
    return rating
