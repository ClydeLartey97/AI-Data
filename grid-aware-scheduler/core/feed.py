"""
The one place the app asks for market data.

Cache first, live only for what the cache cannot hold. Settled days come off
disk instantly; the last day or two are still forecasts and change, so those
always go to the API.

Everything above this line — pages, charts, the scheduler — asks ``load()``
and never touches an adapter directly. That keeps the "which market" decision
in one place, and it means the range selector can ask ``extent()`` what data
actually exists rather than offering 1Y over three weeks of history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adapters.base_adapter import GridDataPoint
from adapters.gb import GBAdapter
from core import store

_ADAPTERS = {"GB": GBAdapter}


def extent(market: str = "GB") -> tuple[datetime, datetime] | None:
    """Oldest and newest data available, cache included."""
    return store.extent(market)


def load(start: datetime, end: datetime, market: str = "GB",
         *, live_tail: bool = True) -> list[GridDataPoint]:
    """Series for a window, served from cache with a live tail.

    ``live_tail`` fetches the unsettled recent days from the API. Turn it off
    for backtests, where reproducibility matters more than freshness.
    """
    start, end = _utc(start), _utc(end)
    rows = store.read(market, start, end)
    points = [GridDataPoint(timestamp=r.timestamp, carbon_intensity=r.carbon, price=r.price)
              for r in rows]

    if live_tail:
        cutoff = store.settled_cutoff()
        tail_from = max(start, _utc(datetime.combine(cutoff, datetime.min.time())))
        if end > tail_from:
            try:
                adapter = _ADAPTERS.get(market, GBAdapter)()
                fresh = adapter.get_data(tail_from, end)
                seen = {p.timestamp for p in points}
                points += [p for p in fresh if p.timestamp not in seen]
            except Exception:
                # An unreachable API must degrade to cached history, not to a
                # blank page. The UI reports the extent it actually has.
                pass

    points.sort(key=lambda p: p.timestamp)
    return points


def load_days(days: int, market: str = "GB", **kw) -> list[GridDataPoint]:
    end = datetime.now(timezone.utc)
    return load(end - timedelta(days=days), end, market, **kw)


def coverage(market: str = "GB") -> dict:
    s = store.stats(market)
    ext = extent(market)
    s["span_days"] = (ext[1] - ext[0]).days if ext else 0
    return s


def _utc(v: datetime) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
