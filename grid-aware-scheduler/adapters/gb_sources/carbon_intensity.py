"""Thin HTTP client for the Carbon Intensity API (carbonintensity.org.uk).

Free and keyless.

API docs: https://carbon-intensity.github.io/api-definitions/
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

DEFAULT_BASE_URL = "https://api.carbonintensity.org.uk"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Tidy columns the historical fetcher emits (half-hourly, on the settlement
# spine so it warehouses alongside the Elexon feeds).
EMISSIONS_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "forecast_gco2_kwh",
    "actual_gco2_kwh",
    "index",
)


class CarbonIntensityError(RuntimeError):
    """Raised when the Carbon Intensity API cannot be reached or errors."""

    def __init__(self, message: str, *, url: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class CarbonIntensityClient:
    """Small wrapper around ``requests`` with retries and consistent errors,
    mirroring ``elexon.client.ElexonClient`` so callers feel the same."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        session: requests.Session | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._session = session or requests.Session()

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        last_error: CarbonIntensityError | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self._timeout_seconds,
                    headers={"Accept": "application/json"},
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = CarbonIntensityError(
                    f"Could not reach the Carbon Intensity API at {url}: {exc}", url=url
                )
            else:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = CarbonIntensityError(
                        f"Carbon Intensity API returned HTTP {response.status_code} for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                elif response.status_code >= 400:
                    raise CarbonIntensityError(
                        f"Carbon Intensity API request failed with HTTP {response.status_code} for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        # A handful of historical days serve malformed JSON.
                        # Surface it as this source's own error so callers
                        # (pages, backfill) handle it like any other source
                        # failure instead of crashing on a raw decode error.
                        raise CarbonIntensityError(
                            f"Carbon Intensity API returned invalid JSON for {url}",
                            url=url,
                        ) from exc

            if attempt < self._max_attempts:
                time.sleep(self._backoff_seconds * 2 ** (attempt - 1))

        assert last_error is not None
        raise last_error


def fetch_intensity_for_date(
    settlement_date: date, *, client: CarbonIntensityClient | None = None
) -> pd.DataFrame:
    """National carbon intensity for one settlement date, half-hourly (gCO2/kWh).

    One row per settlement period, carrying the forecast and settled ``actual``
    intensity and the qualitative index. Shaped on the settlement spine so it
    warehouses alongside the Elexon feeds. Returns whatever periods the API
    holds — usually 48, fewer on days with a data gap — and an empty frame
    before the API's history begins (around September 2017).
    """
    client = client or CarbonIntensityClient()
    rows = client.get(f"intensity/date/{settlement_date.isoformat()}").get("data", [])
    if not rows:
        return pd.DataFrame(columns=list(EMISSIONS_COLUMNS))
    frame = pd.DataFrame(
        {
            "settlement_date": settlement_date,
            "settlement_period": range(1, len(rows) + 1),
            "start_time": pd.to_datetime([r.get("from") for r in rows], utc=True),
            "forecast_gco2_kwh": [r.get("intensity", {}).get("forecast") for r in rows],
            "actual_gco2_kwh": [r.get("intensity", {}).get("actual") for r in rows],
            "index": [r.get("intensity", {}).get("index") for r in rows],
        }
    )
    return frame[list(EMISSIONS_COLUMNS)]
