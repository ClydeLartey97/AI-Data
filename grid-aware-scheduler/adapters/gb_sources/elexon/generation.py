"""Fetchers for the Generation category, returning tidy pandas DataFrames.

Covers the fuel-mix feeds (FUELINST, FUELHH), the ETR-format per-type series
(AGPT, AGWS), and interconnector flows, which Elexon publishes as INT* fuel
types within FUELINST rather than as a separate dataset.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ._frames import to_frame
from .client import ElexonClient

# Columns served by /datasets/FUELINST and /datasets/FUELHH, snake_cased.
FUEL_MIX_COLUMNS = (
    "dataset",
    "publish_time",
    "start_time",
    "settlement_date",
    "settlement_period",
    "fuel_type",
    "generation",
)

# Columns served by /datasets/B1610 (actual generation per unit), snake_cased.
ACTUAL_PER_UNIT_COLUMNS = (
    "dataset",
    "psr_type",
    "bm_unit",
    "national_grid_bm_unit_id",
    "settlement_date",
    "settlement_period",
    "half_hour_end_time",
    "quantity",
)

# Columns served by /datasets/AGPT and /datasets/AGWS (ETR format), snake_cased.
GENERATION_BY_TYPE_COLUMNS = (
    "dataset",
    "document_id",
    "document_revision_number",
    "publish_time",
    "business_type",
    "psr_type",
    "quantity",
    "start_time",
    "settlement_date",
    "settlement_period",
)


def _format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_fuel_mix_instant(
    published_from: datetime,
    published_to: datetime,
    *,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Instantaneous fuel mix (FUELINST) over a publish-time window.

    One row per fuel type per five-minute spot value, sorted by time then
    fuel type. Interconnectors appear as INT* fuel types.
    """
    client = client or ElexonClient()
    payload = client.get(
        "datasets/FUELINST",
        params={
            "publishDateTimeFrom": _format_utc(published_from),
            "publishDateTimeTo": _format_utc(published_to),
        },
    )
    frame = to_frame(payload.get("data", []), FUEL_MIX_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["start_time", "fuel_type"], ignore_index=True)


def fetch_fuel_mix_half_hourly(
    settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Half-hourly fuel mix (FUELHH) for one settlement date.

    One row per fuel type per settlement period, sorted by period then fuel
    type.
    """
    client = client or ElexonClient()
    payload = client.get(
        "datasets/FUELHH",
        params={
            "settlementDateFrom": settlement_date.isoformat(),
            "settlementDateTo": settlement_date.isoformat(),
        },
    )
    frame = to_frame(payload.get("data", []), FUEL_MIX_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["settlement_period", "fuel_type"], ignore_index=True)


def fetch_fuel_mix_half_hourly_range(
    start: date, end: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Half-hourly fuel mix (FUELHH) over an inclusive settlement-date range,
    for the warehouse backfill. The endpoint caps the window at 7 days, so
    callers keep the range to ≤7 days per call."""
    client = client or ElexonClient()
    payload = client.get(
        "datasets/FUELHH",
        params={
            "settlementDateFrom": start.isoformat(),
            "settlementDateTo": end.isoformat(),
        },
    )
    frame = to_frame(payload.get("data", []), FUEL_MIX_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(
        ["settlement_date", "settlement_period", "fuel_type"], ignore_index=True
    )


# Columns served by /datasets/B1610/stream.
ACTUAL_OUTTURN_COLUMNS = (
    "settlement_date", "settlement_period", "half_hour_end_time",
    "bm_unit", "psr_type", "quantity",
)


def fetch_actual_outturn(
    settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Actual per-unit metered generation outturn (B1610) for one settlement
    date. One row per unit per period; ``quantity`` can be negative (e.g. a
    storage unit charging).

    Settles on a real lag — recent dates return empty until the settlement
    run catches up, typically several weeks later. That's expected, not an
    error: it just means this date hasn't been through settlement yet.
    """
    client = client or ElexonClient()
    payload = client.get(
        "datasets/B1610/stream",
        params={
            "from": f"{settlement_date.isoformat()}T00:00:00Z",
            "to": f"{settlement_date.isoformat()}T23:59:59Z",
        },
    )
    records = payload if isinstance(payload, list) else payload.get("data", [])
    frame = to_frame(records, ACTUAL_OUTTURN_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.reindex(columns=list(ACTUAL_OUTTURN_COLUMNS))
    return frame.sort_values(["settlement_period", "bm_unit"], ignore_index=True)


def fetch_actual_outturn_range(
    start: date, end: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Actual per-unit metered generation (B1610) over an inclusive settlement-
    date range, in one ``/stream`` call — the warehouse-backfill shape. The
    ``/stream`` endpoint has no 7-day cap, so a multi-day window is a single
    request. Recent dates within the range may be empty until settlement
    catches up (see :func:`fetch_actual_outturn`); that is expected, not an
    error."""
    client = client or ElexonClient()
    payload = client.get(
        "datasets/B1610/stream",
        params={
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
    )
    records = payload if isinstance(payload, list) else payload.get("data", [])
    frame = to_frame(records, ACTUAL_OUTTURN_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.reindex(columns=list(ACTUAL_OUTTURN_COLUMNS))
    frame = frame[
        (frame["settlement_date"] >= start) & (frame["settlement_date"] <= end)
    ]
    if frame.empty:
        return frame
    return frame.sort_values(
        ["settlement_date", "settlement_period", "bm_unit"], ignore_index=True
    )


def fetch_interconnector_flows(
    published_from: datetime,
    published_to: datetime,
    *,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Interconnector flows over a publish-time window.

    There is no separate INT* dataset: flows are the INT-prefixed fuel types
    within FUELINST. Positive generation is an import into GB.
    """
    frame = fetch_fuel_mix_instant(published_from, published_to, client=client)
    if frame.empty:
        return frame
    return frame[frame["fuel_type"].str.startswith("INT")].reset_index(drop=True)


def _fetch_per_type_dataset(
    dataset: str, settlement_date: date, client: ElexonClient | None
) -> pd.DataFrame:
    """Shared logic for the ETR per-type datasets (AGPT, AGWS).

    These publish shortly after each period and can be revised, so the
    publish window is widened around the settlement date, the result is
    filtered back to it, and only the latest revision of each
    (period, psr type) value is kept.
    """
    client = client or ElexonClient()
    window_start = datetime.combine(settlement_date, datetime.min.time(), timezone.utc)
    payload = client.get(
        f"datasets/{dataset}",
        params={
            "publishDateTimeFrom": _format_utc(window_start - timedelta(hours=12)),
            "publishDateTimeTo": _format_utc(window_start + timedelta(hours=36)),
        },
    )
    frame = to_frame(payload.get("data", []), GENERATION_BY_TYPE_COLUMNS)
    if frame.empty:
        return frame
    frame = frame[frame["settlement_date"] == settlement_date]
    frame = frame.sort_values("publish_time").drop_duplicates(
        subset=["settlement_date", "settlement_period", "psr_type"], keep="last"
    )
    return frame.sort_values(["settlement_period", "psr_type"], ignore_index=True)


def fetch_generation_by_type(
    settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Actual aggregated generation per type (AGPT / B1620) for one date."""
    return _fetch_per_type_dataset("AGPT", settlement_date, client)


def fetch_wind_solar(
    settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Actual or estimated wind and solar generation (AGWS / B1630) for one date."""
    return _fetch_per_type_dataset("AGWS", settlement_date, client)


def fetch_actual_generation_per_unit(
    settlement_date: date,
    settlement_period: int,
    *,
    bm_unit: str | None = None,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Actual metered generation per BM Unit (B1610) for one settlement period.

    B1610 is ex-post: it settles with a lag (recent days are not published
    yet), and the Insights feed serves it a single period at a time, returning
    every unit for that period. Pass ``bm_unit`` to filter to one unit. Sorted
    by output, highest first. Empty frame if the period is not yet settled.
    """
    client = client or ElexonClient()
    params: dict = {
        "settlementDate": settlement_date.isoformat(),
        "settlementPeriod": settlement_period,
    }
    if bm_unit:
        params["bmUnit"] = bm_unit
    payload = client.get("datasets/B1610", params=params)
    frame = to_frame(payload.get("data", []), ACTUAL_PER_UNIT_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values("quantity", ascending=False, ignore_index=True)
