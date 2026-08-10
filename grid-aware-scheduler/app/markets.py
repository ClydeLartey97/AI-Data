"""Market and location context shared by the dashboard and planner.

The common scheduler consumes half-hour price and carbon points. This module
owns the important market-specific truth around those points:

* GB price is national while carbon can be selected by grid region.
* CAISO price is selected at a pricing node while carbon is balancing-area.
* NYISO price is selected at one of eleven zones while carbon is balancing-area.

Live feeds do not currently provide a complete 48-hour forward price curve in
both markets. The planner therefore uses a recent 48-hour replay and labels it
as such. That keeps the optimisation exact without claiming a forecast that
does not exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.base_adapter import GridDataPoint
from adapters.caiso import CAISOAdapter, LOCATIONS as CAISO_LOCATIONS
from adapters.gb import GBAdapter
from adapters.gb_regional import GBRegionalAdapter, REGIONS
from adapters.nyiso import NYISOAdapter, LOCATIONS as NYISO_LOCATIONS
from core import feed


@dataclass(frozen=True)
class LocationChoice:
    key: str
    name: str
    detail: str


@dataclass
class MarketContext:
    market_key: str
    market_name: str
    location_key: str
    location_name: str
    series: list[GridDataPoint]
    currency: str
    symbol: str
    price_label: str
    carbon_label: str
    provenance: str
    signal_mode: str
    locations: list[LocationChoice]
    allows_custom_node: bool = False


def market_locations(market: str) -> list[LocationChoice]:
    if market.upper() == "CAISO":
        return [
            LocationChoice(key, loc.name, f"{loc.kind} · {loc.node}")
            for key, loc in CAISO_LOCATIONS.items()
        ]
    if market.upper() == "NYISO":
        return [
            LocationChoice(key, loc.name, f"NYISO zone · {loc.area}")
            for key, loc in NYISO_LOCATIONS.items()
        ]
    return [LocationChoice("national", "GB national", "National carbon and price")] + [
        LocationChoice(key, name, "Regional carbon · national price")
        for key, (_, name) in REGIONS.items()
    ]


def _aligned_hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _join_gb_region(region_key: str, start: datetime,
                    end: datetime) -> list[GridDataPoint]:
    region_id, _ = REGIONS[region_key]
    prices = GBAdapter().get_data(start, end)
    regional = GBRegionalAdapter().forecast(region_id, start)
    carbon = {p.timestamp: p.carbon_forecast for p in regional}
    return [
        GridDataPoint(
            timestamp=p.timestamp,
            carbon_intensity=carbon.get(p.timestamp),
            price=p.price,
        )
        for p in prices
        if start <= p.timestamp < end
    ]


def load_market(market: str = "GB", location: str = "national", *,
                days: int = 400, planner: bool = False,
                now: datetime | None = None) -> MarketContext:
    """Load one truthful market/location view.

    Planner mode is a recent complete replay. Dashboard mode retains the long
    cached GB history for analytics, but uses a seven-day CAISO view because
    OASIS live queries are deliberately bounded.
    """
    current = _aligned_hour(now or datetime.now(timezone.utc))
    key = market.upper()

    if key == "CAISO":
        location_key = location or "sp15"
        adapter = CAISOAdapter(location_key)
        end = current - timedelta(days=2)
        start = end - timedelta(days=2 if planner else min(days, 7))
        series = adapter.get_data(start, end)
        loc = adapter.location
        methods = {getattr(point, "carbon_method", "") for point in series}
        uses_direct_rate = any("consumption CO2 rate" in method for method in methods)
        carbon_description = (
            "EIA-930 published consumption rate"
            if uses_direct_rate
            else "EIA-930 fuel-mix estimate; imports and unclassified other excluded"
        )
        return MarketContext(
            market_key="CAISO",
            market_name="California ISO",
            location_key=location_key,
            location_name=loc.name,
            series=series,
            currency="USD",
            symbol="$",
            price_label=f"Day-ahead LMP · {loc.node}",
            carbon_label=f"CAISO balancing-area carbon · {carbon_description}",
            provenance=("Price: CAISO OASIS day-ahead LMP. Carbon: "
                        f"{carbon_description}."),
            signal_mode="Historical replay · latest complete official interval",
            locations=market_locations("CAISO"),
            allows_custom_node=True,
        )

    if key == "NYISO":
        location_key = location or "nyc"
        adapter = NYISOAdapter(location_key)
        end = current - timedelta(days=2)
        start = end - timedelta(days=2 if planner else min(days, 7))
        series = adapter.get_data(start, end)
        loc = adapter.location
        methods = {getattr(point, "carbon_method", "") for point in series}
        uses_direct_rate = any("consumption CO2 rate" in method for method in methods)
        carbon_description = (
            "EIA-930 published consumption rate"
            if uses_direct_rate
            else "EIA-930 fuel-mix estimate; imports and unclassified other excluded"
        )
        return MarketContext(
            market_key="NYISO",
            market_name="New York ISO",
            location_key=location_key,
            location_name=f"{loc.name} zone",
            series=series,
            currency="USD",
            symbol="$",
            price_label=f"Day-ahead zonal LBMP · {loc.zone}",
            carbon_label=f"NYISO balancing-area carbon · {carbon_description}",
            provenance=("Price: NYISO MIS day-ahead zonal LBMP. Carbon: "
                        f"{carbon_description}."),
            signal_mode="Historical replay · latest complete official interval",
            locations=market_locations("NYISO"),
        )

    if key != "GB":
        raise ValueError(f"unsupported market {market!r}")

    location_key = location if location in REGIONS else "national"
    if planner or location_key != "national":
        end = current - timedelta(hours=1)
        start = end - timedelta(days=2)
        if location_key == "national":
            series = GBAdapter().get_data(start, end)
            location_name = "GB national"
            carbon_label = "National carbon forecast"
        else:
            series = _join_gb_region(location_key, start, end)
            location_name = REGIONS[location_key][1]
            carbon_label = f"{location_name} regional carbon forecast"
        mode = "Historical replay · recent complete market interval"
    else:
        end = current
        series = feed.load(end - timedelta(days=days), end)
        location_name = "GB national"
        carbon_label = "National carbon forecast"
        mode = "Live API plus settled local cache"

    return MarketContext(
        market_key="GB",
        market_name="Great Britain",
        location_key=location_key,
        location_name=location_name,
        series=series,
        currency="GBP",
        symbol="£",
        price_label="National day-ahead Market Index price",
        carbon_label=carbon_label,
        provenance=("Carbon: National Grid ESO Carbon Intensity API. Price: Elexon "
                    "Insights Market Index Data, APXMIDP only."),
        signal_mode=mode,
        locations=market_locations("GB"),
    )


def summarise_market(context: MarketContext, window_hours: float = 4.0
                     ) -> dict[str, Any]:
    """Compact grid context for workload energy estimates.

    The minimum price and carbon values are separate complete contiguous
    windows. They answer two counterfactual objectives and are not presented
    as one simultaneously cheapest-and-cleanest interval.
    """
    series = context.series
    width = max(1, round(window_hours * 2))

    def latest(attribute: str) -> float | None:
        return next(
            (getattr(point, attribute) for point in reversed(series)
             if getattr(point, attribute) is not None),
            None,
        )

    def best(attribute: str) -> float | None:
        values: list[float] = []
        for start in range(len(series) - width + 1):
            rows = series[start:start + width]
            if not all(
                b.timestamp - a.timestamp == timedelta(minutes=30)
                for a, b in zip(rows, rows[1:])
            ):
                continue
            window = [getattr(point, attribute) for point in rows]
            if any(value is None for value in window):
                continue
            values.append(sum(window) / width)
        return min(values) if values else None

    price_now = latest("price")
    carbon_now = latest("carbon_intensity")
    price_best = best("price")
    carbon_best = best("carbon_intensity")
    return {
        "ok": all(value is not None for value in (
            price_now, carbon_now, price_best, carbon_best
        )),
        "price_now": price_now,
        "price_cheap": price_best,
        "carbon_now": carbon_now,
        "carbon_clean": carbon_best,
        "from": series[0].timestamp.strftime("%d %b") if series else "",
        "to": series[-1].timestamp.strftime("%d %b %Y") if series else "",
        "market_key": context.market_key,
        "market_name": context.market_name,
        "location_key": context.location_key,
        "location_name": context.location_name,
        "symbol": context.symbol,
        "price_label": context.price_label,
        "carbon_label": context.carbon_label,
        "signal_mode": context.signal_mode,
        "allows_custom_node": context.allows_custom_node,
        "locations": [choice.__dict__ for choice in context.locations],
    }
