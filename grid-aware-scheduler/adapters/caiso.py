"""California ISO grid adapter with locational day-ahead pricing.

CAISO is materially different from GB.  GB has one national wholesale price;
CAISO settles electricity at individual pricing nodes.  A location selection
therefore changes the bill as well as the grid context.

Two official, public sources are joined on UTC hour:

* CAISO OASIS ``PRC_LMP``: day-ahead locational marginal price in USD/MWh.
* EIA-930: hourly consumption CO2 rate where published, plus generation by
  fuel for the CAISO balancing authority as a recent-period fallback.

The preferred carbon series is EIA's consumption-based rate, which uses a
multi-region input-output flow model. It is delayed, so recent periods fall
back to an **estimated production intensity** calculated from the fuel mix and
EIA's published US-average operational factors. The fallback excludes imports
and unclassified "other" generation. Neither measure is nodal. The method is
carried in ``CAISOPoint.carbon_method`` so the UI cannot quietly present
balancing-area carbon as node-level measurement.

Both upstream feeds are hourly.  Each observation is expanded into two
half-hours to preserve the scheduler's common 30-minute decision grain.
"""
from __future__ import annotations

import csv
import io
import math
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests

from adapters.base_adapter import GridDataPoint, MarketAdapter

OASIS_URL = "https://oasis.caiso.com/oasisapi/SingleZip"
EIA_930_URL = (
    "https://www.eia.gov/electricity/930-api/"
    "region_data_by_fuel_type/series_data"
)
EIA_930_REGION_URL = (
    "https://www.eia.gov/electricity/930-api/"
    "region_data/series_data"
)


@dataclass(frozen=True)
class CAISOLocation:
    """A selectable CAISO settlement location.

    The initial catalogue uses the three trading hubs and the three default
    load aggregation points.  ``node`` remains an open input on the adapter,
    so a customer can supply a specific PNode without changing scheduling
    logic.
    """

    key: str
    name: str
    node: str
    kind: str
    area: str


LOCATIONS: dict[str, CAISOLocation] = {
    loc.key: loc for loc in (
        CAISOLocation("np15", "NP15 trading hub", "TH_NP15_GEN-APND",
                      "Trading hub", "Northern California"),
        CAISOLocation("sp15", "SP15 trading hub", "TH_SP15_GEN-APND",
                      "Trading hub", "Southern California"),
        CAISOLocation("zp26", "ZP26 trading hub", "TH_ZP26_GEN-APND",
                      "Trading hub", "Central California"),
        CAISOLocation("pgae", "PG&E default load area", "DLAP_PGAE-APND",
                      "Load aggregation point", "Northern California"),
        CAISOLocation("sce", "SCE default load area", "DLAP_SCE-APND",
                      "Load aggregation point", "Southern California"),
        CAISOLocation("sdge", "SDG&E default load area", "DLAP_SDGE-APND",
                      "Load aggregation point", "San Diego"),
    )
}

# EIA 2023 US operational factors in pounds CO2/kWh, converted to g/kWh.
# EIA treats biomass, hydro, solar, wind and nuclear as zero operational CO2.
_LB_TO_G = 453.59237
FUEL_FACTOR_G_KWH = {
    "COL": 2.31 * _LB_TO_G,
    "NG": 0.96 * _LB_TO_G,
    "OIL": 2.46 * _LB_TO_G,
}
ACCOUNTED_FUELS = set(FUEL_FACTOR_G_KWH) | {"WAT", "SUN", "WND", "GEO", "NUC"}


@dataclass
class CAISOPoint(GridDataPoint):
    node: str = ""
    price_component: str = "LMP"
    carbon_method: str = "EIA-930 fuel-mix estimate; CAISO BA; imports/other excluded"


def location(key_or_node: str) -> CAISOLocation:
    """Resolve a catalogue key, or accept an explicit CAISO node."""
    if key_or_node in LOCATIONS:
        return LOCATIONS[key_or_node]
    node = key_or_node.strip().upper()
    if not node or len(node) > 80 or not all(c.isalnum() or c in "_-." for c in node):
        raise ValueError("invalid CAISO pricing node")
    return CAISOLocation(node.lower(), node, node, "Pricing node", "Custom")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _oasis_stamp(value: datetime) -> str:
    return _utc(value).strftime("%Y%m%dT%H:%M-0000")


def _parse_oasis_csv(payload: bytes, node: str) -> dict[datetime, float]:
    """Parse an OASIS ZIP response into hourly total LMP values."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        preview = payload[:160].decode("utf-8", errors="replace")
        raise RuntimeError(f"CAISO OASIS returned a non-ZIP response: {preview}") from exc

    out: dict[datetime, float] = {}
    for name in archive.namelist():
        if not name.lower().endswith(".csv"):
            continue
        with archive.open(name) as raw:
            rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            for row in rows:
                if row.get("LMP_TYPE") not in ("LMP", None, ""):
                    continue
                if row.get("NODE") and row["NODE"] != node:
                    continue
                try:
                    stamp = datetime.fromisoformat(
                        row["INTERVALSTARTTIME_GMT"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    value = float(row["MW"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    out[stamp] = value
    return out


def _series_rows(payload: object) -> Iterable[dict]:
    if not isinstance(payload, list):
        return []
    rows: list[dict] = []
    for wrapper in payload:
        if isinstance(wrapper, dict):
            data = wrapper.get("data", [])
            if isinstance(data, list):
                rows.extend(r for r in data if isinstance(r, dict))
    return rows


def _parse_eia_mix(payload: object) -> dict[datetime, float]:
    """Turn EIA generation-by-fuel series into production gCO2/kWh."""
    generation: dict[datetime, dict[str, float]] = {}
    for row in _series_rows(payload):
        fuel = str(row.get("FUEL_TYPE_ID", ""))
        values = row.get("VALUES", {})
        if not isinstance(values, dict):
            continue
        dates, data = values.get("DATES", []), values.get("DATA", [])
        if not isinstance(dates, list) or not isinstance(data, list):
            continue
        for date_text, raw in zip(dates, data):
            if raw is None:
                continue
            try:
                stamp = datetime.strptime(str(date_text), "%m/%d/%Y %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                value = max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
            generation.setdefault(stamp, {})[fuel] = value

    intensities: dict[datetime, float] = {}
    for stamp, mix in generation.items():
        # OTH can include storage and unidentified generation, and can be
        # strongly negative while storage charges. It has no defensible single
        # factor, so omit it from both sides rather than silently treating it
        # as zero-carbon generation.
        accounted = {fuel: value for fuel, value in mix.items()
                     if fuel in ACCOUNTED_FUELS}
        total_mwh = sum(accounted.values())
        if total_mwh <= 0:
            continue
        emitted = sum(
            mwh * FUEL_FACTOR_G_KWH.get(fuel, 0.0)
            for fuel, mwh in accounted.items()
        )
        intensities[stamp] = emitted / total_mwh
    return intensities


def _parse_eia_rate(payload: object, rate_type: str = "CO2.CER"
                    ) -> dict[datetime, float]:
    """Parse EIA emissions rate from metric tonnes/MWh to gCO2/kWh."""
    out: dict[datetime, float] = {}
    for row in _series_rows(payload):
        if row.get("TYPE_ID") != rate_type:
            continue
        values = row.get("VALUES", {})
        if not isinstance(values, dict):
            continue
        dates, data = values.get("DATES", []), values.get("DATA", [])
        if not isinstance(dates, list) or not isinstance(data, list):
            continue
        for date_text, raw in zip(dates, data):
            if raw is None:
                continue
            try:
                stamp = datetime.strptime(str(date_text), "%m/%d/%Y %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                tonnes_per_mwh = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(tonnes_per_mwh) and tonnes_per_mwh >= 0:
                out[stamp] = tonnes_per_mwh * 1000.0
    return out


class CAISOAdapter(MarketAdapter):
    """Official CAISO LMP plus EIA balancing-area carbon estimate."""

    def __init__(self, location_key: str = "sp15", *, session=None,
                 timeout: float = 30.0) -> None:
        self.location = location(location_key)
        self._session = session or requests.Session()
        self.timeout = timeout

    @property
    def market_name(self) -> str:
        return "CAISO"

    def _get(self, url: str, *, params: dict, attempts: int = 3):
        last = None
        for attempt in range(attempts):
            response = self._session.get(
                url, params=params, timeout=self.timeout,
                headers={"User-Agent": "grid-aware-scheduler/0.2"},
            )
            last = response
            if response.status_code != 429:
                response.raise_for_status()
                return response
            if attempt + 1 < attempts:
                retry = response.headers.get("Retry-After")
                time.sleep(max(5.0, float(retry) if retry else 5.0))
        assert last is not None
        last.raise_for_status()

    def _prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        response = self._get(OASIS_URL, params={
            "queryname": "PRC_LMP",
            "startdatetime": _oasis_stamp(start),
            "enddatetime": _oasis_stamp(end),
            "market_run_id": "DAM",
            "node": self.location.node,
            "version": "1",
            "resultformat": "6",
        })
        return _parse_oasis_csv(response.content, self.location.node)

    def _carbon(self, start: datetime, end: datetime
                ) -> dict[datetime, tuple[float, str]]:
        common = {
            "respondent[]": "CISO",
            "frequency": "hourly",
            "start": _utc(start).strftime("%m%d%Y %H:%M:%S"),
            "end": _utc(end).strftime("%m%d%Y %H:%M:%S"),
            "timezone": "UTC",
        }
        rate_response = self._get(EIA_930_REGION_URL, params={
            **common,
            "type[]": "CO2.CER",
        })
        mix_response = self._get(EIA_930_URL, params={
            **common,
            "respondent[]": "CISO",
            "type[]": "NG",
        })
        direct = _parse_eia_rate(rate_response.json())
        estimated = _parse_eia_mix(mix_response.json())
        stamps = set(direct) | set(estimated)
        return {
            stamp: (
                direct[stamp],
                "EIA-930 consumption CO2 rate; CAISO BA; includes modelled interchange",
            ) if stamp in direct else (
                estimated[stamp],
                "EIA-930 fuel-mix estimate; CAISO BA; imports/other excluded",
            )
            for stamp in stamps
            if stamp in direct or stamp in estimated
        }

    def get_data(self, start: datetime, end: datetime) -> list[GridDataPoint]:
        start, end = _utc(start), _utc(end)
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > timedelta(days=31):
            raise ValueError("CAISO live queries are limited to 31 days")

        with ThreadPoolExecutor(max_workers=2) as pool:
            price_future = pool.submit(self._prices, start, end)
            carbon_future = pool.submit(self._carbon, start, end)
            prices = price_future.result()
            carbon = carbon_future.result()
        points: list[CAISOPoint] = []
        for stamp in sorted(prices):
            if stamp < start or stamp >= end:
                continue
            for offset in (timedelta(0), timedelta(minutes=30)):
                half = stamp + offset
                if half >= end:
                    continue
                carbon_value, carbon_method = carbon.get(stamp, (None, "Unavailable"))
                points.append(CAISOPoint(
                    timestamp=half,
                    carbon_intensity=carbon_value,
                    price=prices[stamp],
                    node=self.location.node,
                    carbon_method=carbon_method,
                ))
        return points
