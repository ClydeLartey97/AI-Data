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

from core import generation
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
AVAILABILITY_METHODS = {"flat", "diurnal", "weather", "series", "plant"}

#: Methods that model a shape rather than observe one. A modelled shape is
#: ESTIMATED even when the capacity behind it is metered, because the shape
#: is where the error lives.
MODELLED_METHODS = {"diurnal", "weather"}

#: Availability read from the plant's own declaration to the system operator
#: rather than modelled. Not a measurement at our meter, but not a guess
#: either: the operator of the plant filed it, and an outage appears in it.
REPORTED_PROVENANCE = "REPORTED"

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
    #: Fraction of nameplate this plant delivers when running normally, after
    #: forced and planned outages. Distinct from `confidence`, which derates a
    #: forecast nobody trusts; this derates the plant itself.
    availability_factor: float = 1.0
    peak_hour: float = 12.0
    capacity_factors: tuple[float, ...] = ()
    #: The plant's identity in its market's own register — a GB BM Unit id
    #: such as "T_SIZB-1". Coordinates locate a station well enough to fetch
    #: its weather, but two units share a station's coordinates and can have
    #: completely different availability, so identity has to be the unit.
    plant_id: str = ""
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
        if self.method == "plant":
            return REPORTED_PROVENANCE
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
    declared_electrical_limit_kw: float | None
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
                "electrical_limit_kw": self.electrical_limit_kw,
                "electrical_limit_declared": (
                    self.declared_electrical_limit_kw is not None),
                "evidence": self.facility_evidence,
                "provenance": EVIDENCE_TIERS[self.facility_evidence],
            },
            "sources": [
                {
                    "source_id": source.source_id, "name": source.name,
                    "kind": source.kind, "capacity_kw": source.capacity_kw,
                    "availability_method": source.method,
                    "availability_factor": source.availability_factor,
                    # Whether the shape came from a real forecast or from the
                    # declared availability. An operator surface must be able
                    # to tell these apart at a glance.
                    "weather_modelled": (
                        source.method == "weather"
                        and generation.can_model_from_weather(source.kind)),
                    "has_peak_worth_scheduling_into": (
                        source.kind in generation.MUST_RUN_VARIABLE_KINDS),
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


    @property
    def electrical_limit_kw(self) -> float:
        """The most the site can draw at once, from any combination of supply.

        Declared when the operator knows their switchgear rating; otherwise
        derived as the import limit plus every kilowatt of physically
        delivered generation, which is the most that could ever arrive at
        once. It is deliberately not the import limit — a site importing
        1 kW while its own array produces 4 kW can draw 5 kW.
        """
        if self.declared_electrical_limit_kw is not None:
            return self.declared_electrical_limit_kw
        return (self.max_import_kw or 0.0) + sum(
            source.capacity_kw for source in self.sources
            if source.delivery_type != "contractual")


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
    declared_limit = (None if facility_raw.get("electrical_limit_kw") is None
                      else _number(facility_raw, "electrical_limit_kw",
                                   where="facility", minimum=0.000001))

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
        # Only demand coordinates when a forecast will actually be fetched.
        # A nuclear or hydro plant declared with 'weather' falls back to its
        # declared availability and is warned about below, so insisting on a
        # location for a request that never happens would refuse a valid
        # declaration over an unused field.
        if method == "plant":
            _require(bool(_text(raw, "plant_id", where=where, required=False)),
                     f"{where}: availability_method 'plant' needs 'plant_id' — "
                     "the unit's id in its market register, such as a GB BM "
                     "Unit id like 'T_SIZB-1'. A station's coordinates are not "
                     "enough: two units share them and can differ completely.")

        if method == "weather" and generation.can_model_from_weather(kind):
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

        # Asking for a forecast that cannot be made must be visible. The
        # fallback is the declared availability, not full nameplate.
        note = generation.availability_note(kind, method)
        if note:
            warnings.append(f"{source_id}: {note}")

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
            availability_factor=_number(raw, "availability_factor",
                                        where=where, default=1.0,
                                        minimum=0, maximum=1),
            peak_hour=_number(raw, "peak_hour", where=where, default=12.0,
                              minimum=0, maximum=23.99),
            capacity_factors=factors,
            plant_id=_text(raw, "plant_id", where=where, required=False),
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
        max_import_kw=max_import_kw,
        declared_electrical_limit_kw=declared_limit,
        facility_evidence=facility_evidence,
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


def fetch_weather(profile: SiteProfile, timestamps: list[datetime],
                  *, adapter: Any = None
                  ) -> tuple[dict[str, dict[datetime, Any]], list[str]]:
    """Forecast weather at each declared plant, keyed by source.

    Sources are grouped by coordinate so two arrays on one roof cost one
    request. Weather is per-plant and not per-site on purpose: a wind farm
    fifty kilometres away has its own wind, and using the data centre's
    weather for it would be a quietly wrong answer rather than a missing one.

    A failed fetch returns a warning and no data for that source, which the
    envelope then treats as no contribution. That is the fail-closed
    direction: an unavailable forecast must never licence a schedule that
    assumes generation.
    """
    from adapters.weather import Location, WeatherAdapter

    needed = [source for source in profile.sources
              if source.method == "weather" and source.latitude is not None]
    if not needed:
        return {}, []

    adapter = adapter or WeatherAdapter()
    span = max(timestamps) - min(timestamps)
    days = max(1, min(16, int(span.total_seconds() // 86400) + 2))

    by_place: dict[tuple[float, float], list[DeclaredSource]] = {}
    for source in needed:
        by_place.setdefault(
            (round(source.latitude, 4), round(source.longitude, 4)), []
        ).append(source)

    weather: dict[str, dict[datetime, Any]] = {}
    warnings: list[str] = []
    for (latitude, longitude), sources in by_place.items():
        try:
            points = adapter.forecast(
                Location(sources[0].name, latitude, longitude), days=days)
        except Exception as exc:                       # noqa: BLE001
            names = ", ".join(source.source_id for source in sources)
            warnings.append(
                f"weather forecast unavailable for {names}: {exc}; these "
                "sources contribute no power until a forecast is available")
            continue
        indexed = {point.timestamp: point for point in points}
        for source in sources:
            weather[source.source_id] = indexed
    return weather, warnings


def _weather_factors(source: DeclaredSource, timestamps: list[datetime],
                     weather: dict[datetime, Any]) -> list[float]:
    """Per-interval capacity factor from a real forecast.

    A kind with no weather model falls back to the source's declared
    availability rather than to full output. That fallback used to be a silent
    1.0, which reported a nuclear unit mid-refuelling and a becalmed hydro
    scheme as producing nameplate in every interval. `parse` raises a warning
    naming any source that lands here, so the substitution is visible.
    """
    if not generation.can_model_from_weather(source.kind):
        return [source.availability_factor] * len(timestamps)

    # What a missing forecast hour means depends on the plant, and getting this
    # backwards is a real error in both directions. For solar and wind the
    # forecast *is* the output, so an unavailable forecast fails closed to zero
    # — it must never licence a schedule that assumes generation. For a
    # dispatchable machine the forecast only supplies a temperature derate; the
    # turbine is still installed and still able to run, so falling to zero
    # would report a working plant as offline.
    missing = (0.0 if source.kind in generation.MUST_RUN_VARIABLE_KINDS
               else source.availability_factor)

    factors = []
    for stamp in timestamps:
        point = weather.get(stamp) or weather.get(stamp.replace(minute=0))
        factor = generation.weather_capacity_factor(source.kind, point)
        factors.append(missing if factor is None else factor)
    return factors


def availability_kw(source: DeclaredSource, timestamps: list[datetime],
                    weather: dict[str, dict[datetime, Any]] | None = None,
                    plant_data: dict[str, dict[datetime, float]] | None = None
                    ) -> list[float]:
    """Expand one declaration into per-interval available power.

    `weather` and `plant_data` are both keyed by source ID rather than by
    timestamp alone, because each declared plant has its own coordinates and
    its own market identity, and therefore its own series.
    """
    if source.method == "plant":
        # Absolute kW straight from the plant's own declaration, not a factor
        # applied to nameplate. A half hour the unit did not report is treated
        # as unavailable: for a plant we are physically connected to, silence
        # is not permission to assume it is generating.
        reported = (plant_data or {}).get(source.source_id, {})
        return [reported.get(stamp, 0.0) for stamp in timestamps]

    if source.method == "flat":
        factors = [source.availability_factor] * len(timestamps)
    elif source.method == "diurnal":
        factors = [_diurnal_factor(source, stamp) for stamp in timestamps]
    elif source.method == "weather":
        factors = _weather_factors(
            source, timestamps, (weather or {}).get(source.source_id, {}))
    else:
        # A declared series shorter than the horizon repeats daily rather
        # than being padded with zeros, because a 24-hour shape is the usual
        # thing an operator has and silently zeroing the tail would read as
        # an outage the site is not having.
        factors = [source.capacity_factors[i % len(source.capacity_factors)]
                   for i in range(len(timestamps))]
    return [source.capacity_kw * factor for factor in factors]


def fetch_plant_data(profile: SiteProfile, timestamps: list[datetime]
                     ) -> tuple[dict[str, dict[datetime, float]], list[str]]:
    """Declared availability for every source that names a market unit.

    Fails closed per source, matching `fetch_weather`: a unit whose data
    cannot be retrieved contributes nothing and says so, because an
    unavailable reading must never licence a schedule that assumes the plant
    is running.
    """
    wanted = [s for s in profile.sources if s.method == "plant"]
    if not wanted:
        return {}, []

    out: dict[str, dict[datetime, float]] = {}
    warnings: list[str] = []
    if profile.market != "GB":
        return {}, [
            f"{len(wanted)} source(s) declare availability_method 'plant', "
            f"which currently only resolves in GB through Elexon per-unit "
            f"balancing data. Market is {profile.market}; those sources "
            f"contribute no power."]

    from adapters import gb_plant
    for source in wanted:
        try:
            out[source.source_id] = gb_plant.availability_by_timestamp(
                source.plant_id, timestamps)
        except Exception as exc:  # noqa: BLE001 - fail closed, keep the reason
            out[source.source_id] = {}
            warnings.append(
                f"{source.source_id}: could not read declared availability "
                f"for unit {source.plant_id} ({type(exc).__name__}); it "
                f"contributes no power this run.")
    return out, warnings


def power_envelope(profile: SiteProfile, timestamps: list[datetime],
                   weather: dict[str, dict[datetime, Any]] | None = None,
                   *, electrical_limit_kw: float | None = None,
                   plant_data: dict[str, dict[datetime, float]] | None = None
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
        served = availability_kw(source, timestamps, weather, plant_data)
        loss = 1.0 - source.delivery_loss_percent / 100
        onsite.append([value * source.confidence * loss for value in served])

    envelope = []
    for index, stamp in enumerate(timestamps):
        generation = sum(series[index] for series in onsite)
        envelope.append((stamp, min(ceiling, import_limit + generation)))
    return envelope


def to_facility_payload(profile: SiteProfile, timestamps: list[datetime],
                        weather: dict[str, dict[datetime, Any]] | None = None,
                        plant_data: dict[str, dict[datetime, float]] | None = None,
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
        # The site's absolute ceiling, which is NOT the import limit: a site
        # importing 1 kW while its own array produces 4 kW can draw 5 kW. Using
        # the import limit here would cap the facility at the grid connection
        # and discard every kilowatt of on-site generation.
        "max_power_kw": (max_power_kw if max_power_kw is not None
                         else profile.electrical_limit_kw),
        "base_load_kw": profile.base_load_kw,
        "pue_profile": [
            profile.pue if 7 <= stamp.hour < 19 else night_pue
            for stamp in timestamps
        ],
        # The API's own name for the same setting. Emitting the document's
        # wording here would silently fall back to the default.
        "energy_priority": profile.dispatch_priority,
        "site": dict(profile.site),
        "energy_sources": [],
    }
    limit = payload["max_power_kw"]
    if limit:
        payload["power_profile_kw"] = [
            value for _, value in power_envelope(
                profile, timestamps, weather, electrical_limit_kw=limit)
        ]
    if profile.battery:
        payload["battery"] = dict(profile.battery)

    for source in profile.sources:
        payload["energy_sources"].append({
            "source_id": source.source_id,
            "name": source.name,
            "kind": source.kind,
            "availability_kw": availability_kw(source, timestamps, weather,
                                              plant_data),
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
