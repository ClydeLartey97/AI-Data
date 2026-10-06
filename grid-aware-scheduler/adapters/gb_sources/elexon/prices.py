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

# Columns served by /balancing/settlement/system-prices (DISEBSP), snake_cased.
# Used to shape the empty frame when a date has no data published yet.
SYSTEM_PRICES_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "created_date_time",
    "system_sell_price",
    "system_buy_price",
    "bsad_defaulted",
    "price_derivation_code",
    "reserve_scarcity_price",
    "net_imbalance_volume",
    "sell_price_adjustment",
    "buy_price_adjustment",
    "replacement_price",
    "replacement_price_reference_volume",
    "total_accepted_offer_volume",
    "total_accepted_bid_volume",
    "total_adjustment_sell_volume",
    "total_adjustment_buy_volume",
    "total_system_tagged_accepted_offer_volume",
    "total_system_tagged_accepted_bid_volume",
    "total_system_tagged_adjustment_sell_volume",
    "total_system_tagged_adjustment_buy_volume",
)

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

def fetch_system_prices(
    settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """System prices and net imbalance volume (DISEBSP) for one settlement date.

    One row per settlement period (normally 48; 46 or 50 on clock-change
    days), sorted by period. Returns an empty frame if nothing has been
    published for the date yet.
    """
    client = client or ElexonClient()
    payload = client.get(
        f"balancing/settlement/system-prices/{settlement_date.isoformat()}"
    )
    frame = to_frame(payload.get("data", []), SYSTEM_PRICES_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values("settlement_period", ignore_index=True)


def fetch_market_index(
    settlement_date: date,
    *,
    data_providers: Sequence[str] | None = None,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Market Index Data (MID) for one settlement date.

    The API filters MID on start time, and GB settlement days do not line up
    with UTC days, so the request window is widened by a day on each side and
    the result filtered back to the requested settlement date. One row per
    data provider per settlement period, sorted by provider then period.
    """

    client = client or ElexonClient()
    params: dict = {
        "from": (settlement_date - timedelta(days=1)).isoformat(),
        "to": (settlement_date + timedelta(days=1)).isoformat(),
    }
    if data_providers:
        params["dataProviders"] = list(data_providers)
    payload = client.get("datasets/MID", params=params)
    frame = to_frame(payload.get("data", []), MARKET_INDEX_COLUMNS)
    if frame.empty:
        return frame
    frame = frame[frame["settlement_date"] == settlement_date]
    return frame.sort_values(
        ["data_provider", "settlement_period"], ignore_index=True
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
