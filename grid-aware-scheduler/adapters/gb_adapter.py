"""
GB market adapter — wired to Clyde's existing "National Grid Tool" project
(sibling folder: ../National-Grid-Tool), which already has tested, production
clients for two public, keyless UK grid APIs:

- Carbon Intensity API (carbonintensity.org.uk) via
  ``sources/carbon_intensity/client.py``
- Elexon Insights API (data.elexon.co.uk) via ``sources/elexon/client.py`` +
  ``sources/elexon/prices.py``

Both are public and keyless, so this adapter needs zero credentials — that's
deliberate on Clyde's side and is why it was picked as the first market.

Signal choice for scheduling (judgment call, not yet validated on real data —
see HANDOFF.md "Open questions"):

- Carbon: uses ``forecast_gco2_kwh``, not ``actual_gco2_kwh``. A live
  scheduler making a forward decision only ever has the forecast; the
  settled ``actual`` is only known after the fact, so it's kept in
  ``GridDataPoint`` extras for backtesting comparisons only, never used to
  make the scheduling decision itself.
- Price: uses day-ahead Market Index Data (``fetch_market_index_range``),
  because it's published ahead of the delivery day, same reasoning as
  carbon forecast vs. actual. The settlement system price
  (``fetch_system_prices``) is only known after the fact and would leak
  future information into a "should I run this job now" decision — it is
  NOT used here, but is worth pulling in later for scoring how a schedule
  actually performed against realised prices.
- Where a settlement period has multiple Market Index data providers
  (APXMIDP, N2EXMIDP, ...), their prices are averaged. This is a
  simplification worth revisiting once real numbers are in front of us.

This adapter imports the National Grid Tool as a sibling project rather than
duplicating its client code — same reasoning as the rest of this project:
reuse tested infrastructure instead of rebuilding it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# National Grid Tool lives as a sibling folder under the same workspace
# (AI Energy/National-Grid-Tool), not as an installed package. Override with
# the NATIONAL_GRID_TOOL_PATH env var if it's checked out somewhere else.
_DEFAULT_NGT_PATH = Path(__file__).resolve().parents[2] / "National-Grid-Tool"
_NGT_PATH = Path(os.environ.get("NATIONAL_GRID_TOOL_PATH", _DEFAULT_NGT_PATH))
if str(_NGT_PATH) not in sys.path:
    sys.path.insert(0, str(_NGT_PATH))

try:
    from sources.carbon_intensity.client import fetch_intensity_for_date
    from sources.elexon.prices import fetch_market_index_range
except ImportError as exc:  # pragma: no cover - clearer failure than a raw ImportError
    raise ImportError(
        f"Could not import National Grid Tool clients from {_NGT_PATH}. "
        "Set NATIONAL_GRID_TOOL_PATH to point at that project, or check it "
        "out at AI Energy/National-Grid-Tool alongside this project."
    ) from exc

from adapters.base_adapter import MarketAdapter, GridDataPoint

# Elexon's MID endpoint caps its window; the client's own docstring says
# callers should keep each call to <=5 days to stay safely inside that cap.
_MID_CHUNK_DAYS = 5


def _to_utc_timestamp(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _fetch_carbon_range(start_date, end_date) -> pd.DataFrame:
    frames = []
    d = start_date
    while d <= end_date:
        frames.append(fetch_intensity_for_date(d))
        d += timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _fetch_price_range(start_date, end_date) -> pd.DataFrame:
    frames = []
    d = start_date
    while d <= end_date:
        chunk_end = min(d + timedelta(days=_MID_CHUNK_DAYS - 1), end_date)
        frames.append(fetch_market_index_range(d, chunk_end))
        d = chunk_end + timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class GBAdapter(MarketAdapter):
    @property
    def market_name(self) -> str:
        return "GB"

    def get_data(self, start: datetime, end: datetime) -> list[GridDataPoint]:
        start_ts, end_ts = _to_utc_timestamp(start), _to_utc_timestamp(end)
        start_date, end_date = start_ts.date(), end_ts.date()

        carbon = _fetch_carbon_range(start_date, end_date)
        if carbon.empty:
            return []

        price = _fetch_price_range(start_date, end_date)
        price_by_period = (
            price.groupby(["settlement_date", "settlement_period"], as_index=False)["price"]
            .mean()
            if not price.empty
            else pd.DataFrame(columns=["settlement_date", "settlement_period", "price"])
        )

        merged = carbon.merge(
            price_by_period, on=["settlement_date", "settlement_period"], how="left"
        )
        merged = merged[(merged["start_time"] >= start_ts) & (merged["start_time"] <= end_ts)]

        return [
            GridDataPoint(
                timestamp=row.start_time.to_pydatetime(),
                carbon_intensity=row.forecast_gco2_kwh,
                price=row.price,
            )
            for row in merged.itertuples(index=False)
        ]
