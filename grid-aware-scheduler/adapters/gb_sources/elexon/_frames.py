"""Shared helpers for turning Insights API records into tidy DataFrames.

Column names are converted from the API's camelCase to snake_case, timestamp
columns are parsed to timezone-aware UTC, and settlement dates become plain
``datetime.date`` values.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

import pandas as pd

# Timestamp columns seen across the Insights datasets, snake_cased. Any column
# named here is parsed to timezone-aware UTC when present in a frame.
_TIMESTAMP_COLUMNS = (
    "start_time",
    "created_date_time",
    "created_time",
    "publish_time",
    "time_from",
    "time_to",
    "time",
    "acceptance_time",
    "measurement_time",
)

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def to_snake_case(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def format_utc(moment: datetime) -> str:
    """Render a datetime as the ``YYYY-MM-DDTHH:MM:SSZ`` the API expects."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_frame(records: list[dict], fallback_columns: Sequence[str]) -> pd.DataFrame:
    """Build a tidy frame from API records, or an empty one with known columns."""
    if not records:
        return pd.DataFrame(columns=list(fallback_columns))
    frame = pd.DataFrame.from_records(records)
    frame.columns = [to_snake_case(column) for column in frame.columns]
    for column in _TIMESTAMP_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True)
    if "settlement_date" in frame.columns:
        frame["settlement_date"] = pd.to_datetime(frame["settlement_date"]).dt.date
    return frame
