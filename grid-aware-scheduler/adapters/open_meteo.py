"""Open-Meteo forecast client.

Weather is the one leading indicator this project has. Forecast irradiance and
hub-height wind anticipate a declared plant's output before any generation
number exists, which is what lets a Tier 2 site be scheduled against its own
supply rather than only against the grid.

This is a self-contained copy of the equivalent client in the National Grid
Tool, narrowed to the forecast path this project actually calls. It is
duplicated deliberately: every other market adapter here reaches its data with
nothing but ``requests``, and having weather alone require a sibling checkout
meant the package could not be installed anywhere else. Open-Meteo is public
and keyless, so there is nothing to configure.

Rows land on the same UTC hourly spine as the market adapters, so weather joins
to price and carbon without a dtype fight.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
#: ERA5 reanalysis. Same response shape as the forecast, different horizon —
#: which is what lets the local generation model be scored over history against
#: a reference simulation before it is trusted to place a job in the future.
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: Transient by nature — a retry may succeed. Any other 4xx/5xx is a real
#: failure and is raised immediately rather than retried into a timeout.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Open-Meteo hourly variable -> our column name. Units are pinned in the
# request params below rather than converted here.
_HOURLY_VARIABLES = {
    "temperature_2m": "temperature_c",
    "shortwave_radiation": "solar_radiation_wm2",
    "wind_speed_100m": "wind_speed_100m_ms",
    "cloud_cover": "cloud_cover_pct",
}

WEATHER_COLUMNS = ("start_time", *(_HOURLY_VARIABLES.values()))


class WeatherError(RuntimeError):
    """Raised when Open-Meteo cannot be reached or returns an error."""

    def __init__(self, message: str, *, url: str | None = None,
                 status_code: int | None = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class OpenMeteoClient:
    """Open-Meteo over HTTPS, with bounded retries on transient failures."""

    def __init__(self, *, timeout_seconds: float = 30.0, max_attempts: int = 3,
                 backoff_seconds: float = 1.0,
                 session: requests.Session | None = None):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._session = session or requests.Session()

    def get(self, url: str, params: dict) -> dict:
        last_error: WeatherError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(
                    url, params=params, timeout=self._timeout_seconds,
                    headers={"Accept": "application/json"},
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = WeatherError(
                    f"Could not reach Open-Meteo at {url}: {exc}", url=url)
            else:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = WeatherError(
                        f"Open-Meteo returned HTTP {response.status_code} for {url}",
                        url=url, status_code=response.status_code,
                    )
                elif response.status_code >= 400:
                    raise WeatherError(
                        f"Open-Meteo request failed with HTTP "
                        f"{response.status_code} for {url}",
                        url=url, status_code=response.status_code,
                    )
                else:
                    return response.json()
            if attempt < self._max_attempts:
                time.sleep(self._backoff_seconds * 2 ** (attempt - 1))
        assert last_error is not None
        raise last_error


def _to_frame(payload: dict) -> pd.DataFrame:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return pd.DataFrame(columns=list(WEATHER_COLUMNS))
    frame = pd.DataFrame({"start_time": pd.to_datetime(times, utc=True)})
    for source, target in _HOURLY_VARIABLES.items():
        frame[target] = pd.to_numeric(pd.Series(hourly.get(source)),
                                      errors="coerce")
    return frame[list(WEATHER_COLUMNS)].sort_values("start_time",
                                                    ignore_index=True)


def _base_params() -> dict:
    return {
        "hourly": ",".join(_HOURLY_VARIABLES),
        "wind_speed_unit": "ms",   # turbine power curves are stated in m/s
        "timezone": "UTC",         # keep everything on the UTC spine
    }


def fetch_forecast(latitude: float, longitude: float, *, days: int = 7,
                   client: OpenMeteoClient | None = None) -> pd.DataFrame:
    """Hourly weather forecast for a point, out to ``days`` ahead (max 16)."""
    client = client or OpenMeteoClient()
    payload = client.get(FORECAST_URL, {
        **_base_params(),
        "latitude": latitude,
        "longitude": longitude,
        "forecast_days": days,
    })
    return _to_frame(payload)


def fetch_archive(latitude: float, longitude: float, start, end,
                  client: OpenMeteoClient | None = None) -> pd.DataFrame:
    """Hourly ERA5 reanalysis weather for a point over a past date range."""
    client = client or OpenMeteoClient()
    payload = client.get(ARCHIVE_URL, {
        **_base_params(),
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    })
    return _to_frame(payload)
