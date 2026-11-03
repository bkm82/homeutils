import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from django.utils import timezone

from .models import ForecastCache, Zone

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16
# Past days are included so rules can look back, e.g. at rain on sandstone.
PAST_DAYS = 3
CACHE_TTL = timedelta(hours=3)
DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "snowfall_sum",
    "wind_speed_10m_max",
]

# WMO weather interpretation codes, collapsed to what matters for planning.
_CODE_LABELS = [
    ({0, 1}, "Clear"),
    ({2}, "Partly cloudy"),
    ({3}, "Cloudy"),
    ({45, 48}, "Fog"),
    ({51, 53, 55, 56, 57}, "Drizzle"),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "Rain"),
    ({71, 73, 75, 77, 85, 86}, "Snow"),
    ({95, 96, 99}, "Storms"),
]
STORM_CODES = {95, 96, 99}


@dataclass(frozen=True)
class DayForecast:
    date: date
    code: int
    high: float  # °F
    low: float  # °F
    precip: float  # inches, rain + melted snow
    precip_chance: int  # %
    snowfall: float  # inches
    wind: float  # mph, max

    @property
    def summary(self) -> str:
        for codes, label in _CODE_LABELS:
            if self.code in codes:
                return label
        return "—"


def fetch_daily(points: list[tuple[float, float]]) -> list[dict]:
    """One Open-Meteo request for every point; returns each point's `daily` block in order."""
    query = urlencode(
        {
            "latitude": ",".join(str(lat) for lat, _ in points),
            "longitude": ",".join(str(lon) for _, lon in points),
            "daily": ",".join(DAILY_FIELDS),
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "wind_speed_unit": "mph",
            "timezone": "auto",
            "past_days": PAST_DAYS,
            "forecast_days": FORECAST_DAYS,
        }
    )
    with urllib.request.urlopen(f"{OPEN_METEO_URL}?{query}", timeout=10) as response:
        payload = json.load(response)
    # A single location comes back as an object, several as a list.
    results = payload if isinstance(payload, list) else [payload]
    return [result["daily"] for result in results]


def parse_daily(daily: dict) -> list[DayForecast]:
    def value(key: str, i: int) -> float:
        values = daily.get(key)
        return (values[i] if values else None) or 0

    days = []
    for i, day in enumerate(daily["time"]):
        # Open-Meteo pads the far end of the range with nulls for some models.
        if daily["temperature_2m_max"][i] is None:
            continue
        days.append(
            DayForecast(
                date=date.fromisoformat(day),
                code=int(value("weather_code", i)),
                high=value("temperature_2m_max", i),
                low=value("temperature_2m_min", i),
                precip=value("precipitation_sum", i),
                precip_chance=int(value("precipitation_probability_max", i)),
                snowfall=value("snowfall_sum", i),
                wind=value("wind_speed_10m_max", i),
            )
        )
    return days


def _is_fresh(cache: ForecastCache, zone: Zone, now) -> bool:
    return (
        now - cache.fetched_at < CACHE_TTL
        and cache.latitude == zone.latitude
        and cache.longitude == zone.longitude
    )


def get_forecasts(zones: list[Zone]) -> dict[int, list[DayForecast]]:
    """Forecast per zone id, refreshing stale entries in one batched request.

    If the API is unreachable, stale cached forecasts are still returned (better
    than nothing); zones never fetched successfully are simply missing.
    """
    now = timezone.now()
    cached = {c.zone_id: c for c in ForecastCache.objects.filter(zone__in=zones)}
    stale = [z for z in zones if z.id not in cached or not _is_fresh(cached[z.id], z, now)]
    if stale:
        try:
            dailies = fetch_daily([(a.latitude, a.longitude) for a in stale])
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Open-Meteo fetch failed: %s", exc)
        else:
            for zone, daily in zip(stale, dailies, strict=True):
                cached[zone.id], _ = ForecastCache.objects.update_or_create(
                    zone=zone,
                    defaults={
                        "latitude": zone.latitude,
                        "longitude": zone.longitude,
                        "fetched_at": now,
                        "daily": daily,
                    },
                )
    return {zone_id: parse_daily(cache.daily) for zone_id, cache in cached.items()}
