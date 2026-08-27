"""ERCOT day-ahead settlement point price and balancing-area carbon adapter.

ERCOT matters to this project for a reason no other US market shares: it
settles the Texas grid, which is where the largest recent datacentre buildout
is happening, and it is the only one of the seven ISO/RTOs that is its own
interconnection. A schedule written against ERCOT cannot lean on imports from
a neighbour, so on-site generation and demand timing carry more of the load
than they do anywhere else.

**This adapter is keyless, and that is a deliberate choice rather than a
convenience.** ERCOT's newer `api.ercot.com` requires a registered
subscription key. Its long-standing Market Information System does not: the
report listing at `GetReports.do?reportTypeId=12331` ("DAM Settlement Point
Prices") and the `mirDownload` servlet behind it are both anonymous, and they
publish the same day-ahead prices. Building on the anonymous path means an
operator installing this product needs no ERCOT account, matching CAISO,
NYISO and MISO. The registered API remains worth adding later for the
forward-looking reports the MIS does not carry; it is not needed for this.

**Time is the part of ERCOT that is easy to get wrong.** Files are stamped in
Central Prevailing Time — real US Central, with daylight saving — not the
fixed offset MISO uses. `HourEnding` runs 01:00 to 24:00, so HE 01:00 is the
interval *starting* at midnight. On the autumn fold the local hour repeats and
`DSTFlag` is the only thing separating the two occurrences. The convention
applied here is ERCOT's own: `Y` means daylight time is still in effect, which
is the *first* pass through the repeated hour. That one day a year is the only
place the flag changes an answer, and rather than trust it silently the parser
detects a UTC collision and refuses to overwrite an interval it has already
filled, so a misread flag loses one hour instead of corrupting two.

**Depth.** The daily report listing holds roughly the last month. ERCOT also
publishes annual archives (`reportTypeId=13060`, "Historical DAM Load Zone and
Hub Prices") going back over a decade, but those ship as XLSX and reading them
would add `openpyxl` to a dependency list that is deliberately three packages
long. `historical_archives()` locates them so a backfill can fetch them when
that trade is worth making; nothing in the live path depends on it.

Price is at a settlement point. Carbon is EIA-930 for the ERCO balancing area
and is never relabelled as nodal, exactly as in the CAISO, NYISO and MISO
adapters.
"""
from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from adapters.base_adapter import GridDataPoint, MarketAdapter
from adapters.caiso import (EIA_930_REGION_URL, EIA_930_URL, _parse_eia_mix,
                            _parse_eia_rate)

#: ERCOT's Market Information System. Anonymous, no subscription key.
REPORT_LIST_URL = "https://www.ercot.com/misapp/GetReports.do?reportTypeId={report}"
DOWNLOAD_URL = ("https://www.ercot.com/misdownload/servlets/mirDownload"
                "?mimic_duns=000000000&doclookupId={doc}")
#: DAM Settlement Point Prices — one CSV per operating day, posted the day
#: before delivery once the day-ahead market clears.
DAM_SPP_REPORT = 12331
#: Historical DAM Load Zone and Hub Prices — annual XLSX archives.
HISTORICAL_REPORT = 13060

#: Central Prevailing Time. ERCOT observes daylight saving, so this is a real
#: zone rather than the fixed offset MISO's files use.
CPT = ZoneInfo("America/Chicago")

USER_AGENT = "grid-aware-scheduler/0.13"

#: Rows in the listing look like:
#:   <td class='labelOptional_ind'>cdr.00012331.....DAMSPNP4190_csv.zip</td>
#:   ... <a href='...doclookupId=1266957722'>zip</a>
#: The filename carries the posting date; the id is how it is fetched.
_LISTING_ROW = re.compile(
    r"labelOptional_ind'>(?P<name>[^<]+)</td>.*?doclookupId=(?P<doc>\d+)",
    re.DOTALL)
#: An ERCOT filename carries several dot-separated digit runs and the report
#: id comes first: `cdr.00012331.0000000000000000.20260826.124146390...`. A
#: pattern that simply takes the first eight digits reads the report id as a
#: date and silently drops every row, so every candidate is tried in turn.
_DIGIT_RUN = re.compile(r"\.(\d{8})\.")


@dataclass(frozen=True)
class ERCOTLocation:
    key: str
    name: str
    #: The settlement point exactly as ERCOT writes it in the price file.
    settlement_point: str
    kind: str
    area: str


LOCATIONS: dict[str, ERCOTLocation] = {
    loc.key: loc for loc in (
        # Trading hubs. HB_HOUSTON is the one to reach for when siting near
        # the Gulf Coast industrial corridor.
        ERCOTLocation("houston", "Houston Hub", "HB_HOUSTON", "hub", "Houston"),
        ERCOTLocation("north", "North Hub", "HB_NORTH", "hub", "North"),
        ERCOTLocation("south", "South Hub", "HB_SOUTH", "hub", "South"),
        ERCOTLocation("west", "West Hub", "HB_WEST", "hub", "West"),
        ERCOTLocation("panhandle", "Panhandle Hub", "HB_PAN", "hub", "Panhandle"),
        ERCOTLocation("hubavg", "Hub Average", "HB_HUBAVG", "hub", "ERCOT-wide"),
        ERCOTLocation("busavg", "Bus Average", "HB_BUSAVG", "hub", "ERCOT-wide"),
        # Load zones. A load-serving entity settles here rather than at a hub.
        ERCOTLocation("lz-houston", "Houston Load Zone", "LZ_HOUSTON",
                      "load zone", "Houston"),
        ERCOTLocation("lz-north", "North Load Zone", "LZ_NORTH",
                      "load zone", "North"),
        ERCOTLocation("lz-south", "South Load Zone", "LZ_SOUTH",
                      "load zone", "South"),
        ERCOTLocation("lz-west", "West Load Zone", "LZ_WEST",
                      "load zone", "West"),
        ERCOTLocation("lz-aen", "AEN Load Zone", "LZ_AEN",
                      "load zone", "Austin Energy"),
        ERCOTLocation("lz-cps", "CPS Load Zone", "LZ_CPS",
                      "load zone", "CPS Energy"),
        ERCOTLocation("lz-lcra", "LCRA Load Zone", "LZ_LCRA",
                      "load zone", "Lower Colorado River Authority"),
        ERCOTLocation("lz-raybn", "Rayburn Load Zone", "LZ_RAYBN",
                      "load zone", "Rayburn Country"),
    )
}


@dataclass
class ERCOTPoint(GridDataPoint):
    settlement_point: str = ""
    price_component: str = "DAM Settlement Point Price"
    carbon_method: str = "EIA-930 fuel-mix estimate; ERCO BA"


def location(key: str) -> ERCOTLocation:
    normal = key.strip().lower()
    if normal not in LOCATIONS:
        raise ValueError(f"unknown ERCOT settlement point {key!r}")
    return LOCATIONS[normal]


def _parse_listing(payload: str, suffix: str = "_csv.zip"
                   ) -> list[tuple[date, str]]:
    """Pull (posting date, document id) pairs out of a MIS report listing.

    Each report is published in several formats behind separate ids; only the
    CSV archive is wanted, because the XML carries the same numbers at several
    times the size and the project already parses CSV everywhere else.
    """
    found: list[tuple[date, str]] = []
    for match in _LISTING_ROW.finditer(payload):
        name = match.group("name").strip()
        if not name.endswith(suffix):
            continue
        posted = _posting_date(name)
        if posted is None:
            continue
        found.append((posted, match.group("doc")))
    return found


def _posting_date(name: str) -> date | None:
    """The first eight-digit run in a filename that is actually a date."""
    for candidate in _DIGIT_RUN.findall(name):
        try:
            return datetime.strptime(candidate, "%Y%m%d").date()
        except ValueError:
            continue
    return None


def _hour_start(delivery: date, hour_ending: str, dst_flag: str
                ) -> datetime | None:
    """Convert one ERCOT delivery date and hour-ending into a UTC interval start.

    ``HourEnding`` is the hour the interval *finishes*, so HE 01:00 starts at
    midnight and HE 24:00 starts at 23:00. Anything else would shift every
    price by an hour, which is the sort of error that still produces a
    plausible-looking schedule.
    """
    try:
        ending = int(hour_ending.strip().split(":")[0])
    except (AttributeError, ValueError):
        return None
    if not 1 <= ending <= 25:
        return None
    # DSTFlag 'Y' is daylight time still in effect: the first pass through a
    # repeated local hour. `fold=1` selects the second pass.
    fold = 0 if dst_flag.strip().upper() == "Y" else 1
    naive = datetime.combine(delivery, datetime.min.time())
    local = (naive + timedelta(hours=ending - 1)).replace(tzinfo=CPT, fold=fold)
    return local.astimezone(timezone.utc)


def _parse_price_csv(payload: str, selected: ERCOTLocation
                     ) -> dict[datetime, float]:
    """Parse one DAM Settlement Point Prices file into UTC hour starts.

    The delivery date is read from the file rather than inferred from when it
    was posted. ERCOT posts day-ahead results the afternoon before delivery,
    so inferring would be right almost always — and silently wrong whenever a
    market notice delays a posting past midnight.
    """
    output: dict[datetime, float] = {}
    for row in csv.DictReader(io.StringIO(payload)):
        if (row.get("SettlementPoint") or "").strip() != selected.settlement_point:
            continue
        try:
            delivery = datetime.strptime(
                (row.get("DeliveryDate") or "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        stamp = _hour_start(delivery, row.get("HourEnding") or "",
                            row.get("DSTFlag") or "")
        if stamp is None:
            continue
        try:
            price = float((row.get("SettlementPointPrice") or "").strip())
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price):
            continue
        # A collision means the daylight-saving flag failed to separate the
        # two passes through a repeated hour. Keep the first and drop the
        # second rather than overwrite: losing one interval is recoverable,
        # silently reporting the wrong hour's price is not.
        if stamp in output:
            continue
        output[stamp] = price
    return output


def _csv_from_zip(content: bytes) -> str:
    """ERCOT wraps every report in a zip holding exactly one file."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist()
                 if name.lower().endswith(".csv")]
        if not names:
            raise ValueError("ERCOT archive contained no CSV")
        return archive.read(names[0]).decode("utf-8-sig", errors="replace")


def _delivery_dates(start: datetime, end: datetime) -> list[date]:
    first = start.astimezone(CPT).date()
    last = (end - timedelta(microseconds=1)).astimezone(CPT).date()
    return [first + timedelta(days=offset)
            for offset in range((last - first).days + 1)]


class ERCOTAdapter(MarketAdapter):
    """Keyless ERCOT day-ahead settlement point prices plus EIA-930 carbon."""

    def __init__(self, location_key: str = "houston", *, session=None,
                 timeout: float = 60.0) -> None:
        self.location = location(location_key)
        self._session = session or requests.Session()
        self.timeout = timeout

    @property
    def market_name(self) -> str:
        return "ERCOT"

    def _get(self, url: str, **kwargs) -> requests.Response:
        response = self._session.get(
            url, timeout=self.timeout,
            headers={"User-Agent": USER_AGENT}, **kwargs)
        response.raise_for_status()
        return response

    def available_reports(self) -> list[tuple[date, str]]:
        """Posting dates and ids for the day-ahead files currently listed."""
        listing = self._get(REPORT_LIST_URL.format(report=DAM_SPP_REPORT))
        return _parse_listing(listing.text)

    def historical_archives(self) -> list[tuple[date, str]]:
        """Annual XLSX price archives, for a backfill willing to read XLSX.

        Returned rather than fetched: parsing them needs a dependency this
        project does not carry, so the decision belongs to the caller.
        """
        listing = self._get(REPORT_LIST_URL.format(report=HISTORICAL_REPORT))
        return _parse_listing(listing.text, suffix=".zip")

    def _prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        wanted = set(_delivery_dates(start, end))
        # Day-ahead results are posted the afternoon before delivery. Fetch a
        # day either side of that expectation so a late posting still lands.
        posting_days = {day - timedelta(days=1) for day in wanted}
        posting_days |= wanted
        prices: dict[datetime, float] = {}
        for posted, doc in self.available_reports():
            if posted not in posting_days:
                continue
            response = self._get(DOWNLOAD_URL.format(doc=doc))
            try:
                payload = _csv_from_zip(response.content)
            except (zipfile.BadZipFile, ValueError):
                continue
            for stamp, price in _parse_price_csv(payload, self.location).items():
                prices.setdefault(stamp, price)
        return prices

    def _carbon(self, start: datetime, end: datetime
                ) -> dict[datetime, tuple[float, str]]:
        common = {
            "respondent[]": "ERCO",
            "frequency": "hourly",
            "start": start.strftime("%m%d%Y %H:%M:%S"),
            "end": end.strftime("%m%d%Y %H:%M:%S"),
            "timezone": "UTC",
        }
        rate_response = self._get(
            EIA_930_REGION_URL, params={**common, "type[]": "CO2.CER"})
        mix_response = self._get(
            EIA_930_URL, params={**common, "type[]": "NG"})
        direct = _parse_eia_rate(rate_response.json())
        estimated = _parse_eia_mix(mix_response.json())
        return {
            stamp: (
                direct[stamp],
                "EIA-930 consumption CO2 rate; ERCO BA; includes modelled interchange",
            ) if stamp in direct else (
                estimated[stamp],
                "EIA-930 fuel-mix estimate; ERCO BA; imports/other excluded",
            )
            for stamp in set(direct) | set(estimated)
        }

    def get_data(self, start: datetime, end: datetime) -> list[ERCOTPoint]:
        start = (start.replace(tzinfo=timezone.utc) if start.tzinfo is None
                 else start.astimezone(timezone.utc))
        end = (end.replace(tzinfo=timezone.utc) if end.tzinfo is None
               else end.astimezone(timezone.utc))
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > timedelta(days=31):
            raise ValueError(
                "ERCOT live queries are limited to 31 days; the MIS listing "
                "holds about a month. Use historical_archives() for more.")

        with ThreadPoolExecutor(max_workers=2) as pool:
            price_future = pool.submit(self._prices, start, end)
            carbon_future = pool.submit(self._carbon, start, end)
            prices = price_future.result()
            carbon = carbon_future.result()

        points: list[ERCOTPoint] = []
        for stamp in sorted(prices):
            if not start <= stamp < end:
                continue
            carbon_value, carbon_method = carbon.get(stamp, (None, "Unavailable"))
            # ERCOT settles day-ahead hourly; the scheduler's slot is a half
            # hour, so each hour supplies both halves. The price is the hour's,
            # not an interpolation — saying otherwise would invent precision.
            for offset in (timedelta(0), timedelta(minutes=30)):
                half = stamp + offset
                if half >= end:
                    continue
                points.append(ERCOTPoint(
                    timestamp=half,
                    carbon_intensity=carbon_value,
                    price=prices[stamp],
                    settlement_point=self.location.settlement_point,
                    carbon_method=carbon_method,
                ))
        return points
