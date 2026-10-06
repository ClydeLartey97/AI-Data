"""Per-BM-Unit physical data (PN, QPN, MELS, MILS) from ``/balancing/physical``.

Half-hourly level segments, windowed by ``from``/``to``; each row is a
``levelFrom`` → ``levelTo`` ramp over ``timeFrom`` → ``timeTo``.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from ._frames import format_utc, to_frame
from .client import ElexonClient

# Datasets that must be named in the request (the dynamic and rates endpoints
# instead return everything for a unit; their datasets are listed above).
PHYSICAL_DATASETS = ("PN", "QPN", "MELS", "MILS")

PHYSICAL_COLUMNS = (
    "dataset",
    "settlement_date",
    "settlement_period",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "national_grid_bm_unit",
    "bm_unit",
)

def _utc(moment: date, at: time) -> datetime:
    return datetime.combine(moment, at, tzinfo=timezone.utc)


def fetch_physical(
    bm_unit: str,
    dataset: str,
    settlement_date: date,
    *,
    client: ElexonClient | None = None,
) -> pd.DataFrame:
    """Physical level segments (PN/QPN/MELS/MILS) for one unit and day.

    The endpoint filters on wall-clock time, and GB settlement days do not line
    up with UTC days, so the window is widened a day on each side and the result
    filtered back to the requested settlement date. One row per half-hour
    segment, sorted by period.
    """
    if dataset not in PHYSICAL_DATASETS:
        raise ValueError(f"{dataset!r} is not a physical dataset {PHYSICAL_DATASETS}")
    client = client or ElexonClient()
    payload = client.get(
        "balancing/physical",
        {
            "bmUnit": bm_unit,
            "dataset": dataset,
            "from": format_utc(_utc(settlement_date - timedelta(days=1), time.min)),
            "to": format_utc(_utc(settlement_date + timedelta(days=1), time.max)),
        },
    )
    frame = to_frame(payload.get("data", []), PHYSICAL_COLUMNS)
    if frame.empty:
        return frame
    frame = frame[frame["settlement_date"] == settlement_date]
    return frame.sort_values(["settlement_period", "time_from"], ignore_index=True)


