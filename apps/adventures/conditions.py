"""Rules that turn a day's forecast into a go / marginal / no climbing rating.

Each rule starts from a score and docks (or adds) points with a human-readable
reason, so the planner can show *why* something is rated the way it is. The
thresholds are opinionated defaults for a desert-southwest climber - tune them
here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

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


def rate(day: DayForecast, prior: Sequence[DayForecast], sandstone: bool = False) -> Rating:
    """Rate `day` for climbing; `prior` is the days leading up to it, oldest first."""
    rating = _climbing(day, prior, sandstone)
    rating.score = max(0, min(100, rating.score))
    return rating
