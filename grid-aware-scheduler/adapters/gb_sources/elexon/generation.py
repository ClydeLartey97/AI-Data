"""Actual generation per BM Unit (B1610), as a tidy pandas DataFrame."""
from __future__ import annotations

from datetime import date

import pandas as pd

from ._frames import to_frame
from .client import ElexonClient

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
