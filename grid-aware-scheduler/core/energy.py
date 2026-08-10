"""Interval energy supply and storage dispatch for AI facilities.

The workload scheduler decides when compute runs.  This module answers the
separate physical question: which available sources serve facility demand in
each interval?  It keeps onsite and dedicated supply distinct from contractual
matching, serves fixed facility demand before flexible AI demand, and records
the source mix attributed to every workload.

Dispatch is deterministic and auditable.  Variable or otherwise must-take
physical generation is consumed before storage and dispatchable sources.
Surplus renewable or carbon-free generation may charge the battery.  The
battery is then used before a dirtier or more expensive dispatchable source.
The portfolio search remains exact over its discrete workload placements under
this declared dispatch policy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PERIOD_HOURS = 0.5

SOURCE_KINDS = frozenset({
    "solar", "wind", "hydro", "nuclear", "geothermal", "biomass",
    "gas", "coal", "oil", "grid", "other",
})
DELIVERY_TYPES = frozenset({
    "onsite", "dedicated_wire", "grid", "contractual",
})
PHYSICAL_DELIVERY_TYPES = frozenset({"onsite", "dedicated_wire", "grid"})
VARIABLE_KINDS = frozenset({"solar", "wind"})
RENEWABLE_KINDS = frozenset({
    "solar", "wind", "hydro", "geothermal", "biomass",
})
DISPATCH_PRIORITIES = frozenset({
    "renewable", "carbon_free", "carbon", "cost",
})


def _finite(name: str, value: float, *, minimum: float | None = None,
            maximum: float | None = None) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")


def _coordinate_pair(latitude: float | None, longitude: float | None, *,
                     owner: str) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError(f"{owner} latitude and longitude must be supplied together")
    if latitude is not None:
        _finite(f"{owner} latitude", latitude, minimum=-90, maximum=90)
        _finite(f"{owner} longitude", longitude, minimum=-180, maximum=180)


def haversine_km(latitude_a: float, longitude_a: float,
                 latitude_b: float, longitude_b: float) -> float:
    """Great-circle distance between two WGS84 coordinate pairs."""
    for name, value, minimum, maximum in (
        ("latitude_a", latitude_a, -90, 90),
        ("longitude_a", longitude_a, -180, 180),
        ("latitude_b", latitude_b, -90, 90),
        ("longitude_b", longitude_b, -180, 180),
    ):
        _finite(name, value, minimum=minimum, maximum=maximum)
    radius_km = 6371.0088
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    term = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.asin(min(1.0, math.sqrt(term)))


@dataclass(frozen=True)
class FacilitySite:
    """Exact physical facility identity, separate from grid-signal scope."""
    site_id: str
    name: str
    latitude: float
    longitude: float
    grid_connection_id: str = ""
    time_zone: str = "UTC"

    def __post_init__(self) -> None:
        if not self.site_id.strip() or not self.name.strip():
            raise ValueError("facility sites need an ID and name")
        _coordinate_pair(self.latitude, self.longitude, owner="facility site")
        if not isinstance(self.grid_connection_id, str):
            raise ValueError("grid_connection_id must be a string")
        if not isinstance(self.time_zone, str) or not self.time_zone.strip():
            raise ValueError("time_zone must be a non-empty IANA time-zone name")
        try:
            ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA time zone {self.time_zone!r}") from exc


@dataclass(frozen=True)
class SupplyPoint:
    timestamp: datetime
    available_kw: float
    cost_per_mwh: float
    carbon_g_per_kwh: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("supply timestamps must be timezone-aware")
        _finite("available_kw", self.available_kw, minimum=0)
        _finite("cost_per_mwh", self.cost_per_mwh)
        _finite("carbon_g_per_kwh", self.carbon_g_per_kwh, minimum=0)
        _finite("confidence", self.confidence, minimum=0, maximum=1)

    @property
    def firm_available_kw(self) -> float:
        """Confidence-adjusted power that the scheduler may rely on."""
        return self.available_kw * self.confidence


@dataclass(frozen=True)
class EnergySource:
    source_id: str
    name: str
    kind: str
    points: tuple[SupplyPoint, ...]
    renewable: bool | None = None
    carbon_free: bool = False
    delivery_type: str = "onsite"
    dispatchable: bool = False
    provenance: str = "ESTIMATED"
    latitude: float | None = None
    longitude: float | None = None
    grid_connection_id: str = ""
    delivery_loss_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.name.strip():
            raise ValueError("energy sources need an ID and name")
        if self.kind not in SOURCE_KINDS:
            raise ValueError(f"kind must be one of {sorted(SOURCE_KINDS)}")
        if self.delivery_type not in DELIVERY_TYPES:
            raise ValueError(
                f"delivery_type must be one of {sorted(DELIVERY_TYPES)}"
            )
        if not self.points:
            raise ValueError("energy sources need at least one supply point")
        timestamps = [point.timestamp for point in self.points]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("energy source timestamps must be unique")
        if self.renewable is None:
            object.__setattr__(self, "renewable", self.kind in RENEWABLE_KINDS)
        _coordinate_pair(self.latitude, self.longitude, owner="energy source")
        if not isinstance(self.grid_connection_id, str):
            raise ValueError("grid_connection_id must be a string")
        _finite(
            "delivery_loss_fraction", self.delivery_loss_fraction,
            minimum=0, maximum=0.999999,
        )

    @property
    def physical(self) -> bool:
        return self.delivery_type in PHYSICAL_DELIVERY_TYPES

    @property
    def must_take(self) -> bool:
        return self.physical and (not self.dispatchable or self.kind in VARIABLE_KINDS)


@dataclass(frozen=True)
class FacilityPoint:
    timestamp: datetime
    base_load_kw: float = 0.0
    pue: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("facility timestamps must be timezone-aware")
        _finite("base_load_kw", self.base_load_kw, minimum=0)
        if self.pue is not None:
            _finite("pue", self.pue, minimum=1, maximum=5)


@dataclass(frozen=True)
class BatterySpec:
    capacity_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    initial_energy_kwh: float = 0.0
    round_trip_efficiency: float = 0.9
    initial_cost_per_mwh: float = 0.0
    initial_carbon_g_per_kwh: float = 0.0
    initial_renewable_fraction: float = 0.0
    initial_carbon_free_fraction: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (
            ("capacity_kwh", self.capacity_kwh),
            ("max_charge_kw", self.max_charge_kw),
            ("max_discharge_kw", self.max_discharge_kw),
            ("initial_energy_kwh", self.initial_energy_kwh),
            ("initial_cost_per_mwh", self.initial_cost_per_mwh),
            ("initial_carbon_g_per_kwh", self.initial_carbon_g_per_kwh),
        ):
            _finite(name, value, minimum=0)
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial battery energy cannot exceed capacity")
        _finite(
            "round_trip_efficiency", self.round_trip_efficiency,
            minimum=0.000001, maximum=1,
        )
        _finite(
            "initial_renewable_fraction", self.initial_renewable_fraction,
            minimum=0, maximum=1,
        )
        _finite(
            "initial_carbon_free_fraction", self.initial_carbon_free_fraction,
            minimum=0, maximum=1,
        )


@dataclass(frozen=True)
class SiteEnergyProfile:
    market: str
    location: str
    facility_points: tuple[FacilityPoint, ...]
    sources: tuple[EnergySource, ...]
    battery: BatterySpec | None = None
    dispatch_priority: str = "carbon"
    site: FacilitySite | None = None

    def __post_init__(self) -> None:
        if not self.market.strip() or not self.location.strip():
            raise ValueError("site energy profiles need market and location")
        if not self.facility_points:
            raise ValueError("site energy profiles need facility points")
        stamps = [point.timestamp for point in self.facility_points]
        if len(stamps) != len(set(stamps)):
            raise ValueError("facility timestamps must be unique")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("energy source IDs must be unique")
        if not any(source.physical for source in self.sources):
            raise ValueError("at least one physical energy source is required")
        if self.dispatch_priority not in DISPATCH_PRIORITIES:
            raise ValueError(
                f"dispatch_priority must be one of {sorted(DISPATCH_PRIORITIES)}"
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.market, self.location

    def facility_at(self, timestamp: datetime) -> FacilityPoint:
        lookup = {point.timestamp: point for point in self.facility_points}
        try:
            return lookup[timestamp]
        except KeyError as exc:
            raise ValueError(
                f"missing facility energy point at {timestamp.isoformat()}"
            ) from exc


@dataclass
class SourceTotals:
    name: str
    kind: str
    renewable: bool
    carbon_free: bool
    delivery_type: str
    provenance: str
    latitude: float | None = None
    longitude: float | None = None
    grid_connection_id: str = ""
    distance_to_site_km: float | None = None
    delivery_loss_fraction: float = 0.0
    delivery_loss_kwh: float = 0.0
    base_kwh: float = 0.0
    ai_kwh: float = 0.0
    battery_charge_input_kwh: float = 0.0
    curtailed_kwh: float = 0.0
    contractual_available_kwh: float = 0.0


@dataclass
class JobEnergyTotals:
    energy_kwh: float = 0.0
    renewable_kwh: float = 0.0
    carbon_free_kwh: float = 0.0
    grid_kwh: float = 0.0
    battery_kwh: float = 0.0
    cost: float = 0.0
    carbon_kg: float = 0.0
    source_kwh: dict[str, float] = field(default_factory=dict)

    @property
    def renewable_match_pct(self) -> float:
        return 0.0 if not self.energy_kwh else self.renewable_kwh / self.energy_kwh * 100

    @property
    def carbon_free_match_pct(self) -> float:
        return 0.0 if not self.energy_kwh else self.carbon_free_kwh / self.energy_kwh * 100


@dataclass
class IntervalDispatch:
    timestamp: datetime
    base_load_kw: float
    ai_load_kw: float
    source_base_kwh: dict[str, float]
    source_ai_kwh: dict[str, float]
    battery_charge_input_kwh: float
    battery_discharge_kwh: float
    battery_state_kwh: float
    curtailed_kwh: float
    unmet_kwh: float


@dataclass
class EnergyDispatchResult:
    market: str
    location: str
    site: FacilitySite | None
    intervals: list[IntervalDispatch]
    sources: dict[str, SourceTotals]
    jobs: dict[str, JobEnergyTotals]
    base_energy_kwh: float
    ai_energy_kwh: float
    ai_renewable_kwh: float
    ai_carbon_free_kwh: float
    ai_grid_kwh: float
    ai_battery_kwh: float
    ai_cost: float
    ai_carbon_kg: float
    curtailed_kwh: float
    final_battery_kwh: float
    feasible: bool

    @property
    def ai_renewable_match_pct(self) -> float:
        return 0.0 if not self.ai_energy_kwh else self.ai_renewable_kwh / self.ai_energy_kwh * 100

    @property
    def ai_carbon_free_match_pct(self) -> float:
        return 0.0 if not self.ai_energy_kwh else self.ai_carbon_free_kwh / self.ai_energy_kwh * 100


@dataclass
class _BatteryState:
    energy_kwh: float
    cost: float
    carbon_g: float
    renewable_kwh: float
    carbon_free_kwh: float


@dataclass(frozen=True)
class _Allocation:
    source_id: str
    energy_kwh: float
    cost: float
    carbon_g: float
    renewable_fraction: float
    carbon_free_fraction: float
    delivery_type: str


def _allocation_key(allocation: _Allocation, priority: str) -> tuple:
    intensity = (
        allocation.carbon_g / allocation.energy_kwh
        if allocation.energy_kwh else math.inf
    )
    cost_per_kwh = (
        allocation.cost / allocation.energy_kwh
        if allocation.energy_kwh else math.inf
    )
    if priority == "renewable":
        return (-allocation.renewable_fraction, intensity,
                cost_per_kwh, allocation.source_id)
    if priority == "carbon_free":
        return (-allocation.carbon_free_fraction, intensity,
                cost_per_kwh, allocation.source_id)
    if priority == "cost":
        return (cost_per_kwh, intensity, allocation.source_id)
    return intensity, cost_per_kwh, allocation.source_id


def dispatch_energy(
    profile: SiteEnergyProfile,
    workload_loads_kw: dict[str, dict[datetime, float]],
    *,
    period_hours: float = PERIOD_HOURS,
) -> EnergyDispatchResult:
    """Dispatch site generation, storage and grid supply interval by interval.

    Fixed facility demand is served first.  Remaining source availability is
    the usable supply against which flexible AI work competes.  AI source
    attribution is proportional when multiple workloads overlap.
    """
    _finite("period_hours", period_hours, minimum=0.000001)
    facility_by_time = {point.timestamp: point for point in profile.facility_points}
    timestamps = sorted(facility_by_time)
    source_points = {
        source.source_id: {point.timestamp: point for point in source.points}
        for source in profile.sources
    }
    source_by_id = {source.source_id: source for source in profile.sources}

    def distance_to_site(source: EnergySource) -> float | None:
        if (profile.site is None or source.latitude is None
                or source.longitude is None):
            return None
        return haversine_km(
            profile.site.latitude, profile.site.longitude,
            source.latitude, source.longitude,
        )

    totals = {
        source.source_id: SourceTotals(
            source.name, source.kind, bool(source.renewable), source.carbon_free,
            source.delivery_type, source.provenance,
            source.latitude, source.longitude, source.grid_connection_id,
            distance_to_site(source), source.delivery_loss_fraction,
        )
        for source in profile.sources
    }
    jobs = {job_id: JobEnergyTotals() for job_id in workload_loads_kw}

    battery = profile.battery
    if battery is None:
        state = _BatteryState(0, 0, 0, 0, 0)
        charge_efficiency = discharge_efficiency = 1.0
    else:
        charge_efficiency = discharge_efficiency = math.sqrt(
            battery.round_trip_efficiency
        )
        state = _BatteryState(
            battery.initial_energy_kwh,
            battery.initial_energy_kwh * battery.initial_cost_per_mwh / 1000,
            battery.initial_energy_kwh * battery.initial_carbon_g_per_kwh,
            battery.initial_energy_kwh * battery.initial_renewable_fraction,
            battery.initial_energy_kwh * battery.initial_carbon_free_fraction,
        )

    intervals: list[IntervalDispatch] = []
    for timestamp in timestamps:
        facility = facility_by_time[timestamp]
        job_power = {
            job_id: max(0.0, loads.get(timestamp, 0.0))
            for job_id, loads in workload_loads_kw.items()
            if loads.get(timestamp, 0.0) > 1e-12
        }
        ai_kw = sum(job_power.values())
        base_need = facility.base_load_kw * period_hours
        ai_need = ai_kw * period_hours
        available: dict[str, float] = {}
        source_base: dict[str, float] = {}
        source_ai: dict[str, float] = {}

        for source in profile.sources:
            point = source_points[source.source_id].get(timestamp)
            raw_energy = (
                0.0 if point is None else point.firm_available_kw * period_hours
            )
            energy = (
                raw_energy * (1 - source.delivery_loss_fraction)
                if source.physical else raw_energy
            )
            if source.physical:
                available[source.source_id] = energy
                totals[source.source_id].delivery_loss_kwh += raw_energy - energy
            else:
                totals[source.source_id].contractual_available_kwh += energy

        def source_order(source: EnergySource) -> tuple:
            point = source_points[source.source_id].get(timestamp)
            if point is None:
                return (math.inf, math.inf, math.inf, source.source_id)
            allocation = _Allocation(
                source.source_id, 1.0,
                point.cost_per_mwh / 1000,
                point.carbon_g_per_kwh,
                1.0 if source.renewable else 0.0,
                1.0 if source.carbon_free else 0.0,
                source.delivery_type,
            )
            return _allocation_key(allocation, profile.dispatch_priority)

        must_take = sorted(
            (source for source in profile.sources if source.must_take),
            key=source_order,
        )
        dispatchable = sorted(
            (source for source in profile.sources
             if source.physical and not source.must_take),
            key=source_order,
        )

        def use_source(source: EnergySource, need: float,
                       target: dict[str, float]) -> tuple[float, _Allocation | None]:
            if need <= 1e-12:
                return need, None
            point = source_points[source.source_id].get(timestamp)
            take = min(need, available.get(source.source_id, 0.0))
            if point is None or take <= 1e-12:
                return need, None
            available[source.source_id] -= take
            target[source.source_id] = target.get(source.source_id, 0.0) + take
            return need - take, _Allocation(
                source.source_id,
                take,
                take * point.cost_per_mwh / 1000,
                take * point.carbon_g_per_kwh,
                1.0 if source.renewable else 0.0,
                1.0 if source.carbon_free else 0.0,
                source.delivery_type,
            )

        base_allocations: list[_Allocation] = []
        ai_allocations: list[_Allocation] = []
        for source in must_take:
            base_need, allocation = use_source(source, base_need, source_base)
            if allocation:
                base_allocations.append(allocation)
        for source in must_take:
            ai_need, allocation = use_source(source, ai_need, source_ai)
            if allocation:
                ai_allocations.append(allocation)

        battery_discharge = 0.0
        if battery is not None and state.energy_kwh > 1e-12:
            deliverable = min(
                state.energy_kwh * discharge_efficiency,
                battery.max_discharge_kw * period_hours,
            )

            def battery_allocation(amount: float) -> _Allocation:
                removed = amount / discharge_efficiency
                fraction = removed / state.energy_kwh
                return _Allocation(
                    "battery", amount, state.cost * fraction,
                    state.carbon_g * fraction,
                    state.renewable_kwh / state.energy_kwh,
                    state.carbon_free_kwh / state.energy_kwh,
                    "storage",
                )

            best_dispatchable = None
            for source in dispatchable:
                point = source_points[source.source_id].get(timestamp)
                if point is not None and available.get(source.source_id, 0) > 1e-12:
                    best_dispatchable = _Allocation(
                        source.source_id, 1.0,
                        point.cost_per_mwh / 1000,
                        point.carbon_g_per_kwh,
                        1.0 if source.renewable else 0.0,
                        1.0 if source.carbon_free else 0.0,
                        source.delivery_type,
                    )
                    break
            battery_probe = battery_allocation(min(1.0, deliverable))
            if (best_dispatchable is None
                    or _allocation_key(
                        battery_probe, profile.dispatch_priority,
                    ) <= _allocation_key(
                        best_dispatchable, profile.dispatch_priority,
                    )):
                # Stored surplus is reserved for the controllable AI load
                # before inflexible base demand. This is an explicit operating
                # policy, not a claim that electrons can be distinguished.
                for target_name, need in (("ai", ai_need), ("base", base_need)):
                    if need <= 1e-12 or deliverable <= 1e-12:
                        continue
                    take = min(need, deliverable)
                    allocation = battery_allocation(take)
                    removed = take / discharge_efficiency
                    fraction = removed / state.energy_kwh
                    state.cost *= 1 - fraction
                    state.carbon_g *= 1 - fraction
                    state.renewable_kwh *= 1 - fraction
                    state.carbon_free_kwh *= 1 - fraction
                    state.energy_kwh -= removed
                    deliverable -= take
                    battery_discharge += take
                    if target_name == "base":
                        source_base["battery"] = source_base.get("battery", 0) + take
                        base_allocations.append(allocation)
                        base_need -= take
                    else:
                        source_ai["battery"] = source_ai.get("battery", 0) + take
                        ai_allocations.append(allocation)
                        ai_need -= take

        for source in dispatchable:
            base_need, allocation = use_source(source, base_need, source_base)
            if allocation:
                base_allocations.append(allocation)
        for source in dispatchable:
            ai_need, allocation = use_source(source, ai_need, source_ai)
            if allocation:
                ai_allocations.append(allocation)

        battery_charge_input = 0.0
        if battery is not None and state.energy_kwh < battery.capacity_kwh - 1e-12:
            charge_input_limit = min(
                battery.max_charge_kw * period_hours,
                (battery.capacity_kwh - state.energy_kwh) / charge_efficiency,
            )
            charge_sources = sorted(
                (source for source in must_take
                 if source.renewable or source.carbon_free),
                key=source_order,
            )
            for source in charge_sources:
                if charge_input_limit <= 1e-12:
                    break
                point = source_points[source.source_id].get(timestamp)
                take = min(charge_input_limit, available.get(source.source_id, 0.0))
                if point is None or take <= 1e-12:
                    continue
                available[source.source_id] -= take
                stored = take * charge_efficiency
                state.energy_kwh += stored
                state.cost += take * point.cost_per_mwh / 1000
                state.carbon_g += take * point.carbon_g_per_kwh
                state.renewable_kwh += stored if source.renewable else 0
                state.carbon_free_kwh += stored if source.carbon_free else 0
                charge_input_limit -= take
                battery_charge_input += take
                totals[source.source_id].battery_charge_input_kwh += take

        curtailed = 0.0
        for source_id, energy in available.items():
            source = source_by_id[source_id]
            if source.must_take and energy > 1e-12:
                totals[source_id].curtailed_kwh += energy
                curtailed += energy

        for allocation in base_allocations:
            if allocation.source_id in totals:
                totals[allocation.source_id].base_kwh += allocation.energy_kwh
        for allocation in ai_allocations:
            if allocation.source_id in totals:
                totals[allocation.source_id].ai_kwh += allocation.energy_kwh

        total_job_power = sum(job_power.values())
        if total_job_power > 1e-12:
            for job_id, power in job_power.items():
                share = power / total_job_power
                job = jobs[job_id]
                for allocation in ai_allocations:
                    energy = allocation.energy_kwh * share
                    cost = allocation.cost * share
                    carbon_g = allocation.carbon_g * share
                    job.energy_kwh += energy
                    job.renewable_kwh += energy * allocation.renewable_fraction
                    job.carbon_free_kwh += energy * allocation.carbon_free_fraction
                    job.grid_kwh += (
                        energy if allocation.delivery_type == "grid" else 0
                    )
                    job.battery_kwh += (
                        energy if allocation.source_id == "battery" else 0
                    )
                    job.cost += cost
                    job.carbon_kg += carbon_g / 1000
                    job.source_kwh[allocation.source_id] = (
                        job.source_kwh.get(allocation.source_id, 0) + energy
                    )

        intervals.append(IntervalDispatch(
            timestamp=timestamp,
            base_load_kw=facility.base_load_kw,
            ai_load_kw=ai_kw,
            source_base_kwh=source_base,
            source_ai_kwh=source_ai,
            battery_charge_input_kwh=battery_charge_input,
            battery_discharge_kwh=battery_discharge,
            battery_state_kwh=state.energy_kwh,
            curtailed_kwh=curtailed,
            unmet_kwh=max(0.0, base_need) + max(0.0, ai_need),
        ))

    ai_energy = sum(job.energy_kwh for job in jobs.values())
    return EnergyDispatchResult(
        market=profile.market,
        location=profile.location,
        site=profile.site,
        intervals=intervals,
        sources=totals,
        jobs=jobs,
        base_energy_kwh=sum(
            point.base_load_kw * period_hours for point in profile.facility_points
        ),
        ai_energy_kwh=ai_energy,
        ai_renewable_kwh=sum(job.renewable_kwh for job in jobs.values()),
        ai_carbon_free_kwh=sum(job.carbon_free_kwh for job in jobs.values()),
        ai_grid_kwh=sum(job.grid_kwh for job in jobs.values()),
        ai_battery_kwh=sum(job.battery_kwh for job in jobs.values()),
        ai_cost=sum(job.cost for job in jobs.values()),
        ai_carbon_kg=sum(job.carbon_kg for job in jobs.values()),
        curtailed_kwh=sum(row.curtailed_kwh for row in intervals),
        final_battery_kwh=state.energy_kwh,
        feasible=all(row.unmet_kwh <= 1e-9 for row in intervals),
    )
