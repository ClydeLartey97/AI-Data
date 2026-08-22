"""
GB market adapter — the corrected implementation.

This supersedes ``gb_adapter.py``, which is kept unchanged as the historical
first pass. The difference is the price path, and it matters:

``gb_adapter.py`` averages Market Index price across every data provider that
reports for a settlement period. Only two providers exist, and ``N2EXMIDP``
publishes structural zeros — 100% zero across Aug/Jun/Feb 2026, 99.6% zero in
Nov 2025 (measured, not assumed). Averaging therefore halves every price, and
in the 99.6% case halves *most* periods but not all, which distorts the shape
of the price curve rather than just its level. Shape is the entire input to a
"which half-hour is cheapest" decision, so that is a correctness bug, not a
cosmetic one.

Here, price comes from a single preferred provider (``APXMIDP``), with
non-positive prices dropped rather than averaged in. If the preferred provider
is missing for a period, that period reports no price rather than silently
falling back to a zero-contaminated mean.

Signal choice is unchanged and deliberate: forecast carbon (not settled
actual) and day-ahead Market Index price (not settlement system price),
because a scheduler deciding *now* only ever has forward-looking data. The
settled values are for scoring a schedule after the fact, never for making it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from adapters import national_grid_tool
from adapters.base_adapter import GridDataPoint, MarketAdapter

# Elexon's MID endpoint caps its window at 7 days and widens it a day either
# side, so callers must keep each request to <=5 days.
_MID_CHUNK_DAYS = 5

#: Market Index data provider to trust. See the module docstring for why this
#: is a single preferred provider rather than a mean across all of them.
PREFERRED_PRICE_PROVIDER = "APXMIDP"


@dataclass
class GBPoint(GridDataPoint):
    """A GB half-hour, carrying the provenance the plain GridDataPoint omits.

    ``settled_carbon`` is the *actual* outturn where it exists. It is recorded
    for scoring a schedule after the fact and must never feed a scheduling
    decision — doing so leaks information the scheduler could not have had.
    """

    settlement_period: int = 0
    price_provider: str | None = None
    settled_carbon: float | None = None
    extras: dict = field(default_factory=dict)


def _fetch_carbon_range(start_date: date, end_date: date) -> pd.DataFrame:
    fetch_intensity_for_date, = national_grid_tool.load(
        "sources.carbon_intensity.client", "fetch_intensity_for_date")
    frames, day = [], start_date
    while day <= end_date:
        frames.append(fetch_intensity_for_date(day))
        day += timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _fetch_price_range(start_date: date, end_date: date) -> pd.DataFrame:
    fetch_market_index_range, = national_grid_tool.load(
        "sources.elexon.prices", "fetch_market_index_range")
    frames, day = [], start_date
    while day <= end_date:
        chunk_end = min(day + timedelta(days=_MID_CHUNK_DAYS - 1), end_date)
        frames.append(fetch_market_index_range(day, chunk_end))
        day = chunk_end + timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _clean_price(price: pd.DataFrame, provider: str) -> pd.DataFrame:
    """Reduce provider-level MID rows to one price per settlement period.

    Prefers ``provider``; drops non-positive prices, which are how a
    non-reporting provider shows up in this feed. Never averages across
    providers — see the module docstring.
    """
    columns = ["settlement_date", "settlement_period", "price", "price_provider"]
    if price.empty:
        return pd.DataFrame(columns=columns)

    usable = price[price["price"] > 0]
    preferred = usable[usable["data_provider"] == provider]
    chosen = preferred if not preferred.empty else usable
    if chosen.empty:
        return pd.DataFrame(columns=columns)

    # One row per period. If the preferred provider somehow double-reports,
    # take the first rather than blending.
    chosen = chosen.sort_values(["settlement_date", "settlement_period", "data_provider"])
    chosen = chosen.groupby(["settlement_date", "settlement_period"], as_index=False).first()
    return chosen.rename(columns={"data_provider": "price_provider"})[columns]


class GBAdapter(MarketAdapter):
    """GB electricity market: Carbon Intensity API + Elexon Insights MID."""

    def __init__(self, price_provider: str = PREFERRED_PRICE_PROVIDER) -> None:
        self.price_provider = price_provider

    @property
    def market_name(self) -> str:
        return "GB"

    @property
    def currency(self) -> str:
        return "GBP"

    def get_data(self, start: datetime, end: datetime) -> list[GBPoint]:
        start_ts, end_ts = _utc(start), _utc(end)

        carbon = _fetch_carbon_range(start_ts.date(), end_ts.date())
        if carbon.empty:
            return []

        price = _clean_price(_fetch_price_range(start_ts.date(), end_ts.date()),
                             self.price_provider)

        merged = carbon.merge(price, on=["settlement_date", "settlement_period"], how="left")
        merged = merged[(merged["start_time"] >= start_ts) & (merged["start_time"] <= end_ts)]
        merged = merged.sort_values("start_time")

        return [
            GBPoint(
                timestamp=row.start_time.to_pydatetime(),
                carbon_intensity=_number(row.forecast_gco2_kwh),
                price=_number(row.price),
                settlement_period=int(row.settlement_period),
                price_provider=row.price_provider if pd.notna(row.price_provider) else None,
                settled_carbon=_number(getattr(row, "actual_gco2_kwh", None)),
            )
            for row in merged.itertuples(index=False)
        ]


def _utc(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _number(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
