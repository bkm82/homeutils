from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from .models import Medication

HEATMAP_WEEKS = 12


@dataclass(frozen=True)
class HeatmapCell:
    date: date
    status: str  # "taken" | "missed" | "today-pending"


def _taken_dates(medication: Medication) -> set[date]:
    return set(medication.logs.filter(taken=True).values_list("date", flat=True))


def current_streak(medication: Medication, today: date | None = None) -> int:
    """Consecutive taken days ending today (or yesterday if today is still
    unlogged, since a pending day shouldn't break the streak yet)."""
    today = today or timezone.localdate()
    taken = _taken_dates(medication)

    day = today
    if day not in taken:
        day -= timedelta(days=1)

    streak = 0
    while day in taken:
        streak += 1
        day -= timedelta(days=1)
    return streak


def completion_rate(
    medication: Medication, window_days: int = 30, today: date | None = None
) -> int:
    """Percentage of days taken over the last `window_days`."""
    today = today or timezone.localdate()
    start = today - timedelta(days=window_days - 1)
    taken_count = medication.logs.filter(taken=True, date__gte=start, date__lte=today).count()
    return round(100 * taken_count / window_days)


def heatmap(
    medication: Medication, weeks: int = HEATMAP_WEEKS, today: date | None = None
) -> list[HeatmapCell]:
    """Oldest-to-newest list of day cells for the trailing `weeks`, aligned
    so the grid always ends on today."""
    today = today or timezone.localdate()
    taken = _taken_dates(medication)

    total_days = weeks * 7
    first_day = today - timedelta(days=total_days - 1)

    cells = []
    for offset in range(total_days):
        day = first_day + timedelta(days=offset)
        if day == today and day not in taken:
            status = "today-pending"
        elif day in taken:
            status = "taken"
        else:
            status = "missed"
        cells.append(HeatmapCell(date=day, status=status))
    return cells
