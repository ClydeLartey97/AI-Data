"""
Location weather — the physical cause behind the carbon signal.

Carbon intensity is not an arbitrary number that happens to move. It moves
because the wind blows and the sun shines. Pulling weather for the same
location as the grid region turns the dashboard from "trust this figure" into
"here is why the figure is what it is" — 96% wind in North Scotland is a
weather fact before it is a grid fact.

It also matters for the forecasting work already decided in HANDOFF.md: GB's
own operator forecast is too good to beat, but CAISO and ERCOT have no free
transparent equivalent, and weather is the input any such model is built on.
This is the feed that work would start from.

Open-Meteo. Public, keyless, worldwide — so unlike the GB carbon endpoints,
this one already works for the markets that do not have adapters yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from adapters.open_meteo import OpenMeteoClient, fetch_forecast

#: Rough cut-in / rated speeds for a typical utility-scale turbine, at 100 m.
#: Used only to say "windy enough to generate" in plain language — this is a
#: readability aid, not a power-curve model, and must not be presented as a
#: generation forecast.
WIND_CUT_IN_MS = 3.5
WIND_RATED_MS = 12.0


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    postcode: str | None = None


#: A few GB locations spanning the widest carbon spread, for a location picker
#: that is useful the moment it opens. Each sits in a different grid region.
PRESETS = [
    Location("Thurso, North Scotland", 58.59, -3.52, "KW14"),
    Location("Edinburgh", 55.95, -3.19, "EH1"),
    Location("Manchester", 53.48, -2.24, "M1"),
    Location("Cardiff", 51.48, -3.18, "CF10"),
    Location("London", 51.51, -0.13, "SW1A"),
    Location("Nottingham, East Midlands", 52.95, -1.15, "NG1"),
]


@dataclass
class WeatherPoint:
    timestamp: datetime
    temperature_c: float | None
    solar_radiation_wm2: float | None
    wind_speed_100m_ms: float | None
    cloud_cover_pct: float | None

    @property
    def wind_usefulness(self) -> float:
        """0-1, how much of a turbine's working range this wind speed reaches.

        Deliberately crude and deliberately labelled as such — it exists so a
        reader can see "the wind is doing the work here", not to predict MW.
        """
        if self.wind_speed_100m_ms is None:
            return 0.0
        span = WIND_RATED_MS - WIND_CUT_IN_MS
        return max(0.0, min(1.0, (self.wind_speed_100m_ms - WIND_CUT_IN_MS) / span))


class WeatherAdapter:
    """Weather for a location, on the same forward-looking basis as the grid."""

    def __init__(self, *, timeout_seconds: float = 30.0,
                 max_attempts: int = 3) -> None:
        self._client = OpenMeteoClient(
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )

    def forecast(self, location: Location, days: int = 2) -> list[WeatherPoint]:
        frame = fetch_forecast(location.latitude, location.longitude, days=days,
                               client=self._client)
        return [
            WeatherPoint(
                timestamp=row.start_time.to_pydatetime(),
                temperature_c=_num(row.temperature_c),
                solar_radiation_wm2=_num(row.solar_radiation_wm2),
                wind_speed_100m_ms=_num(row.wind_speed_100m_ms),
                cloud_cover_pct=_num(row.cloud_cover_pct),
            )
            for row in frame.itertuples(index=False)
        ]


def _num(value) -> float | None:
    try:
        return None if value is None or value != value else float(value)
    except (TypeError, ValueError):
        return None
