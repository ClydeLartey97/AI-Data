"""Versioned JSON contract for local planning integrations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
import math
from typing import Any

from app.markets import MarketContext
from adapters.base_adapter import GridDataPoint
from core.backtest import BacktestCandidate, backtest
from core.energy import (BatterySpec, EnergyDispatchResult, EnergySource,
                         FacilityPoint, FacilitySite, SiteEnergyProfile,
                         SupplyPoint)
from core.evidence import EvidenceProfile
from core.estimator import (GridLocation, WorkloadSpec, plan_workload,
                            planning_candidates)
from core.planner import PlanOption, PlanningCandidate, PlanningRequest
from core.portfolio import (PortfolioJob, PortfolioPolicy, SiteCapacity,
                            optimise_portfolio, schedule_earliest)
from hardware.calibration import CalibrationProfile

API_VERSION = "v1"
#: Single source of truth for the version. pyproject.toml reads this attribute
#: rather than restating it, so a released package can never disagree with what
#: the health endpoint tells an operator it is running.
PRODUCT_VERSION = "0.13.0"
MAX_ALTERNATIVES = 100


def _number(raw: dict[str, Any], name: str, *, default: float | None = None,
            minimum: float | None = None, maximum: float | None = None) -> float:
    value = raw.get(name, default)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _numeric_series(value: Any, name: str, count: int, *,
                    minimum: float | None = None,
                    maximum: float | None = None) -> list[float]:
    values = value if isinstance(value, list) else [value] * count
    if len(values) != count:
        raise ValueError(f"{name} must contain exactly {count} values")
    result = []
    for index, item in enumerate(values):
        row = {name: item}
        try:
            result.append(_number(
                row, name, minimum=minimum, maximum=maximum,
            ))
        except ValueError as exc:
            raise ValueError(f"{name}[{index}]: {exc}") from exc
    return result


def _energy_profile(facility_raw: dict[str, Any], context: MarketContext,
                    max_power_kw: float,
                    dispatch_priority: str) -> SiteEnergyProfile | None:
    """Build the optional physical energy-supply model for one facility."""
    energy_keys = {
        "energy_sources", "base_load_kw", "base_load_profile_kw",
        "pue_profile", "battery", "site",
    }
    if not energy_keys.intersection(facility_raw):
        return None
    count = len(context.series)
    stamps = [point.timestamp for point in context.series]
    site_raw = facility_raw.get("site")
    site = None
    if site_raw is not None:
        if not isinstance(site_raw, dict):
            raise ValueError("facility.site must be an object")
        site_id = site_raw.get("site_id")
        site_name = site_raw.get("name")
        if not isinstance(site_id, str) or not site_id.strip():
            raise ValueError("facility.site.site_id is required")
        if not isinstance(site_name, str) or not site_name.strip():
            raise ValueError("facility.site.name is required")
        connection_id = site_raw.get("grid_connection_id", "")
        time_zone = site_raw.get("time_zone", "UTC")
        if not isinstance(connection_id, str):
            raise ValueError("facility.site.grid_connection_id must be a string")
        if not isinstance(time_zone, str):
            raise ValueError("facility.site.time_zone must be a string")
        site = FacilitySite(
            site_id=site_id,
            name=site_name,
            latitude=_number(
                site_raw, "latitude", minimum=-90, maximum=90,
            ),
            longitude=_number(
                site_raw, "longitude", minimum=-180, maximum=180,
            ),
            grid_connection_id=connection_id,
            time_zone=time_zone,
        )
    base_raw = facility_raw.get(
        "base_load_profile_kw", facility_raw.get("base_load_kw", 0),
    )
    base_loads = _numeric_series(
        base_raw, "base_load_profile_kw", count, minimum=0,
        maximum=max_power_kw,
    )
    pue_raw = facility_raw.get("pue_profile")
    pue_values = (
        [None] * count
        if pue_raw is None
        else _numeric_series(pue_raw, "pue_profile", count, minimum=1, maximum=5)
    )

    sources_raw = facility_raw.get("energy_sources", [])
    if not isinstance(sources_raw, list):
        raise ValueError("energy_sources must be a list")
    if len(sources_raw) > 30:
        raise ValueError("energy_sources cannot contain more than 30 entries")
    sources: list[EnergySource] = []
    seen_ids = {"grid"}
    for index, raw in enumerate(sources_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"energy_sources[{index}] must be an object")
        source_id = raw.get("source_id")
        name = raw.get("name")
        kind = raw.get("kind")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"energy_sources[{index}] needs source_id")
        if source_id in seen_ids:
            raise ValueError("energy source IDs must be unique; grid is reserved")
        seen_ids.add(source_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"energy source {source_id!r} needs name")
        if not isinstance(kind, str) or kind == "grid":
            raise ValueError(
                f"energy source {source_id!r} needs a non-grid kind"
            )

        if "availability_kw" in raw:
            availability = _numeric_series(
                raw["availability_kw"], "availability_kw", count, minimum=0,
            )
        else:
            capacity = _number(raw, "capacity_kw", minimum=0)
            factors = _numeric_series(
                raw.get("capacity_factors", 1), "capacity_factors", count,
                minimum=0, maximum=1,
            )
            availability = [capacity * factor for factor in factors]
        if raw.get("interconnection_limit_kw") is not None:
            limit = _number(raw, "interconnection_limit_kw", minimum=0)
            availability = [min(value, limit) for value in availability]
        costs = _numeric_series(
            raw.get("cost_per_mwh", 0), "cost_per_mwh", count,
        )
        carbons = _numeric_series(
            raw.get("carbon_g_per_kwh", 0), "carbon_g_per_kwh", count,
            minimum=0,
        )
        confidence = _numeric_series(
            raw.get("confidence", 1), "confidence", count,
            minimum=0, maximum=1,
        )
        renewable = raw.get("renewable")
        if renewable is not None and not isinstance(renewable, bool):
            raise ValueError("renewable must be boolean when supplied")
        carbon_free = raw.get("carbon_free", False)
        dispatchable = raw.get("dispatchable", kind not in {"solar", "wind"})
        if not isinstance(carbon_free, bool) or not isinstance(dispatchable, bool):
            raise ValueError("carbon_free and dispatchable must be boolean")
        delivery_type = raw.get("delivery_type", "onsite")
        provenance = raw.get("provenance", "ESTIMATED")
        if not isinstance(delivery_type, str):
            raise ValueError("delivery_type must be a string")
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("energy source provenance is required")
        latitude_raw = raw.get("latitude")
        longitude_raw = raw.get("longitude")
        if (latitude_raw is None) != (longitude_raw is None):
            raise ValueError(
                f"energy source {source_id!r} latitude and longitude "
                "must be supplied together"
            )
        latitude = (
            None if latitude_raw is None
            else _number(raw, "latitude", minimum=-90, maximum=90)
        )
        longitude = (
            None if longitude_raw is None
            else _number(raw, "longitude", minimum=-180, maximum=180)
        )
        connection_id = raw.get("grid_connection_id", "")
        if not isinstance(connection_id, str):
            raise ValueError("energy source grid_connection_id must be a string")
        sources.append(EnergySource(
            source_id=source_id,
            name=name,
            kind=kind,
            points=tuple(
                SupplyPoint(stamp, available, cost, carbon, certainty)
                for stamp, available, cost, carbon, certainty in zip(
                    stamps, availability, costs, carbons, confidence,
                )
            ),
            renewable=renewable,
            carbon_free=carbon_free,
            delivery_type=delivery_type,
            dispatchable=dispatchable,
            provenance=provenance,
            latitude=latitude,
            longitude=longitude,
            grid_connection_id=connection_id,
            delivery_loss_fraction=_number(
                raw, "delivery_loss_fraction", default=0,
                minimum=0, maximum=0.999999,
            ),
        ))

    grid_points = []
    missing_grid_signals = 0
    for point in context.series:
        if point.price is None or point.carbon_intensity is None:
            missing_grid_signals += 1
        grid_points.append(SupplyPoint(
            point.timestamp, max_power_kw,
            point.price if point.price is not None else 0,
            point.carbon_intensity if point.carbon_intensity is not None else 0,
            1,
        ))
    sources.append(EnergySource(
        source_id="grid",
        name="Residual grid",
        kind="grid",
        points=tuple(grid_points),
        renewable=False,
        carbon_free=False,
        delivery_type="grid",
        dispatchable=True,
        grid_connection_id=site.grid_connection_id if site else "",
        provenance=(
            context.provenance
            + (
                f" {missing_grid_signals} incomplete market interval(s) use "
                "base-load-only accounting placeholders; AI placement still "
                "requires complete price and carbon signals."
                if missing_grid_signals else ""
            )
        ),
    ))

    battery_raw = facility_raw.get("battery")
    battery = None
    if battery_raw is not None:
        if not isinstance(battery_raw, dict):
            raise ValueError("battery must be an object")
        battery = BatterySpec(
            capacity_kwh=_number(battery_raw, "capacity_kwh", minimum=0),
            max_charge_kw=_number(battery_raw, "max_charge_kw", minimum=0),
            max_discharge_kw=_number(
                battery_raw, "max_discharge_kw", minimum=0,
            ),
            initial_energy_kwh=_number(
                battery_raw, "initial_energy_kwh", default=0, minimum=0,
            ),
            round_trip_efficiency=_number(
                battery_raw, "round_trip_efficiency", default=0.9,
                minimum=0.000001, maximum=1,
            ),
            initial_cost_per_mwh=_number(
                battery_raw, "initial_cost_per_mwh", default=0, minimum=0,
            ),
            initial_carbon_g_per_kwh=_number(
                battery_raw, "initial_carbon_g_per_kwh", default=0, minimum=0,
            ),
            initial_renewable_fraction=_number(
                battery_raw, "initial_renewable_fraction", default=0,
                minimum=0, maximum=1,
            ),
            initial_carbon_free_fraction=_number(
                battery_raw, "initial_carbon_free_fraction", default=0,
                minimum=0, maximum=1,
            ),
        )

    return SiteEnergyProfile(
        market=context.market_key,
        location=context.location_name,
        facility_points=tuple(
            FacilityPoint(stamp, base, pue)
            for stamp, base, pue in zip(stamps, base_loads, pue_values)
        ),
        sources=tuple(sources),
        battery=battery,
        dispatch_priority=dispatch_priority,
        site=site,
    )


def _energy_dispatch_response(dispatch: EnergyDispatchResult) -> dict[str, Any]:
    return {
        "market": dispatch.market,
        "location": dispatch.location,
        "site": asdict(dispatch.site) if dispatch.site else None,
        "base_energy_kwh": dispatch.base_energy_kwh,
        "ai_energy_kwh": dispatch.ai_energy_kwh,
        "ai_renewable_kwh": dispatch.ai_renewable_kwh,
        "ai_renewable_match_pct": dispatch.ai_renewable_match_pct,
        "ai_carbon_free_kwh": dispatch.ai_carbon_free_kwh,
        "ai_carbon_free_match_pct": dispatch.ai_carbon_free_match_pct,
        "ai_grid_kwh": dispatch.ai_grid_kwh,
        "ai_battery_kwh": dispatch.ai_battery_kwh,
        "ai_cost": dispatch.ai_cost,
        "ai_carbon_kg": dispatch.ai_carbon_kg,
        "curtailed_kwh": dispatch.curtailed_kwh,
        "final_battery_kwh": dispatch.final_battery_kwh,
        "feasible": dispatch.feasible,
        "sources": [
            {
                "source_id": source_id,
                **asdict(source),
            }
            for source_id, source in sorted(dispatch.sources.items())
        ],
        "intervals": [
            {
                **asdict(row),
                "timestamp": row.timestamp.isoformat(),
            }
            for row in dispatch.intervals
        ],
    }


def _spatial_precision(context: MarketContext,
                       site: FacilitySite | None) -> dict[str, Any]:
    if context.market_key == "CAISO":
        price_scope = "pricing_node"
        carbon_scope = "balancing_area"
    elif context.market_key == "NYISO":
        price_scope = "zone"
        carbon_scope = "balancing_area"
    else:
        price_scope = "national"
        carbon_scope = (
            "national" if context.location_key == "national" else "grid_region"
        )
    return {
        "physical_site_scope": (
            "exact_wgs84_coordinates" if site else "market_location_only"
        ),
        "facility_site": asdict(site) if site else None,
        "price_signal_scope": price_scope,
        "carbon_signal_scope": carbon_scope,
        "price_signal_label": context.price_label,
        "carbon_signal_label": context.carbon_label,
        "decision_interval_minutes": 30,
        "provider_native_resolution_minutes": (
            60 if context.market_key in {"CAISO", "NYISO"} else 30
        ),
        "claim_boundary": (
            "Exact coordinates identify the physical site and source geometry. "
            "Electricity price and carbon retain the spatial resolution of "
            "their named provider feeds and are not inferred at site level."
        ),
    }


def _option(option: PlanOption) -> dict[str, Any]:
    candidate = option.candidate
    return {
        "candidate_key": candidate.key,
        "hardware": candidate.hardware,
        "hardware_provenance": candidate.hardware_provenance,
        "market": candidate.market,
        "location": candidate.location,
        "grid_provenance": candidate.grid_provenance,
        "start": option.start_time.isoformat(),
        "finish": option.finish_time.isoformat(),
        "runtime_hours": candidate.runtime_hours,
        "delay_hours": option.delay_hours,
        "it_power_kw": candidate.it_power_kw,
        "pue": candidate.pue,
        "facility_energy_kwh": option.facility_energy_kwh,
        "cost": option.cost,
        "currency": candidate.currency,
        "carbon_kg": option.carbon_kg,
        "score": option.score if math.isfinite(option.score) else None,
        "pareto": option.pareto,
        "notes": list(candidate.notes),
    }


def plan_response(payload: dict[str, Any], context: MarketContext,
                  calibrations: list[CalibrationProfile] | None = None
                  ) -> dict[str, Any]:
    """Validate one request, run the canonical estimator and serialise it."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    workload_raw = payload.get("workload")
    planning_raw = payload.get("planning")
    if not isinstance(workload_raw, dict) or not isinstance(planning_raw, dict):
        raise ValueError("workload and planning objects are required")

    try:
        workload = WorkloadSpec(**workload_raw)
        request = PlanningRequest(**planning_raw)
    except TypeError as exc:
        raise ValueError(f"invalid planning field: {exc}") from exc

    device_keys = payload.get("device_keys")
    if device_keys is not None:
        if not isinstance(device_keys, list) or not all(
            isinstance(key, str) for key in device_keys
        ):
            raise ValueError("device_keys must be a list of strings")
        if not device_keys:
            raise ValueError("device_keys cannot be empty")
        device_keys = list(dict.fromkeys(device_keys))
        if len(device_keys) > 200:
            raise ValueError("device_keys cannot contain more than 200 entries")

    location = GridLocation(
        market=context.market_key,
        location=context.location_name,
        series=context.series,
        currency=context.currency,
        provenance=context.provenance,
    )
    result = plan_workload(
        workload, [location], request, device_keys, calibrations
    )
    candidate_snapshot = {}
    for option in result.alternatives:
        candidate = option.candidate
        candidate_snapshot.setdefault(candidate.key, {
            "key": candidate.key,
            "hardware": candidate.hardware,
            "market": candidate.market,
            "location": candidate.location,
            "runtime_hours": candidate.runtime_hours,
            "it_power_kw": candidate.it_power_kw,
            "pue": candidate.pue,
            "memory_ok": candidate.memory_ok,
            "currency": candidate.currency,
            "hardware_provenance": candidate.hardware_provenance,
            "grid_provenance": candidate.grid_provenance,
            "notes": list(candidate.notes),
        })
    return {
        "api_version": API_VERSION,
        "product_version": PRODUCT_VERSION,
        "algorithm": "exact-enumeration-v1",
        "signal_mode": context.signal_mode,
        "market": {
            "key": context.market_key,
            "name": context.market_name,
            "location_key": context.location_key,
            "location_name": context.location_name,
            "currency": context.currency,
            "price_label": context.price_label,
            "carbon_label": context.carbon_label,
            "provenance": context.provenance,
        },
        "workload": asdict(workload),
        "planning": asdict(request),
        "selected": _option(result.selected),
        "alternatives": [
            _option(option) for option in result.alternatives[:MAX_ALTERNATIVES]
        ],
        "feasible_count": len(result.alternatives),
        "frontier_count": len(result.frontier),
        "alternatives_truncated": len(result.alternatives) > MAX_ALTERNATIVES,
        "rejected": result.rejected,
        "candidate_snapshot": list(candidate_snapshot.values()),
    }


def market_response(context: MarketContext) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "product_version": PRODUCT_VERSION,
        "market": context.market_key,
        "location": context.location_key,
        "currency": context.currency,
        "price_label": context.price_label,
        "carbon_label": context.carbon_label,
        "signal_mode": context.signal_mode,
        "provenance": context.provenance,
        "points": [
            {
                "timestamp": point.timestamp.isoformat(),
                "price": point.price,
                "carbon_intensity_g_per_kwh": point.carbon_intensity,
                "carbon_method": getattr(point, "carbon_method", None),
            }
            for point in context.series
        ],
    }


def _expand_checkpoint_jobs(jobs_raw: list[Any]) -> list[Any]:
    """Expand explicitly checkpointable jobs into schedulable restart chunks."""
    identifiers = [
        raw.get("job_id") if isinstance(raw, dict) else None
        for raw in jobs_raw
    ]
    valid_ids = [value for value in identifiers if isinstance(value, str)]
    if len(valid_ids) != len(set(valid_ids)):
        raise ValueError("portfolio job IDs must be unique")
    counts: dict[str, int] = {}
    for index, raw in enumerate(jobs_raw):
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("job_id")
        checkpointable = raw.get("checkpointable", False)
        if not isinstance(checkpointable, bool):
            raise ValueError("checkpointable must be boolean")
        default_count = 2 if checkpointable else 1
        count = raw.get("checkpoint_count", default_count)
        if (isinstance(count, bool) or not isinstance(count, int)
                or not 1 <= count <= 24):
            raise ValueError("checkpoint_count must be an integer in [1, 24]")
        if count > 1 and not checkpointable:
            raise ValueError(
                f"job {job_id or index!r} must be checkpointable to use multiple chunks"
            )
        if isinstance(job_id, str):
            counts[job_id] = count

    def final_id(job_id: str) -> str:
        count = counts.get(job_id, 1)
        return (
            f"{job_id}-checkpoint-{count}"
            if count > 1 else job_id
        )

    expanded: list[Any] = []
    for raw in jobs_raw:
        if not isinstance(raw, dict):
            expanded.append(raw)
            continue
        job_id = raw.get("job_id")
        count = counts.get(job_id, 1) if isinstance(job_id, str) else 1
        dependencies = raw.get("depends_on", [])
        remapped = (
            [final_id(value) for value in dependencies]
            if isinstance(dependencies, list)
            else dependencies
        )
        if count == 1:
            item = deepcopy(raw)
            item["depends_on"] = remapped
            item["parent_job_id"] = job_id
            item["checkpoint_index"] = 1
            item["checkpoint_count"] = 1
            expanded.append(item)
            continue
        work_amount = _number(raw, "work_amount", minimum=0.000001)
        utility = _number(raw, "utility", minimum=0.000001)
        variants = raw.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"job {job_id!r} needs variants before checkpointing")
        stage_name = raw.get("stage_name", raw.get("workload_class", job_id))
        for chunk_index in range(1, count + 1):
            item = deepcopy(raw)
            chunk_id = f"{job_id}-checkpoint-{chunk_index}"
            item["job_id"] = chunk_id
            item["parent_job_id"] = job_id
            item["checkpoint_index"] = chunk_index
            item["checkpoint_count"] = count
            item["work_amount"] = work_amount / count
            item["utility"] = utility / count
            item["stage_name"] = (
                f"{stage_name} · checkpoint {chunk_index}/{count}"
            )
            item["depends_on"] = (
                remapped if chunk_index == 1
                else [f"{job_id}-checkpoint-{chunk_index - 1}"]
            )
            for variant in item["variants"]:
                if not isinstance(variant, dict):
                    continue
                runtime = _number(
                    variant, "runtime_hours", minimum=0.000001,
                )
                variant["runtime_hours"] = runtime / count
                key = variant.get("candidate_key")
                if isinstance(key, str):
                    variant["candidate_key"] = f"{key}-checkpoint-{chunk_index}"
            expanded.append(item)
    if len(expanded) > 50:
        raise ValueError("checkpoint expansion cannot exceed 50 schedulable chunks")
    return expanded


def portfolio_response(payload: dict[str, Any], context: MarketContext,
                       evidence_profiles: dict[str, EvidenceProfile] | None = None,
                       ) -> dict[str, Any]:
    """Validate and schedule a capacity-constrained AI workload queue."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    jobs_raw = payload.get("jobs")
    facility_raw = payload.get("facility")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise ValueError("jobs must be a non-empty list")
    if len(jobs_raw) > 50:
        raise ValueError("jobs cannot contain more than 50 entries")
    jobs_raw = _expand_checkpoint_jobs(jobs_raw)
    if not isinstance(facility_raw, dict):
        raise ValueError("facility must be an object")
    if not context.series:
        raise ValueError("selected market/location has no grid signal")

    energy_priority = facility_raw.get("energy_priority", "renewable")
    if not isinstance(energy_priority, str):
        raise ValueError("energy_priority must be a string")
    max_power_kw = _number(facility_raw, "max_power_kw", minimum=0.000001)
    energy_profile = _energy_profile(
        facility_raw, context, max_power_kw, energy_priority,
    )
    policy_values: dict[str, Any] = {}
    for source, target in (
        ("max_total_cost", "max_total_cost"),
        ("max_total_carbon_kg", "max_total_carbon_kg"),
    ):
        if facility_raw.get(source) is not None:
            policy_values[target] = _number(facility_raw, source, minimum=0)
    search_limit = facility_raw.get("max_search_combinations", 1_000_000)
    if (isinstance(search_limit, bool) or not isinstance(search_limit, int)
            or not 0 < search_limit <= 10_000_000):
        raise ValueError(
            "max_search_combinations must be an integer in [1, 10000000]"
        )

    origin = context.series[0].timestamp
    evidence_profiles = evidence_profiles or {}
    jobs: list[PortfolioJob] = []
    quality_rejected: dict[str, list[str]] = {}
    eligible_variant_counts: dict[str, int] = {}
    job_metadata: dict[str, dict[str, Any]] = {}
    variant_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for index, job_raw in enumerate(jobs_raw):
        if not isinstance(job_raw, dict):
            raise ValueError(f"jobs[{index}] must be an object")
        job_id = job_raw.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError(f"jobs[{index}].job_id is required")
        workload_class = job_raw.get("workload_class", job_id)
        run_mode = job_raw.get("run_mode", "inference")
        workflow_id = job_raw.get("workflow_id", job_id)
        stage_name = job_raw.get("stage_name", workload_class)
        if not isinstance(workload_class, str) or not workload_class.strip():
            raise ValueError(f"job {job_id!r} needs workload_class")
        if run_mode not in {"inference", "evaluation", "fine_tuning", "training"}:
            raise ValueError(
                "run_mode must be inference, evaluation, fine_tuning or training"
            )
        for field_name, field_value in (
            ("workflow_id", workflow_id),
            ("stage_name", stage_name),
        ):
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"job {job_id!r} needs {field_name}")
        depends_raw = job_raw.get("depends_on", [])
        if (not isinstance(depends_raw, list)
                or not all(isinstance(value, str) and value.strip()
                           for value in depends_raw)):
            raise ValueError("depends_on must be a list of non-empty job IDs")
        depends_on = tuple(depends_raw)
        earliest_delay = _number(
            job_raw, "earliest_delay_hours", default=0, minimum=0,
        )
        deadline_hours = _number(
            job_raw, "deadline_hours", minimum=0.000001,
        )
        if earliest_delay >= deadline_hours:
            raise ValueError(f"job {job_id!r} must start before its deadline")
        minimum_quality = _number(
            job_raw, "minimum_quality", default=0, minimum=0, maximum=1,
        )
        work_amount = _number(job_raw, "work_amount", minimum=0.000001)
        work_unit = str(job_raw.get("work_unit", ""))
        require_measured = job_raw.get("require_measured_quality", True)
        if not isinstance(require_measured, bool):
            raise ValueError("require_measured_quality must be boolean")
        variants_raw = job_raw.get("variants")
        auto_evidence_profiles = job_raw.get("auto_evidence_profiles", False)
        if not isinstance(auto_evidence_profiles, bool):
            raise ValueError("auto_evidence_profiles must be boolean")
        if auto_evidence_profiles:
            compatible_profiles = sorted(
                (
                    profile for profile in evidence_profiles.values()
                    if profile.workload_class == workload_class
                    and profile.run_mode == run_mode
                    and profile.work_unit == work_unit
                ),
                key=lambda profile: profile.profile_id,
            )
            if not compatible_profiles:
                raise ValueError(
                    f"job {job_id!r} has no compatible governed evidence profiles"
                )
            variants_raw = [
                {
                    "candidate_key": f"{job_id}:{profile.profile_id}",
                    "evidence_profile_id": profile.profile_id,
                }
                for profile in compatible_profiles
            ]
            require_measured = True
        if not isinstance(variants_raw, list) or not variants_raw:
            raise ValueError(f"job {job_id!r} needs a non-empty variants list")
        if len(variants_raw) > 100:
            raise ValueError(f"job {job_id!r} cannot exceed 100 variants")

        candidates: list[PlanningCandidate] = []
        seen_variants: set[str] = set()
        candidate_evidence_profiles: list[EvidenceProfile] = []
        candidate_quality_fingerprints: set[tuple[str, str]] = set()
        for variant_index, variant_raw in enumerate(variants_raw):
            if not isinstance(variant_raw, dict):
                raise ValueError(
                    f"job {job_id!r} variant {variant_index} must be an object"
                )
            variant_values = dict(variant_raw)
            evidence_profile_id = variant_values.get("evidence_profile_id")
            measured_profile = None
            if evidence_profile_id is not None:
                if not isinstance(evidence_profile_id, str) or not evidence_profile_id:
                    raise ValueError("evidence_profile_id must be a non-empty string")
                measured_profile = evidence_profiles.get(evidence_profile_id)
                if measured_profile is None:
                    raise ValueError(
                        f"unknown measured evidence profile {evidence_profile_id!r}"
                    )
                if measured_profile.workload_class != workload_class:
                    raise ValueError(
                        f"evidence profile {evidence_profile_id!r} is for workload "
                        f"class {measured_profile.workload_class!r}, not {workload_class!r}"
                    )
                if measured_profile.run_mode != run_mode:
                    raise ValueError(
                        f"evidence profile {evidence_profile_id!r} is for run mode "
                        f"{measured_profile.run_mode!r}, not {run_mode!r}"
                    )
                if measured_profile.work_unit != work_unit:
                    raise ValueError(
                        f"evidence profile {evidence_profile_id!r} measures "
                        f"{measured_profile.work_unit!r}, not {work_unit!r}"
                    )
                derived = {
                    "hardware": (
                        f"{measured_profile.device_key} "
                        f"({measured_profile.compute_unit})"
                    ),
                    "model_id": measured_profile.model_id,
                    "model_version": measured_profile.model_version,
                    "precision": measured_profile.precision,
                    "compute_unit": measured_profile.compute_unit,
                    "runtime_hours": (
                        work_amount / measured_profile.work_rate_per_second / 3600
                    ),
                    "it_power_kw": measured_profile.average_it_power_watts / 1000,
                    "quality_score": measured_profile.quality_score,
                    "quality_provenance": "MEASURED",
                    "evaluation_suite": measured_profile.quality_suite,
                    "evaluation_version": measured_profile.quality_suite_version,
                    "hardware_provenance": "MEASURED",
                }
                for field_name, expected in derived.items():
                    if field_name in variant_values:
                        supplied = variant_values[field_name]
                        if isinstance(expected, float):
                            matches = (
                                isinstance(supplied, (int, float))
                                and not isinstance(supplied, bool)
                                and math.isclose(float(supplied), expected,
                                                 rel_tol=1e-9, abs_tol=1e-12)
                            )
                        else:
                            matches = supplied == expected
                        if not matches:
                            raise ValueError(
                                f"{field_name} conflicts with measured profile "
                                f"{evidence_profile_id!r}"
                            )
                    variant_values[field_name] = expected
                if variant_values.get("memory_available_gb") is not None:
                    variant_values["memory_required_gb"] = (
                        measured_profile.peak_memory_mb / 1024
                    )
                candidate_evidence_profiles.append(measured_profile)

            candidate_key = variant_values.get("candidate_key")
            hardware = variant_values.get("hardware")
            if not isinstance(candidate_key, str) or not candidate_key.strip():
                raise ValueError(f"job {job_id!r} variant needs candidate_key")
            if candidate_key in seen_variants:
                raise ValueError(f"job {job_id!r} variant keys must be unique")
            seen_variants.add(candidate_key)
            if not isinstance(hardware, str) or not hardware.strip():
                raise ValueError(f"job {job_id!r} variant needs hardware")
            model_id = variant_values.get("model_id", candidate_key)
            model_version = variant_values.get("model_version", "unspecified")
            precision = variant_values.get("precision", "unspecified")
            compute_unit = variant_values.get("compute_unit", "unspecified")
            for field_name, field_value in (
                ("model_id", model_id),
                ("model_version", model_version),
                ("precision", precision),
                ("compute_unit", compute_unit),
            ):
                if not isinstance(field_value, str) or not field_value.strip():
                    raise ValueError(
                        f"job {job_id!r} variant needs {field_name}"
                    )
            quality_score = _number(
                variant_values, "quality_score", minimum=0, maximum=1,
            )
            quality_provenance = variant_values.get("quality_provenance", "MEASURED")
            if quality_provenance not in {"MEASURED", "ESTIMATED"}:
                raise ValueError(
                    "quality_provenance must be MEASURED or ESTIMATED"
                )
            evaluation_suite = variant_values.get("evaluation_suite")
            evaluation_version = variant_values.get("evaluation_version")
            if (not isinstance(evaluation_suite, str) or not evaluation_suite.strip()
                    or not isinstance(evaluation_version, str)
                    or not evaluation_version.strip()):
                raise ValueError("each variant needs a versioned evaluation suite")
            if require_measured and quality_provenance != "MEASURED":
                quality_rejected.setdefault(job_id, []).append(
                    f"{candidate_key}: quality evidence is not measured"
                )
                continue
            if quality_score < minimum_quality:
                quality_rejected.setdefault(job_id, []).append(
                    f"{candidate_key}: quality {quality_score:.3f} is below "
                    f"{minimum_quality:.3f}"
                )
                continue
            candidate_quality_fingerprints.add(
                (evaluation_suite, evaluation_version)
            )
            hardware_provenance = variant_values.get(
                "hardware_provenance", "ESTIMATED"
            )
            if hardware_provenance not in {"MEASURED", "ESTIMATED", "SPEC"}:
                raise ValueError(
                    "hardware_provenance must be MEASURED, ESTIMATED or SPEC"
                )
            runtime_hours = _number(
                variant_values, "runtime_hours", minimum=0.000001,
            )
            it_power_kw = _number(
                variant_values, "it_power_kw", minimum=0,
            )
            pue = _number(
                variant_values, "pue", default=1.2, minimum=1, maximum=5,
            )
            memory_required = variant_values.get("memory_required_gb")
            memory_available = variant_values.get("memory_available_gb")
            if (memory_required is None) != (memory_available is None):
                raise ValueError(
                    "memory_required_gb and memory_available_gb must be supplied together"
                )
            if memory_required is not None:
                memory_required = _number(
                    variant_values, "memory_required_gb", minimum=0,
                )
                memory_available = _number(
                    variant_values, "memory_available_gb", minimum=0,
                )
            memory_ok = (
                True if memory_required is None
                else memory_required <= memory_available
            )
            candidates.append(PlanningCandidate(
                key=candidate_key,
                hardware=hardware,
                market=context.market_key,
                location=context.location_name,
                series=context.series,
                runtime_hours=runtime_hours,
                it_power_kw=it_power_kw,
                pue=pue,
                memory_ok=memory_ok,
                currency=context.currency,
                hardware_provenance=hardware_provenance,
                grid_provenance=context.provenance,
                notes=(
                    f"Model {model_id} version {model_version}",
                    f"Run mode {run_mode}; precision {precision}; compute {compute_unit}",
                    f"Quality {quality_score:.3f} from "
                    f"{evaluation_suite} {evaluation_version}",
                    f"Quality provenance {quality_provenance}",
                    (f"Memory {memory_required:g}/{memory_available:g} GB"
                     if memory_required is not None else "Memory not supplied"),
                    (
                        f"Measured evidence profile {evidence_profile_id}; "
                        f"energy {measured_profile.energy_method} / "
                        f"{measured_profile.energy_scope}"
                        if measured_profile else "No governed evidence profile"
                    ),
                ),
            ))
            variant_metadata[(job_id, candidate_key)] = {
                "model_id": model_id,
                "model_version": model_version,
                "precision": precision,
                "compute_unit": compute_unit,
                "quality_score": quality_score,
                "quality_provenance": quality_provenance,
                "evaluation_suite": evaluation_suite,
                "evaluation_version": evaluation_version,
                "memory_required_gb": memory_required,
                "memory_available_gb": memory_available,
                "memory_ok": memory_ok,
                "evidence_profile_id": evidence_profile_id,
                "energy_method": (
                    measured_profile.energy_method if measured_profile else None
                ),
                "energy_scope": (
                    measured_profile.energy_scope if measured_profile else None
                ),
                "energy_provenance": (
                    measured_profile.energy_provenance
                    if measured_profile else None
                ),
                "evidence_sample_count": (
                    measured_profile.sample_count if measured_profile else None
                ),
                "evidence_throughput_relative_mad": (
                    measured_profile.throughput_relative_mad
                    if measured_profile else None
                ),
                "evidence_energy_relative_mad": (
                    measured_profile.energy_relative_mad
                    if measured_profile else None
                ),
            }
        if len(candidate_quality_fingerprints) > 1:
            raise ValueError(
                "quality scores cannot be ranked across different evaluation "
                "suite versions"
            )
        if (len(candidates) > 1 and candidate_evidence_profiles
                and any(not profile.cross_device_comparable
                        for profile in candidate_evidence_profiles)):
            device_keys = {profile.device_key for profile in candidate_evidence_profiles}
            energy_scopes = {
                (profile.energy_method, profile.energy_scope,
                 profile.energy_provenance)
                for profile in candidate_evidence_profiles
            }
            if (len(device_keys) > 1 or len(energy_scopes) > 1
                    or len(candidate_evidence_profiles) < len(candidates)):
                raise ValueError(
                    "same-device Apple subsystem energy cannot be ranked against "
                    "another device, measurement scope or unscoped manual variant"
                )
        if not candidates:
            detail = "; ".join(quality_rejected.get(job_id, []))
            raise ValueError(
                f"job {job_id!r} has no quality-eligible variants"
                + (f": {detail}" if detail else "")
            )
        eligible_variant_counts[job_id] = len(candidates)
        mandatory = job_raw.get("mandatory", True)
        if not isinstance(mandatory, bool):
            raise ValueError("mandatory must be boolean")
        try:
            job_metadata[job_id] = {
                "workload_class": workload_class,
                "run_mode": run_mode,
                "workflow_id": workflow_id,
                "stage_name": stage_name,
                "depends_on": list(depends_on),
                "parent_job_id": job_raw.get("parent_job_id", job_id),
                "checkpoint_index": job_raw.get("checkpoint_index", 1),
                "checkpoint_count": job_raw.get("checkpoint_count", 1),
            }
            jobs.append(PortfolioJob(
                job_id=job_id,
                candidates=tuple(candidates),
                earliest_start=origin + timedelta(hours=earliest_delay),
                deadline=origin + timedelta(hours=deadline_hours),
                work_amount=work_amount,
                work_unit=work_unit,
                utility=_number(job_raw, "utility", minimum=0.000001),
                mandatory=mandatory,
                depends_on=depends_on,
            ))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid job {job_id!r}: {exc}") from exc

    # A declared power envelope makes on-site generation a throughput input,
    # not only a price one: the ceiling rises while the site's own generation
    # runs, so heavy work placed there runs at full power instead of being
    # throttled behind a flat limit. Absent a declaration the ceiling is the
    # flat `max_power_kw` it has always been.
    envelope_raw = facility_raw.get("power_profile_kw")
    envelope: tuple[tuple[datetime, float], ...] = ()
    if envelope_raw is not None:
        values = _numeric_series(
            envelope_raw, "power_profile_kw", len(context.series),
            minimum=0, maximum=max_power_kw,
        )
        envelope = tuple(
            (point.timestamp, value)
            for point, value in zip(context.series, values)
        )
    capacities = (SiteCapacity(
        context.market_key, context.location_name, max_power_kw,
        power_profile_kw=envelope,
    ),)
    energy_profiles = (energy_profile,) if energy_profile else ()
    result = optimise_portfolio(
        jobs,
        PortfolioPolicy(
            capacities=capacities,
            max_search_combinations=search_limit,
            energy_profiles=energy_profiles,
            energy_priority=energy_priority,
            **policy_values,
        ),
    )
    baseline = schedule_earliest(
        jobs,
        PortfolioPolicy(
            capacities=capacities,
            energy_profiles=energy_profiles,
            energy_priority=energy_priority,
        ),
    )
    energy_by_job = {
        job_id: totals
        for dispatch in result.energy_dispatches
        for job_id, totals in dispatch.jobs.items()
    }
    baseline_dispatch = (
        baseline.energy_dispatches[0] if baseline.energy_dispatches else None
    )

    def serialise_assignment(assignment) -> dict[str, Any]:
        job = assignment.job
        option = assignment.option
        energy = energy_by_job.get(job.job_id)
        option_values = _option(option)
        if energy is not None:
            option_values.update({
                "facility_energy_kwh": energy.energy_kwh,
                "cost": energy.cost,
                "carbon_kg": energy.carbon_kg,
                "renewable_kwh": energy.renewable_kwh,
                "renewable_match_pct": energy.renewable_match_pct,
                "carbon_free_kwh": energy.carbon_free_kwh,
                "carbon_free_match_pct": energy.carbon_free_match_pct,
                "grid_kwh": energy.grid_kwh,
                "battery_kwh": energy.battery_kwh,
                "source_kwh": energy.source_kwh,
            })
        energy_kwh = option_values["facility_energy_kwh"]
        carbon_kg = option_values["carbon_kg"]
        cost = option_values["cost"]
        return {
            "job_id": job.job_id,
            **job_metadata[job.job_id],
            **variant_metadata[(job.job_id, option.candidate.key)],
            "work_amount": job.work_amount,
            "work_unit": job.work_unit,
            "utility": job.utility,
            "energy_wh_per_work_unit": (
                energy_kwh * 1000 / job.work_amount
            ),
            "carbon_g_per_work_unit": (
                carbon_kg * 1000 / job.work_amount
            ),
            "cost_per_work_unit": cost / job.work_amount,
            **option_values,
        }

    energy_objectives = {
        "renewable": [
            "renewable_energy_match_pct_desc",
            "carbon_free_energy_match_pct_desc",
            "operational_carbon_asc", "electricity_cost_asc",
        ],
        "carbon_free": [
            "carbon_free_energy_match_pct_desc",
            "renewable_energy_match_pct_desc",
            "operational_carbon_asc", "electricity_cost_asc",
        ],
        "carbon": [
            "operational_carbon_asc", "renewable_energy_match_pct_desc",
            "carbon_free_energy_match_pct_desc", "electricity_cost_asc",
        ],
        "cost": [
            "electricity_cost_asc", "operational_carbon_asc",
            "renewable_energy_match_pct_desc",
            "carbon_free_energy_match_pct_desc",
        ],
    }
    optimised_dispatch = (
        result.energy_dispatches[0] if result.energy_dispatches else None
    )

    def clean_delta(value: float) -> float:
        return 0.0 if abs(value) < 1e-9 else value

    counterfactual = {
        "method": "deterministic-earliest-feasible-v1",
        "description": (
            "Same admitted, quality-qualified workflow started as soon as "
            "dependencies, deadlines and facility capacity allow; energy "
            "outcomes are not optimised and policy caps are not applied."
        ),
        "assignments": [
            {
                "job_id": assignment.job.job_id,
                "start": assignment.option.start_time.isoformat(),
                "finish": assignment.option.finish_time.isoformat(),
            }
            for assignment in baseline.assignments
        ],
        "total_energy_kwh": baseline.total_energy_kwh,
        "total_cost": baseline.total_cost,
        "total_carbon_kg": baseline.total_carbon_kg,
        "total_delay_hours": baseline.total_delay_hours,
        "renewable_match_pct": (
            baseline_dispatch.ai_renewable_match_pct
            if baseline_dispatch else None
        ),
        "carbon_free_match_pct": (
            baseline_dispatch.ai_carbon_free_match_pct
            if baseline_dispatch else None
        ),
        "grid_kwh": (
            baseline_dispatch.ai_grid_kwh if baseline_dispatch else None
        ),
        "battery_kwh": (
            baseline_dispatch.ai_battery_kwh if baseline_dispatch else None
        ),
        "optimisation_delta": {
            "additional_delay_hours": (
                clean_delta(result.total_delay_hours - baseline.total_delay_hours)
            ),
            "first_start_delay_hours": (
                (
                    min(item.option.start_time for item in result.assignments)
                    - min(item.option.start_time for item in baseline.assignments)
                ).total_seconds() / 3600
                if result.assignments and baseline.assignments else None
            ),
            "workflow_completion_delay_hours": (
                (
                    max(item.option.finish_time for item in result.assignments)
                    - max(item.option.finish_time for item in baseline.assignments)
                ).total_seconds() / 3600
                if result.assignments and baseline.assignments else None
            ),
            "energy_saved_kwh": (
                clean_delta(baseline.total_energy_kwh - result.total_energy_kwh)
            ),
            "cost_saved": clean_delta(baseline.total_cost - result.total_cost),
            "carbon_avoided_kg": (
                clean_delta(baseline.total_carbon_kg - result.total_carbon_kg)
            ),
            "renewable_match_uplift_points": (
                clean_delta(
                    optimised_dispatch.ai_renewable_match_pct
                    - baseline_dispatch.ai_renewable_match_pct
                )
                if optimised_dispatch and baseline_dispatch else None
            ),
            "carbon_free_match_uplift_points": (
                clean_delta(
                    optimised_dispatch.ai_carbon_free_match_pct
                    - baseline_dispatch.ai_carbon_free_match_pct
                )
                if optimised_dispatch and baseline_dispatch else None
            ),
            "grid_energy_avoided_kwh": (
                clean_delta(
                    baseline_dispatch.ai_grid_kwh
                    - optimised_dispatch.ai_grid_kwh
                )
                if optimised_dispatch and baseline_dispatch else None
            ),
        },
    }

    return {
        "api_version": API_VERSION,
        "product_version": PRODUCT_VERSION,
        "algorithm": (
            "exact-workflow-with-geospatial-energy-dispatch-v3"
            if energy_profile else "exact-capacity-portfolio-v1"
        ),
        "objective_order": (
            ["completed_utility_desc"] + energy_objectives[energy_priority] + [
                "facility_energy_asc", "curtailment_asc", "delay_asc",
            ]
            if energy_profile else [
                "completed_utility_desc",
                "operational_carbon_asc",
                "electricity_cost_asc",
                "delay_asc",
            ]
        ),
        "signal_mode": context.signal_mode,
        "market": {
            "key": context.market_key,
            "name": context.market_name,
            "location_key": context.location_key,
            "location_name": context.location_name,
            "currency": context.currency,
            "price_label": context.price_label,
            "carbon_label": context.carbon_label,
            "provenance": context.provenance,
        },
        "facility": {
            **facility_raw,
            "max_power_kw": max_power_kw,
        },
        "spatial_precision": _spatial_precision(
            context, energy_profile.site if energy_profile else None,
        ),
        "assignments": [
            serialise_assignment(assignment)
            for assignment in result.assignments
        ],
        "energy_dispatch": (
            _energy_dispatch_response(result.energy_dispatches[0])
            if result.energy_dispatches else None
        ),
        "earliest_run_counterfactual": counterfactual,
        "unscheduled_job_ids": result.unscheduled_job_ids,
        "completed_utility": result.completed_utility,
        "completed_work": result.completed_work,
        "total_energy_kwh": result.total_energy_kwh,
        "total_cost": result.total_cost,
        "total_carbon_kg": result.total_carbon_kg,
        "total_delay_hours": result.total_delay_hours,
        "exact": result.exact,
        "combinations_considered": result.combinations_considered,
        "search_space_upper_bound": result.search_space_upper_bound,
        "eligible_variant_counts": eligible_variant_counts,
        "quality_rejected": quality_rejected,
        "rejected": result.rejected,
    }


def score_response(decision: dict[str, Any], payload: dict[str, Any]
                   ) -> dict[str, Any]:
    """Score one persisted decision against caller-supplied realised points."""
    rows = payload.get("realised_points") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("realised_points must be a non-empty list")

    def point(row: dict[str, Any], *, stored: bool = False) -> GridDataPoint:
        if not isinstance(row, dict):
            raise ValueError("each realised point must be an object")
        try:
            timestamp = datetime.fromisoformat(str(row["timestamp"] if not stored else row["ts"]))
            price = row.get("price")
            carbon = row.get("carbon_intensity_g_per_kwh") if not stored else row.get("carbon")
            return GridDataPoint(
                timestamp=timestamp,
                price=None if price is None else float(price),
                carbon_intensity=None if carbon is None else float(carbon),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid realised point: {exc}") from exc

    realised = sorted((point(row) for row in rows), key=lambda item: item.timestamp)
    forecast = [point(row, stored=True) for row in decision.get("signals", [])]
    request_payload = decision.get("request", {})
    try:
        workload = WorkloadSpec(**request_payload["workload"])
        planning = PlanningRequest(**request_payload["planning"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"stored decision has invalid planning inputs: {exc}") from exc
    device_keys = request_payload.get("device_keys")
    location = GridLocation(
        decision["market"], decision["location"], forecast,
        decision.get("response", {}).get("market", {}).get("currency", ""),
        "FORECAST_SNAPSHOT",
    )
    snapshot = decision.get("response", {}).get("candidate_snapshot")
    if isinstance(snapshot, list) and snapshot:
        candidates = []
        for row in snapshot:
            if not isinstance(row, dict):
                raise ValueError("stored candidate snapshot is invalid")
            try:
                candidates.append(PlanningCandidate(
                    key=str(row["key"]),
                    hardware=str(row["hardware"]),
                    market=str(row["market"]),
                    location=str(row["location"]),
                    series=forecast,
                    runtime_hours=float(row["runtime_hours"]),
                    it_power_kw=float(row["it_power_kw"]),
                    pue=float(row["pue"]),
                    memory_ok=bool(row["memory_ok"]),
                    currency=str(row["currency"]),
                    hardware_provenance=str(row["hardware_provenance"]),
                    grid_provenance=str(row["grid_provenance"]),
                    notes=tuple(str(note) for note in row.get("notes", [])),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"stored candidate snapshot is invalid: {exc}") from exc
    else:
        candidates = planning_candidates(workload, [location], device_keys)
    result = backtest(
        [BacktestCandidate(candidate, realised) for candidate in candidates],
        planning,
        decided_at=datetime.fromisoformat(decision["created_at"]),
    )
    return {
        "decision_id": decision["id"],
        "scored_at_signal_end": realised[-1].timestamp.isoformat(),
        "realised_selected": _option(result.realised_selected),
        "realised_immediate": _option(result.realised_immediate),
        "realised_oracle": _option(result.realised_oracle),
        "cost_saved": result.cost_saved,
        "carbon_saved_kg": result.carbon_saved_kg,
        "cost_forecast_error": result.cost_forecast_error,
        "carbon_forecast_error_kg": result.carbon_forecast_error_kg,
        "cost_regret": result.cost_regret,
        "carbon_regret_kg": result.carbon_regret_kg,
    }
