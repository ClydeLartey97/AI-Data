"""MISO day-ahead hub price and balancing-area carbon adapter.

MISO publishes keyless daily market-report CSVs containing hourly day-ahead
ex-post LMP by node; the eight named trading hubs are selectable here.
EIA-930 supplies hourly balancing-area carbon. Price changes by hub; carbon
never claims hub precision.

MISO market data uses Eastern Standard Time year-round — the file states
"All Hours-Ending are Eastern Standard Time (EST)" — so timestamps are a
fixed UTC-5 with no daylight-saving fold, unlike NYISO.
"""
from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from adapters.base_adapter import GridDataPoint, MarketAdapter
from adapters.caiso import (EIA_930_REGION_URL, EIA_930_URL, _parse_eia_mix,
                            _parse_eia_rate)

MISO_CSV = "https://docs.misoenergy.org/marketreports/{day}_da_expost_lmp.csv"
#: MISO market hours-ending are EST all year — a fixed offset, not a zone.
EST = timezone(timedelta(hours=-5), "EST")


@dataclass(frozen=True)
class MISOLocation:
    key: str
    name: str
    node: str
    area: str


LOCATIONS: dict[str, MISOLocation] = {
    loc.key: loc for loc in (
        MISOLocation("arkansas", "Arkansas Hub", "ARKANSAS.HUB", "Arkansas"),
        MISOLocation("illinois", "Illinois Hub", "ILLINOIS.HUB", "Illinois"),
        MISOLocation("indiana", "Indiana Hub", "INDIANA.HUB", "Indiana"),
        MISOLocation("louisiana", "Louisiana Hub", "LOUISIANA.HUB", "Louisiana"),
        MISOLocation("michigan", "Michigan Hub", "MICHIGAN.HUB", "Michigan"),
        MISOLocation("minnesota", "Minnesota Hub", "MINN.HUB", "Minnesota"),
        MISOLocation("mississippi", "Mississippi Hub", "MS.HUB", "Mississippi"),
        MISOLocation("texas", "Texas Hub", "TEXAS.HUB", "East Texas"),
    )
}


@dataclass
class MISOPoint(GridDataPoint):
    hub: str = ""
    price_component: str = "DA ex-post LMP"
    carbon_method: str = "EIA-930 fuel-mix estimate; MISO BA"


def location(key: str) -> MISOLocation:
    normal = key.strip().lower()
    if normal not in LOCATIONS:
        raise ValueError(f"unknown MISO hub {key!r}")
    return LOCATIONS[normal]


def _parse_price_csv(payload: str, market_day: date,
                     selected: MISOLocation) -> dict[datetime, float]:
    """Parse one daily DA ex-post LMP file into UTC hour starts.

    The file opens with preamble lines before the real header; rows carry 24
    hours-ending columns. HE 1 is the hour ending 01:00 EST, so its interval
    starts at midnight EST on the market day.
    """
    lines = payload.splitlines()
    header = next((index for index, line in enumerate(lines)
                   if line.startswith("Node,Type,Value")), None)
    if header is None:
        return {}
    midnight = datetime.combine(market_day, datetime.min.time(), tzinfo=EST)
    output: dict[datetime, float] = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[header:]))):
        if row.get("Node") != selected.node or row.get("Value") != "LMP":
            continue
        for hour_ending in range(1, 25):
            raw = row.get(f"HE {hour_ending}", "")
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price):
                continue
            stamp = (midnight + timedelta(hours=hour_ending - 1)
                     ).astimezone(timezone.utc)
            output[stamp] = price
    return output


def _dates(start: datetime, end: datetime) -> list[date]:
    first = start.astimezone(EST).date()
    last = (end - timedelta(microseconds=1)).astimezone(EST).date()
    return [first + timedelta(days=offset)
            for offset in range((last - first).days + 1)]


class MISOAdapter(MarketAdapter):
    """Official MISO hub DA ex-post LMP plus EIA balancing-area carbon."""

    def __init__(self, location_key: str = "indiana", *, session=None,
                 timeout: float = 30.0) -> None:
        self.location = location(location_key)
        self._session = session or requests.Session()
        self.timeout = timeout

    @property
    def market_name(self) -> str:
        return "MISO"

    def _prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        prices: dict[datetime, float] = {}
        for day in _dates(start, end):
            response = self._session.get(
                MISO_CSV.format(day=day.strftime("%Y%m%d")),
                timeout=self.timeout,
                headers={"User-Agent": "grid-aware-scheduler/0.4"},
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            prices.update(_parse_price_csv(response.text, day, self.location))
        return prices

    def _carbon(self, start: datetime, end: datetime
                ) -> dict[datetime, tuple[float, str]]:
        common = {
            "respondent[]": "MISO",
            "frequency": "hourly",
            "start": start.strftime("%m%d%Y %H:%M:%S"),
            "end": end.strftime("%m%d%Y %H:%M:%S"),
            "timezone": "UTC",
        }
        rate_response = self._session.get(
            EIA_930_REGION_URL, params={**common, "type[]": "CO2.CER"},
            timeout=self.timeout,
        )
        rate_response.raise_for_status()
        mix_response = self._session.get(
            EIA_930_URL, params={**common, "type[]": "NG"},
            timeout=self.timeout,
        )
        mix_response.raise_for_status()
        direct = _parse_eia_rate(rate_response.json())
        estimated = _parse_eia_mix(mix_response.json())
        return {
            stamp: (
                direct[stamp],
                "EIA-930 consumption CO2 rate; MISO BA; includes modelled interchange",
            ) if stamp in direct else (
                estimated[stamp],
                "EIA-930 fuel-mix estimate; MISO BA; imports/other excluded",
            )
            for stamp in set(direct) | set(estimated)
        }

    def get_data(self, start: datetime, end: datetime) -> list[MISOPoint]:
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > timedelta(days=31):
            raise ValueError("MISO live queries are limited to 31 days")
        with ThreadPoolExecutor(max_workers=2) as pool:
            price_future = pool.submit(self._prices, start, end)
            carbon_future = pool.submit(self._carbon, start, end)
            prices = price_future.result()
            carbon = carbon_future.result()

        points: list[MISOPoint] = []
        for stamp in sorted(prices):
            if not start <= stamp < end:
                continue
            carbon_value, carbon_method = carbon.get(stamp, (None, "Unavailable"))
            for offset in (timedelta(0), timedelta(minutes=30)):
                half = stamp + offset
                if half >= end:
                    continue
                points.append(MISOPoint(
                    timestamp=half,
                    carbon_intensity=carbon_value,
                    price=prices[stamp],
                    hub=self.location.node,
                    carbon_method=carbon_method,
                ))
        return points
