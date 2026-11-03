"""YDS grade math and Mountain Project tick parsing.

Grades are mapped onto one number line so they can be averaged and compared:
5.9 = 9.0, 5.10a = 10.0, 5.10b = 10.25, 5.10c = 10.5, 5.10d = 10.75, 5.11a = 11.0.
Coarse grades (common in OpenBeta's data) land between letters: 5.10- = a/b,
5.10 = b/c, 5.10+ = c/d.
"""

import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_YDS = re.compile(r"5\.(\d{1,2})([abcd](?:/[abcd])?|[+-])?")
_LETTERS = {"a": 0.0, "b": 0.25, "c": 0.5, "d": 0.75}
_COARSE = {"-": 0.125, "": 0.375, "+": 0.625}
_SUB_TEN_MODIFIER = {"-": -0.33, "": 0.0, "+": 0.33}

ONSIGHT_STYLES = {"onsight", "flash"}
REDPOINT_STYLES = ONSIGHT_STYLES | {"redpoint", "pinkpoint"}
LEVEL_WINDOW_DAYS = 120
LEVEL_TOP_N = 3


def parse_yds(rating: str) -> float | None:
    match = _YDS.search(rating or "")
    if not match:
        return None
    number, suffix = int(match.group(1)), match.group(2) or ""
    if number < 10:
        # Letters below 5.10 are rare; treat them like +/-.
        if suffix and suffix[0] in _LETTERS:
            suffix = "-" if suffix[0] in "ab" else "+"
        return number + _SUB_TEN_MODIFIER.get(suffix, 0.0)
    if suffix in _COARSE:
        return number + _COARSE[suffix]
    letters = suffix.split("/")
    return number + sum(_LETTERS[letter] for letter in letters) / len(letters)


def yds_label(value: float) -> str:
    if value < (9 + _SUB_TEN_MODIFIER["+"] + 10) / 2:  # nearer 5.9+ than 5.10a
        _, label = min(
            (abs(n + offset - value), f"5.{n}{suffix}")
            for n in range(10)
            for suffix, offset in _SUB_TEN_MODIFIER.items()
        )
        return label
    # Nearest letter grade, counting quarter-steps up from 5.10a.
    steps = max(0, round((value - 10) / 0.25))
    return f"5.{10 + steps // 4}{'abcd'[steps % 4]}"


@dataclass
class ClimbingLevel:
    onsight: float | None
    redpoint: float | None

    @property
    def onsight_label(self) -> str | None:
        return yds_label(self.onsight) if self.onsight is not None else None

    @property
    def redpoint_label(self) -> str | None:
        return yds_label(self.redpoint) if self.redpoint is not None else None

    @property
    def volume_band_label(self) -> str | None:
        band = self.volume_band()
        return f"{yds_label(band[0])}–{yds_label(band[1])}" if band else None

    def volume_band(self) -> tuple[float, float] | None:
        """Grades that make good mileage: up to three letter grades under onsight."""
        if self.onsight is None:
            return None
        return (self.onsight - 0.75, self.onsight)


def from_grades(onsight: str, redpoint: str) -> ClimbingLevel:
    """A level from hand-entered grade labels (blank or unparseable -> unknown)."""
    return ClimbingLevel(onsight=parse_yds(onsight), redpoint=parse_yds(redpoint))


def _top_average(grades: list[float]) -> float | None:
    top = sorted(grades, reverse=True)[:LEVEL_TOP_N]
    return sum(top) / len(top) if top else None


def climbing_level(ticks: Iterable, today: date) -> ClimbingLevel:
    """Onsight/redpoint level from the average of the hardest few clean leads recently."""
    since = today - timedelta(days=LEVEL_WINDOW_DAYS)
    onsights, sends = [], []
    for tick in ticks:
        if tick.grade is None or tick.date < since:
            continue
        lead_style = tick.lead_style.strip().lower()
        if lead_style in ONSIGHT_STYLES:
            onsights.append(tick.grade)
        if lead_style in REDPOINT_STYLES:
            sends.append(tick.grade)
    return ClimbingLevel(onsight=_top_average(onsights), redpoint=_top_average(sends))


def count_in_band(grade_counts: Mapping[str, int], band: tuple[float, float]) -> int:
    low, high = band
    total = 0
    for label, count in grade_counts.items():
        value = parse_yds(label)
        if value is not None and low <= value <= high:
            total += count
    return total


def parse_tick_export(text: str) -> list[dict]:
    """Rows of a Mountain Project tick-export CSV as Tick field dicts.

    Raises ValueError if it doesn't look like a tick export at all.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"Date", "Route", "Rating"} <= set(reader.fieldnames):
        raise ValueError("Expected a Mountain Project tick export with Date, Route and Rating")
    rows = []
    for row in reader:
        try:
            day = datetime.strptime(row["Date"].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        rating = (row.get("Rating") or "").strip()
        rows.append(
            {
                "date": day,
                "route": (row.get("Route") or "").strip()[:200],
                "rating": rating[:40],
                "grade": parse_yds(rating),
                "style": (row.get("Style") or "").strip()[:40],
                "lead_style": (row.get("Lead Style") or "").strip()[:40],
                "route_type": (row.get("Route Type") or "").strip()[:60],
                "location": (row.get("Location") or "").strip()[:300],
                "url": (row.get("URL") or "").strip()[:300],
            }
        )
    return rows
