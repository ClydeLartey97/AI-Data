"""`facility-energy-v1` — what an operator declares once, instead of typing.

Until now every energy figure reached the optimiser through a browser form:
base load, PUE, each generation source, the battery, the interconnection
limit. That is fine for a demonstration and useless for a real site, where
the answers live in a connection agreement, a PPA and a meter, are stable for
years, and are known by someone who will never open this page.

This module is the document that replaces the form. An operator declares the
site once — where it is, which market settles it, what generation is
connected, under what contract, and how each figure is known — and the
software expands that declaration into the exact request the planner already
consumes. It compiles into the existing contract rather than adding a second
energy model beside it, so nothing here duplicates `core/energy.py`.

Three boundaries this file exists to hold:

**Declaration is not measurement.** Every figure carries how it is known:
`metered` (a real meter, and one must be identified), `contracted` (a signed
agreement), `nameplate` (a datasheet), or `estimated`. A nameplate capacity
is not a measurement, and a modelled generation shape is never better than
ESTIMATED regardless of how well the capacity behind it is known.

**A contract is not an electron.** `delivery_type` decides whether a source
can physically serve load. A virtual PPA or a certificate is reported and
never satisfies the interval energy balance — `core/energy.py` already
enforces this, and the document must not offer a way around it.

**A declared source is not a verified one.** Nothing here inspects a meter or
a contract. The document records what the operator asserts, with their name
and the date on it, so a later dispute is about a stated claim rather than a
number of unknown origin.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.energy import DELIVERY_TYPES, DISPATCH_PRIORITIES, SOURCE_KINDS
from core.renewables import solar_capacity_factor, wind_capacity_factor

VERSION = "facility-energy-v1"

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "site-profile.json"

#: How a declared number is known. Ordered weakest to strongest claim.
EVIDENCE_TIERS = {
    "estimated": "ESTIMATED",
    "nameplate": "SPEC",
    "contracted": "CONTRACTED",
    "metered": "MEASURED",
}

#: How availability over time is derived from a declared capacity.
AVAILABILITY_METHODS = {"flat", "diurnal", "weather", "series"}

#: Methods that model a shape rather than observe one. A modelled shape is
#: ESTIMATED even when the capacity behind it is metered, because the shape
#: is where the error lives.
MODELLED_METHODS = {"diurnal", "weather"}

MAX_SOURCES = 30


class ProfileError(ValueError):
    """A declaration that cannot be trusted, with the reason attached."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _text(raw: dict, key: str, *, where: str, required: bool = True) -> str:
    value = raw.get(key, "")
    _require(isinstance(value, str), f"{where}: {key} must be a string")
    value = value.strip()
    _require(bool(value) or not required, f"{where}: {key} is required")
    return value


def _number(raw: dict, key: str, *, where: str, default: float | None = None,
            minimum: float | None = None, maximum: float | None = None
            ) -> float:
    if key not in raw or raw[key] is None:
        _require(default is not None, f"{where}: {key} is required")
        return float(default)
    value = raw[key]
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             f"{where}: {key} must be a number")
    value = float(value)
    _require(math.isfinite(value), f"{where}: {key} must be finite")
    _require(minimum is None or value >= minimum,
             f"{where}: {key} must be at least {minimum}")
    _require(maximum is None or value <= maximum,
             f"{where}: {key} must be at most {maximum}")
    return value


def _flag(raw: dict, key: str, default: bool, *, where: str) -> bool:
    value = raw.get(key, default)
    _require(isinstance(value, bool), f"{where}: {key} must be true or false")
    return value


def _evidence(raw: dict, *, where: str, default: str = "estimated") -> str:
    value = str(raw.get("evidence", default)).strip().lower()
    _require(value in EVIDENCE_TIERS,
             f"{where}: evidence must be one of {sorted(EVIDENCE_TIERS)}")
    return value


@dataclass(frozen=True)
class DeclaredSource:
    source_id: str
    name: str
    kind: str
    capacity_kw: float
    method: str
    evidence: str
    delivery_type: str = "onsite"
    dispatchable: bool = False
    carbon_free: bool = False
    renewable: bool | None = None
    cost_per_mwh: float = 0.0
    carbon_g_per_kwh: float = 0.0
    confidence: float = 1.0
    peak_hour: float = 12.0
    capacity_factors: tuple[float, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    grid_connection_id: str = ""
    delivery_loss_percent: float = 0.0

    @property
    def provenance(self) -> str:
        """The provenance the expanded series may carry.

        A modelled shape caps the claim at ESTIMATED however well the
        capacity is known: the operator may have metered the array, but
        nobody has metered tomorrow.
        """
        if self.method in MODELLED_METHODS:
            return "ESTIMATED"
        return EVIDENCE_TIERS[self.evidence]


@dataclass(frozen=True)
class SiteProfile:
    version: str
    site: dict
    market: str
    location: str
    base_load_kw: float
    pue: float
    night_pue: float | None
    max_import_kw: float | None
    facility_evidence: str
    sources: tuple[DeclaredSource, ...]
    battery: dict | None
    dispatch_priority: str
    declared_by: str
    declared_at: str
    warnings: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "site": dict(self.site),
            "market": self.market,
            "location": self.location,
            "facility": {
                "base_load_kw": self.base_load_kw,
                "pue": self.pue,
                "night_pue": self.night_pue,
                "max_import_kw": self.max_import_kw,
                "evidence": self.facility_evidence,
                "provenance": EVIDENCE_TIERS[self.facility_evidence],
            },
            "sources": [
                {
                    "source_id": source.source_id, "name": source.name,
                    "kind": source.kind, "capacity_kw": source.capacity_kw,
                    "availability_method": source.method,
                    "delivery_type": source.delivery_type,
                    "physical": source.delivery_type != "contractual",
                    "evidence": source.evidence,
                    "provenance": source.provenance,
                    "grid_connection_id": source.grid_connection_id,
                }
                for source in self.sources
            ],
            "battery": None if self.battery is None else dict(self.battery),
            "dispatch_priority": self.dispatch_priority,
            "declared_by": self.declared_by,
            "declared_at": self.declared_at,
            "warnings": list(self.warnings),
            "boundary": (
                "Every figure is declared by the operator, not verified here. "
                "Contractual sources are reported but never satisfy the "
                "physical interval energy balance."
            ),
        }


def parse(document: dict[str, Any]) -> SiteProfile:
    """Validate a declaration and return it, or say exactly what is wrong."""
    _require(isinstance(document, dict), "site profile must be an object")
    version = str(document.get("version", "")).strip()
    _require(version == VERSION,
             f"unsupported site profile version {version!r}; expected {VERSION}")

    site_raw = document.get("site")
    _require(isinstance(site_raw, dict), "site profile needs a site object")
    site = {
        "site_id": _text(site_raw, "site_id", where="site"),
        "name": _text(site_raw, "name", where="site"),
        "latitude": _number(site_raw, "latitude", where="site",
                            minimum=-90, maximum=90),
        "longitude": _number(site_raw, "longitude", where="site",
                             minimum=-180, maximum=180),
        "grid_connection_id": _text(site_raw, "grid_connection_id",
                                    where="site", required=False),
        "time_zone": _text(site_raw, "time_zone", where="site",
                           required=False) or "UTC",
    }

    market_raw = document.get("market")
    _require(isinstance(market_raw, dict), "site profile needs a market object")
    market = _text(market_raw, "market", where="market").upper()
    location = _text(market_raw, "location", where="market")

    facility_raw = document.get("facility")
    _require(isinstance(facility_raw, dict),
             "site profile needs a facility object")
    facility_evidence = _evidence(facility_raw, where="facility")
    base_load_kw = _number(facility_raw, "base_load_kw", where="facility",
                           default=0, minimum=0)
    pue = _number(facility_raw, "pue", where="facility", default=1.0,
                  minimum=1, maximum=5)
    night_pue = (None if facility_raw.get("night_pue") is None
                 else _number(facility_raw, "night_pue", where="facility",
                              minimum=1, maximum=5))
    max_import_kw = (None if facility_raw.get("max_import_kw") is None
                     else _number(facility_raw, "max_import_kw",
                                  where="facility", minimum=0))

    warnings: list[str] = []
    if facility_evidence == "metered" and not site["grid_connection_id"]:
        # Claiming a meter reading without naming a meter is the exact
        # failure this tier exists to prevent.
        raise ProfileError(
            "facility: evidence 'metered' requires site.grid_connection_id "
            "naming the meter the figures come from")

    sources_raw = document.get("sources")
    _require(isinstance(sources_raw, list) and sources_raw,
             "site profile needs at least one source")
    _require(len(sources_raw) <= MAX_SOURCES,
             f"site profile cannot declare more than {MAX_SOURCES} sources")

    sources: list[DeclaredSource] = []
    seen: set[str] = {"grid"}
    for index, raw in enumerate(sources_raw):
        where = f"sources[{index}]"
        _require(isinstance(raw, dict), f"{where} must be an object")
        source_id = _text(raw, "source_id", where=where)
        _require(source_id not in seen,
                 f"{where}: duplicate source_id {source_id!r} "
                 "('grid' is reserved for residual supply)")
        seen.add(source_id)
        kind = _text(raw, "kind", where=where)
        _require(kind in SOURCE_KINDS and kind != "grid",
                 f"{where}: kind must be one of "
                 f"{sorted(SOURCE_KINDS - {'grid'})}")
        delivery_type = _text(raw, "delivery_type", where=where,
                              required=False) or "onsite"
        _require(delivery_type in DELIVERY_TYPES,
                 f"{where}: delivery_type must be one of "
                 f"{sorted(DELIVERY_TYPES)}")
        method = str(raw.get("availability_method", "flat")).strip().lower()
        _require(method in AVAILABILITY_METHODS,
                 f"{where}: availability_method must be one of "
                 f"{sorted(AVAILABILITY_METHODS)}")
        evidence = _evidence(raw, where=where)

        latitude_raw, longitude_raw = raw.get("latitude"), raw.get("longitude")
        _require((latitude_raw is None) == (longitude_raw is None),
                 f"{where}: latitude and longitude must be given together")
        latitude = (None if latitude_raw is None
                    else _number(raw, "latitude", where=where,
                                 minimum=-90, maximum=90))
        longitude = (None if longitude_raw is None
                     else _number(raw, "longitude", where=where,
                                  minimum=-180, maximum=180))
        if method == "weather":
            _require(latitude is not None,
                     f"{where}: availability_method 'weather' needs the "
                     "source's own coordinates to fetch a forecast for")

        factors: tuple[float, ...] = ()
        if method == "series":
            raw_factors = raw.get("capacity_factors")
            _require(isinstance(raw_factors, list) and raw_factors,
                     f"{where}: availability_method 'series' needs "
                     "capacity_factors")
            for value in raw_factors:
                _require(isinstance(value, (int, float))
                         and not isinstance(value, bool)
                         and 0 <= float(value) <= 1,
                         f"{where}: capacity_factors must be 0-1 numbers")
            factors = tuple(float(value) for value in raw_factors)

        connection = _text(raw, "grid_connection_id", where=where,
                           required=False)
        if evidence == "metered" and not connection:
            raise ProfileError(
                f"{where}: evidence 'metered' requires grid_connection_id "
                "naming the meter")
        if evidence == "contracted" and delivery_type == "onsite":
            warnings.append(
                f"{source_id}: declared onsite but evidenced by contract — "
                "check whether this is a dedicated wire or a market instrument")

        source = DeclaredSource(
            source_id=source_id,
            name=_text(raw, "name", where=where),
            kind=kind,
            capacity_kw=_number(raw, "capacity_kw", where=where, minimum=0),
            method=method,
            evidence=evidence,
            delivery_type=delivery_type,
            dispatchable=_flag(raw, "dispatchable",
                               kind not in {"solar", "wind"}, where=where),
            carbon_free=_flag(raw, "carbon_free", False, where=where),
            renewable=(None if raw.get("renewable") is None
                       else _flag(raw, "renewable", False, where=where)),
            cost_per_mwh=_number(raw, "cost_per_mwh", where=where, default=0),
            carbon_g_per_kwh=_number(raw, "carbon_g_per_kwh", where=where,
                                     default=0, minimum=0),
            confidence=_number(raw, "confidence", where=where, default=1.0,
                               minimum=0, maximum=1),
            peak_hour=_number(raw, "peak_hour", where=where, default=12.0,
                              minimum=0, maximum=23.99),
            capacity_factors=factors,
            latitude=latitude,
            longitude=longitude,
            grid_connection_id=connection,
            delivery_loss_percent=_number(raw, "delivery_loss_percent",
                                          where=where, default=0,
                                          minimum=0, maximum=99.9999),
        )
        sources.append(source)

    if not any(source.delivery_type != "contractual" for source in sources):
        raise ProfileError(
            "at least one source must be physically delivered; a site of "
            "contractual instruments alone cannot serve load")

    battery = None
    battery_raw = document.get("battery")
    if battery_raw is not None:
        _require(isinstance(battery_raw, dict), "battery must be an object")
        capacity = _number(battery_raw, "capacity_kwh", where="battery",
                           minimum=0)
        battery = {
            "capacity_kwh": capacity,
            "max_charge_kw": _number(battery_raw, "max_charge_kw",
                                     where="battery", minimum=0),
            "max_discharge_kw": _number(battery_raw, "max_discharge_kw",
                                        where="battery", minimum=0),
            "initial_energy_kwh": _number(battery_raw, "initial_energy_kwh",
                                          where="battery", default=0,
                                          minimum=0, maximum=capacity),
            "round_trip_efficiency": _number(battery_raw,
                                             "round_trip_efficiency",
                                             where="battery", default=0.9,
                                             minimum=0.000001, maximum=1),
        }

    priority = str(document.get("dispatch_priority", "carbon")).strip().lower()
    _require(priority in DISPATCH_PRIORITIES,
             f"dispatch_priority must be one of {sorted(DISPATCH_PRIORITIES)}")

    declared_by = _text(document, "declared_by", where="document")
    declared_at = _text(document, "declared_at", where="document")

    return SiteProfile(
        version=version, site=site, market=market, location=location,
        base_load_kw=base_load_kw, pue=pue, night_pue=night_pue,
        max_import_kw=max_import_kw, facility_evidence=facility_evidence,
        sources=tuple(sources), battery=battery, dispatch_priority=priority,
        declared_by=declared_by, declared_at=declared_at,
        warnings=tuple(warnings),
    )


def load(path: Path | None = None) -> SiteProfile | None:
    """Read the declared profile, or None when a site has not declared one."""
    target = path or DEFAULT_PATH
    if not target.exists():
        return None
    try:
        document = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise ProfileError(f"site profile is not valid JSON: {exc}") from exc
    return parse(document)


def _diurnal_factor(source: DeclaredSource, timestamp: datetime) -> float:
    """A modelled daily shape, peaking at the declared hour.

    Deliberately crude and deliberately labelled ESTIMATED. It exists so a
    site without coordinates still produces a plausible shape; the honest
    path is `weather`, which uses a real forecast.
    """
    hour = timestamp.hour + timestamp.minute / 60
    distance = abs(hour - source.peak_hour)
    distance = min(distance, 24 - distance)
    if source.kind == "solar":
        # Zero overnight rather than a smooth trough: the sun is not a
        # sine wave that dips, it sets.
        return max(0.0, math.cos(distance / 6.5 * (math.pi / 2))) ** 1.5
    return max(0.15, 0.6 + 0.25 * math.cos(distance / 12 * math.pi))


def _weather_factors(source: DeclaredSource, timestamps: list[datetime],
                     weather: dict[datetime, Any]) -> list[float]:
    factors = []
    for stamp in timestamps:
        point = weather.get(stamp) or weather.get(stamp.replace(minute=0))
        if point is None:
            factors.append(0.0)
            continue
        if source.kind == "solar":
            factors.append(solar_capacity_factor(
                getattr(point, "solar_radiation_wm2", None),
                getattr(point, "temperature_c", None)))
        elif source.kind == "wind":
            factors.append(wind_capacity_factor(
                getattr(point, "wind_speed_100m_ms", None)))
        else:
            factors.append(1.0)
    return factors


def availability_kw(source: DeclaredSource, timestamps: list[datetime],
                    weather: dict[datetime, Any] | None = None) -> list[float]:
    """Expand one declaration into per-interval available power."""
    if source.method == "flat":
        factors = [1.0] * len(timestamps)
    elif source.method == "diurnal":
        factors = [_diurnal_factor(source, stamp) for stamp in timestamps]
    elif source.method == "weather":
        factors = _weather_factors(source, timestamps, weather or {})
    else:
        # A declared series shorter than the horizon repeats daily rather
        # than being padded with zeros, because a 24-hour shape is the usual
        # thing an operator has and silently zeroing the tail would read as
        # an outage the site is not having.
        factors = [source.capacity_factors[i % len(source.capacity_factors)]
                   for i in range(len(timestamps))]
    return [source.capacity_kw * factor for factor in factors]


def power_envelope(profile: SiteProfile, timestamps: list[datetime],
                   weather: dict[datetime, Any] | None = None,
                   *, electrical_limit_kw: float | None = None
                   ) -> list[tuple[datetime, float]]:
    """The ceiling on facility draw in each interval — the performance lever.

    This is what makes on-site generation a throughput input rather than only
    a price input. A site importing at most `max_import_kw` can nonetheless
    draw more while its own generation is producing, so the usable ceiling
    rises and falls with the sun and the wind. Heavy work placed into a high
    window runs at full power instead of being throttled or queued behind a
    flat limit — it finishes sooner, not later.

    Two rules keep it honest:

    * Only **physically delivered** supply raises the ceiling. A virtual PPA
      or a certificate is an accounting instrument and moves no electrons, so
      it cannot power an accelerator.
    * The result is capped by the site's absolute electrical limit. On-site
      generation raises the usable ceiling toward the switchgear rating,
      never through it — the wire is the wire.

    Confidence derates the contribution, so a forecast nobody trusts does not
    licence a schedule that depends on it.
    """
    import_limit = profile.max_import_kw or 0.0
    ceiling = (electrical_limit_kw if electrical_limit_kw is not None
               else max(import_limit, 0.0) + sum(
                   source.capacity_kw for source in profile.sources
                   if source.delivery_type != "contractual"))
    _require(ceiling > 0,
             "a site needs either an import limit or physical generation "
             "before a power ceiling can be derived")

    onsite: list[list[float]] = []
    for source in profile.sources:
        if source.delivery_type == "contractual":
            continue
        served = availability_kw(source, timestamps, weather)
        loss = 1.0 - source.delivery_loss_percent / 100
        onsite.append([value * source.confidence * loss for value in served])

    envelope = []
    for index, stamp in enumerate(timestamps):
        generation = sum(series[index] for series in onsite)
        envelope.append((stamp, min(ceiling, import_limit + generation)))
    return envelope


def to_facility_payload(profile: SiteProfile, timestamps: list[datetime],
                        weather: dict[datetime, Any] | None = None,
                        *, max_power_kw: float | None = None
                        ) -> dict[str, Any]:
    """Compile the declaration into the request the planner already takes.

    This is the whole point of the document: one operator-declared file
    becomes the exact `facility` object the portfolio endpoint has always
    consumed, so no second energy model exists to drift out of step.
    """
    _require(bool(timestamps), "a facility payload needs at least one interval")
    night_pue = profile.night_pue if profile.night_pue is not None else profile.pue
    payload: dict[str, Any] = {
        "max_power_kw": (max_power_kw if max_power_kw is not None
                         else profile.max_import_kw or 0.0),
        "base_load_kw": profile.base_load_kw,
        "pue_profile": [
            profile.pue if 7 <= stamp.hour < 19 else night_pue
            for stamp in timestamps
        ],
        "dispatch_priority": profile.dispatch_priority,
        "site": dict(profile.site),
        "energy_sources": [],
    }
    if profile.battery:
        payload["battery"] = dict(profile.battery)

    for source in profile.sources:
        payload["energy_sources"].append({
            "source_id": source.source_id,
            "name": source.name,
            "kind": source.kind,
            "availability_kw": availability_kw(source, timestamps, weather),
            "cost_per_mwh": source.cost_per_mwh,
            "carbon_g_per_kwh": source.carbon_g_per_kwh,
            "confidence": source.confidence,
            "renewable": source.renewable,
            "carbon_free": source.carbon_free,
            "dispatchable": source.dispatchable,
            "delivery_type": source.delivery_type,
            "provenance": source.provenance,
            "latitude": source.latitude,
            "longitude": source.longitude,
            "grid_connection_id": source.grid_connection_id,
            "delivery_loss_fraction": source.delivery_loss_percent / 100,
        })
    return payload
