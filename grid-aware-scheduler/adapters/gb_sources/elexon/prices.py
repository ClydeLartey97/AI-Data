"""Fetchers for the Prices category, returning tidy pandas DataFrames.

Column names are converted from the API's camelCase to snake_case so they
read naturally in pandas, timestamps are parsed to timezone-aware UTC, and
settlement dates become plain ``datetime.date`` values.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from ._frames import to_frame
from .client import ElexonClient

# Columns served by /datasets/MID, snake_cased.
MARKET_INDEX_COLUMNS = (
    "dataset",
    "start_time",
    "data_provider",
    "settlement_date",
    "settlement_period",
    "price",
    "volume",
)

def fetch_market_index_range(
    start: date,
    end: date,
    *,
    data_providers: Sequence[str] | None = None,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Market Index Data (MID) over an inclusive settlement-date range, for the
    warehouse backfill. The endpoint filters on start time and caps the window
    at 7 days, and the window is widened a day on each side (GB settlement days
    don't align with UTC), so callers must keep the range to ≤5 days per call.
    Rows are filtered back to ``[start, end]``."""
    client = client or ElexonClient()
    params: dict = {
        "from": (start - timedelta(days=1)).isoformat(),
        "to": (end + timedelta(days=1)).isoformat(),
    }
    if data_providers:
        params["dataProviders"] = list(data_providers)
    payload = client.get("datasets/MID", params=params)
    frame = to_frame(payload.get("data", []), MARKET_INDEX_COLUMNS)
    if frame.empty:
        return frame
    frame = frame[
        (frame["settlement_date"] >= start) & (frame["settlement_date"] <= end)
    ]
    if frame.empty:
        return frame
    return frame.sort_values(
        ["settlement_date", "data_provider", "settlement_period"], ignore_index=True
    )
