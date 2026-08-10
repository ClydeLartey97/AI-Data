"""New York ISO day-ahead zonal price and balancing-area carbon adapter.

NYISO publishes keyless daily CSV files containing hourly day-ahead zonal
LBMP. EIA-930 supplies hourly balancing-area carbon information. Price changes
by NYISO zone; carbon does not claim zonal precision.
"""
from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from adapters.base_adapter import GridDataPoint, MarketAdapter
from adapters.caiso import (EIA_930_REGION_URL, EIA_930_URL, _parse_eia_mix,
                            _parse_eia_rate)

NYISO_CSV = "https://mis.nyiso.com/public/csv/damlbmp/{day}damlbmp_zone.csv"
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class NYISOLocation:
    key: str
    name: str
    zone: str
    ptid: int
    area: str


LOCATIONS: dict[str, NYISOLocation] = {
    loc.key: loc for loc in (
        NYISOLocation("west", "West", "WEST", 61752, "Western New York"),
        NYISOLocation("genesee", "Genesee", "GENESE", 61753, "Rochester area"),
        NYISOLocation("central", "Central", "CENTRL", 61754, "Central New York"),
        NYISOLocation("north", "North", "NORTH", 61755, "Northern New York"),
        NYISOLocation("mohawk", "Mohawk Valley", "MHK VL", 61756, "Mohawk Valley"),
        NYISOLocation("capital", "Capital", "CAPITL", 61757, "Albany area"),
        NYISOLocation("hudson", "Hudson Valley", "HUD VL", 61758, "Hudson Valley"),
        NYISOLocation("millwood", "Millwood", "MILLWD", 61759, "Northern Westchester"),
        NYISOLocation("dunwoodie", "Dunwoodie", "DUNWOD", 61760, "Lower Hudson"),
        NYISOLocation("nyc", "New York City", "N.Y.C.", 61761, "New York City"),
        NYISOLocation("longisland", "Long Island", "LONGIL", 61762, "Long Island"),
    )
}


@dataclass
class NYISOPoint(GridDataPoint):
    zone: str = ""
    ptid: int = 0
    price_component: str = "LBMP"
    carbon_method: str = "EIA-930 fuel-mix estimate; NYISO BA"


def location(key: str) -> NYISOLocation:
    normal = key.strip().lower()
    if normal not in LOCATIONS:
        raise ValueError(f"unknown NYISO zone {key!r}")
    return LOCATIONS[normal]


def _parse_price_csv(payload: str, selected: NYISOLocation
                     ) -> dict[datetime, float]:
    """Parse one official daily zonal LBMP file into UTC hours."""
    output: dict[datetime, float] = {}
    occurrences: dict[datetime, int] = {}
    for row in csv.DictReader(io.StringIO(payload)):
        if row.get("Name") != selected.zone:
            continue
        try:
            if int(row.get("PTID", "0")) != selected.ptid:
                continue
            naive = datetime.strptime(row["Time Stamp"], "%m/%d/%Y %H:%M")
            price = float(row["LBMP ($/MWHr)"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(price):
            continue
        # A fall-back day contains the local 01:00 hour twice. Assign the
        # second occurrence fold=1 so the two market hours remain distinct.
        fold = min(1, occurrences.get(naive, 0))
        occurrences[naive] = occurrences.get(naive, 0) + 1
        stamp = naive.replace(tzinfo=NEW_YORK, fold=fold).astimezone(timezone.utc)
        output[stamp] = price
    return output


def _dates(start: datetime, end: datetime) -> list[date]:
    first = start.astimezone(NEW_YORK).date()
    last = (end - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


class NYISOAdapter(MarketAdapter):
    """Official NYISO zonal LBMP plus EIA balancing-area carbon."""

    def __init__(self, location_key: str = "nyc", *, session=None,
                 timeout: float = 30.0) -> None:
        self.location = location(location_key)
        self._session = session or requests.Session()
        self.timeout = timeout

    @property
    def market_name(self) -> str:
        return "NYISO"

    def _prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        prices: dict[datetime, float] = {}
        for day in _dates(start, end):
            response = self._session.get(
                NYISO_CSV.format(day=day.strftime("%Y%m%d")),
                timeout=self.timeout,
                headers={"User-Agent": "grid-aware-scheduler/0.4"},
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            prices.update(_parse_price_csv(response.text, self.location))
        return prices

    def _carbon(self, start: datetime, end: datetime
                ) -> dict[datetime, tuple[float, str]]:
        common = {
            "respondent[]": "NYIS",
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
                "EIA-930 consumption CO2 rate; NYISO BA; includes modelled interchange",
            ) if stamp in direct else (
                estimated[stamp],
                "EIA-930 fuel-mix estimate; NYISO BA; imports/other excluded",
            )
            for stamp in set(direct) | set(estimated)
        }

    def get_data(self, start: datetime, end: datetime) -> list[NYISOPoint]:
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > timedelta(days=31):
            raise ValueError("NYISO live queries are limited to 31 days")
        with ThreadPoolExecutor(max_workers=2) as pool:
            price_future = pool.submit(self._prices, start, end)
            carbon_future = pool.submit(self._carbon, start, end)
            prices = price_future.result()
            carbon = carbon_future.result()

        points: list[NYISOPoint] = []
        for stamp in sorted(prices):
            if not start <= stamp < end:
                continue
            carbon_value, carbon_method = carbon.get(stamp, (None, "Unavailable"))
            for offset in (timedelta(0), timedelta(minutes=30)):
                half = stamp + offset
                if half >= end:
                    continue
                points.append(NYISOPoint(
                    timestamp=half,
                    carbon_intensity=carbon_value,
                    price=prices[stamp],
                    zone=self.location.zone,
                    ptid=self.location.ptid,
                    carbon_method=carbon_method,
                ))
        return points
