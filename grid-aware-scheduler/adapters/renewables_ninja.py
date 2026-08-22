"""Renewables.ninja — reference simulated generation for a declared plant.

`core/renewables.py` models a plant's output locally, from Open-Meteo
irradiance and hub-height wind, because a simulator that recomputes on a slider
drag cannot wait on a rate-limited API. Its own docstring states the cost of
that choice plainly: no bias correction against metered output, a generic
turbine curve, no shading or downtime, and irradiance treated as plane-of-array.
"Expect the shape to be right and the level to be optimistic by some margin."

**That margin is the problem this module exists to measure.** A site scheduling
its heaviest work into the window its own plant produces most is making a
*shape* claim, and a shape claim can be checked. Renewables.ninja converts
MERRA-2 reanalysis into hourly simulated output using real turbine power curves
and bias correction validated against metered national output, so it is the
reference the local model can be scored against.

**It is historical, and this module refuses to pretend otherwise.** MERRA-2 is
reanalysis with a publication lag, so there is no forecast here and a future
date is rejected rather than silently clamped. The division of labour is
therefore fixed: Renewables.ninja calibrates the local model over history, and
the calibrated local model runs forward on Open-Meteo forecasts at decision
time. Using this to schedule tomorrow would be using a rear-view mirror.

Needs a free personal token in ``RENEWABLES_NINJA_TOKEN`` (register at
renewables.ninja). Rate limits are the account's own published figures: 50
requests an hour, one a second. Responses are cached on disk, because burning
an hourly allowance re-fetching a year you already have is the obvious way to
make this unusable.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_URL = "https://www.renewables.ninja/api"
TOKEN_ENV_VAR = "RENEWABLES_NINJA_TOKEN"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: The account's own published limits, from the API's /limits endpoint.
HOURLY_LIMIT = 50
MIN_REQUEST_INTERVAL_SECONDS = 1.05

#: The span MERRA-2 can actually simulate. Reanalysis is published in arrears,
#: so the upper bound moves forward roughly a year behind the calendar. Stated
#: as a constant rather than derived from "now", because guessing the lag would
#: turn a clear rejection into an empty result that looks like a quiet plant.
MERRA2_EARLIEST = date(1980, 1, 1)
MERRA2_LATEST = date(2025, 12, 31)

#: One request per day of simulation is wasteful; the API accepts a range. A
#: year in one call is fine, and keeps a site validation inside one allowance.
MAX_SPAN_DAYS = 366

_PV_DEFAULTS = {"dataset": "merra2", "capacity": 1.0, "system_loss": 0.1,
                "tracking": 0, "tilt": 35, "azim": 180}
_WIND_DEFAULTS = {"dataset": "merra2", "capacity": 1.0, "height": 100,
                  "turbine": "Vestas V90 2000"}

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_STARTED = 0.0
_CALL_LOG: list[float] = []


class NinjaError(RuntimeError):
    """Renewables.ninja could not be reached or returned an error."""


class NinjaAuthError(NinjaError):
    """No token, or the token was rejected."""


class NinjaDateError(NinjaError, ValueError):
    """A date outside what MERRA-2 can simulate — including any future date."""


@dataclass(frozen=True)
class SimulatedPoint:
    """One hour of simulated output, as a fraction of rated capacity."""

    timestamp: datetime
    capacity_factor: float


def has_token() -> bool:
    return bool(os.environ.get(TOKEN_ENV_VAR))


def usage() -> dict:
    """How much of the hourly allowance this process has spent."""
    cutoff = time.time() - 3600
    recent = [t for t in _CALL_LOG if t >= cutoff]
    return {"calls_last_hour": len(recent), "hourly_limit": HOURLY_LIMIT,
            "remaining": max(0, HOURLY_LIMIT - len(recent))}


def _check_dates(start: date, end: date) -> None:
    if end < start:
        raise NinjaDateError(f"end {end} is before start {start}")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise NinjaDateError(
            f"{(end - start).days + 1} days requested; the cap is {MAX_SPAN_DAYS}")
    today = datetime.now(timezone.utc).date()
    if start > today or end > today:
        raise NinjaDateError(
            "Renewables.ninja simulates MERRA-2 reanalysis, which is historical. "
            "It has no forecast, so a future date cannot be simulated. Use the "
            "Open-Meteo forecast through core.renewables for forward-looking "
            "generation, and use this to calibrate that model over history.")
    if start < MERRA2_EARLIEST or end > MERRA2_LATEST:
        raise NinjaDateError(
            f"MERRA-2 covers {MERRA2_EARLIEST} to {MERRA2_LATEST}; "
            f"{start} to {end} falls outside it. Reanalysis is published in "
            "arrears, so the most recent months are never available.")


def _cache_path(kind: str, params: dict, cache_dir: Path) -> Path:
    key = hashlib.sha256(
        json.dumps({"kind": kind, **params}, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    return cache_dir / f"{kind}-{key}.json"


def _to_points(payload: dict) -> list[SimulatedPoint]:
    """Parse the API's ``data`` object into ordered hourly points.

    Keys are millisecond epochs as strings. ``electricity`` is in kW against
    the requested capacity, so at the capacity of 1.0 this module always sends,
    it is already a capacity factor.
    """
    data = payload.get("data") or {}
    points = []
    for raw_key, row in data.items():
        value = (row or {}).get("electricity")
        if value is None:
            continue
        try:
            stamp = datetime.fromtimestamp(int(raw_key) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        points.append(SimulatedPoint(stamp, max(0.0, min(1.0, float(value)))))
    points.sort(key=lambda p: p.timestamp)
    return points


class RenewablesNinjaClient:
    """Token-authenticated Renewables.ninja access, rate-limited and cached."""

    def __init__(self, *, token: str | None = None, timeout_seconds: float = 60.0,
                 max_attempts: int = 3, backoff_seconds: float = 2.0,
                 cache_dir: Path | None = None,
                 session: requests.Session | None = None) -> None:
        self._token = token or os.environ.get(TOKEN_ENV_VAR)
        if not self._token:
            raise NinjaAuthError(
                f"No Renewables.ninja token. Set {TOKEN_ENV_VAR} — registration "
                "is free at renewables.ninja and takes a minute.")
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._cache_dir = cache_dir or (
            Path(__file__).resolve().parents[1] / "data" / "cache" / "ninja")
        self._session = session or requests.Session()

    def _get(self, endpoint: str, params: dict) -> dict:
        global _LAST_REQUEST_STARTED
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        headers = {"Authorization": f"Token {self._token}",
                   "Accept": "application/json"}
        last_error: NinjaError | None = None

        for attempt in range(1, self._max_attempts + 1):
            # One request per second, coordinated across every client in this
            # process. The network wait happens outside the lock so responses
            # may overlap while starts stay a second apart.
            with _REQUEST_LOCK:
                wait = MIN_REQUEST_INTERVAL_SECONDS - (
                    time.monotonic() - _LAST_REQUEST_STARTED)
                if wait > 0:
                    time.sleep(wait)
                _CALL_LOG.append(time.time())
                _LAST_REQUEST_STARTED = time.monotonic()
            try:
                response = self._session.get(url, params=params, headers=headers,
                                             timeout=self._timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = NinjaError(f"Could not reach Renewables.ninja: {exc}")
            else:
                if response.status_code in (401, 403):
                    raise NinjaAuthError(
                        f"Renewables.ninja rejected the token "
                        f"(HTTP {response.status_code}).")
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = NinjaError(
                        f"Renewables.ninja returned HTTP {response.status_code}. "
                        f"The account allows {HOURLY_LIMIT} requests an hour; "
                        f"this process has used {usage()['calls_last_hour']}.")
                elif response.status_code >= 400:
                    raise NinjaError(
                        f"Renewables.ninja request failed with HTTP "
                        f"{response.status_code}: {response.text[:200]}")
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        last_error = NinjaError(f"Invalid JSON from Renewables.ninja: {exc}")
                    else:
                        if isinstance(payload, dict):
                            return payload
                        last_error = NinjaError(
                            f"Expected a JSON object, got {type(payload).__name__}.")
            if attempt < self._max_attempts:
                time.sleep(self._backoff_seconds * 2 ** (attempt - 1))
        assert last_error is not None
        raise last_error

    def _simulate(self, kind: str, endpoint: str, latitude: float, longitude: float,
                  start: date, end: date, extra: dict) -> list[SimulatedPoint]:
        _check_dates(start, end)
        params = {"lat": latitude, "lon": longitude,
                  "date_from": start.isoformat(), "date_to": end.isoformat(),
                  "format": "json", "local_time": "false", **extra}
        cached = _cache_path(kind, params, self._cache_dir)
        if cached.is_file():
            try:
                return _to_points(json.loads(cached.read_text()))
            except (ValueError, OSError):
                pass  # a corrupt cache entry is refetched, never raised
        payload = self._get(endpoint, params)
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(payload))
        except OSError:
            pass  # an unwritable cache costs an allowance, not correctness
        return _to_points(payload)

    def solar(self, latitude: float, longitude: float, start: date, end: date,
              *, tilt: float = 35, azimuth: float = 180,
              system_loss: float = 0.1) -> list[SimulatedPoint]:
        """Hourly simulated PV capacity factor for a point."""
        return self._simulate("pv", "data/pv", latitude, longitude, start, end,
                              {**_PV_DEFAULTS, "tilt": tilt, "azim": azimuth,
                               "system_loss": system_loss})

    def wind(self, latitude: float, longitude: float, start: date, end: date,
             *, height: float = 100,
             turbine: str = "Vestas V90 2000") -> list[SimulatedPoint]:
        """Hourly simulated wind capacity factor for a point."""
        return self._simulate("wind", "data/wind", latitude, longitude, start, end,
                              {**_WIND_DEFAULTS, "height": height,
                               "turbine": turbine})
