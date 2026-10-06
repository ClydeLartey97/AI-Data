"""Fetchers for per-BM-Unit Physical and Dynamic data.

Elexon serves these through three dedicated endpoints, not the generic dataset
feed, and each has a different shape:

  * ``/balancing/physical`` — half-hourly level segments (PN, QPN, MELS, MILS).
    Windowed by ``from``/``to``; each row is a ``levelFrom`` → ``levelTo`` ramp
    over ``timeFrom`` → ``timeTo``.
  * ``/balancing/dynamic`` — point-in-time dynamic parameters (SEL, SIL, MZT,
    MNZT, NDZ, NTB, NTO). A *snapshot*: one value per parameter in force at an
    instant (``snapshotAt``), not a time window.
  * ``/balancing/dynamic/rates`` — run-up / run-down rate curves (RURE, RURI,
    RDRE, RDRI). Also a snapshot; each row is a multi-segment rate curve.

Maximum-delivery parameters (MDP, MDV) come from the generic dataset feed and
are very sparsely populated. FPN, MDO and MDB are listed by Elexon but have no
working per-unit endpoint on the Insights API (see the notes in the app).

Every fetcher takes a BM Unit id and returns a tidy frame on the settlement
spine, so per-unit data joins cleanly with the rest of the app.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from ._frames import format_utc, to_frame
from .client import ElexonClient

# Datasets that must be named in the request (the dynamic and rates endpoints
# instead return everything for a unit; their datasets are listed above).
PHYSICAL_DATASETS = ("PN", "QPN", "MELS", "MILS")
MAX_DELIVERY_DATASETS = ("MDP", "MDV")

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

DYNAMIC_COLUMNS = (
    "dataset",
    "bm_unit",
    "national_grid_bm_unit",
    "time",
    "value",
    "settlement_date",
    "settlement_period",
)

RATES_COLUMNS = (
    "dataset",
    "settlement_date",
    "settlement_period",
    "time",
    "rate1",
    "elbow2",
    "rate2",
    "elbow3",
    "rate3",
    "national_grid_bm_unit",
    "bm_unit",
)

MAX_DELIVERY_COLUMNS = (
    "dataset",
    "settlement_date",
    "settlement_period",
    "time",
    "period_max",
    "volume_max",
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


def fetch_dynamic(
    bm_unit: str, snapshot_at: datetime, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Dynamic parameters (SEL, SIL, MZT, MNZT, NDZ, NTB, NTO) for one unit.

    A snapshot: the value of each parameter in force at ``snapshot_at``. One row
    per parameter present for the unit, sorted by dataset code.
    """
    client = client or ElexonClient()
    payload = client.get(
        "balancing/dynamic",
        {"bmUnit": bm_unit, "snapshotAt": format_utc(snapshot_at)},
    )
    frame = to_frame(payload.get("data", []), DYNAMIC_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values("dataset", ignore_index=True)


def fetch_dynamic_rates(
    bm_unit: str, snapshot_at: datetime, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Run-up / run-down rate curves (RURE, RURI, RDRE, RDRI) for one unit.

    A snapshot at ``snapshot_at``. Each row is a rate curve: ``rate1`` up to
    ``elbow2``, then ``rate2`` up to ``elbow3``, then ``rate3`` (MW/min).
    """
    client = client or ElexonClient()
    payload = client.get(
        "balancing/dynamic/rates",
        {"bmUnit": bm_unit, "snapshotAt": format_utc(snapshot_at)},
    )
    frame = to_frame(payload.get("data", []), RATES_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values("dataset", ignore_index=True)


def fetch_max_delivery(
    bm_unit: str, settlement_date: date, *, client: ElexonClient | None = None
) -> pd.DataFrame:
    """Maximum-delivery parameters (MDP period, MDV volume) for one unit and day.

    These come from the generic dataset feed and are very sparsely populated —
    most units and days return nothing. Rows for both datasets are combined and
    filtered to the requested unit and settlement date.
    """
    client = client or ElexonClient()
    frames = []
    for dataset in MAX_DELIVERY_DATASETS:
        payload = client.get(
            f"datasets/{dataset}",
            {
                "from": format_utc(_utc(settlement_date, time.min)),
                "to": format_utc(_utc(settlement_date, time.max)),
            },
        )
        frames.append(to_frame(payload.get("data", []), MAX_DELIVERY_COLUMNS))
    frame = pd.concat(frames, ignore_index=True)
    if frame.empty:
        return frame
    frame = frame[
        (frame["bm_unit"] == bm_unit) & (frame["settlement_date"] == settlement_date)
    ]
    return frame.sort_values(["dataset", "settlement_period"], ignore_index=True)
