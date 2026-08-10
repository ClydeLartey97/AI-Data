"""Versioned JSON contract for local planning integrations."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import math
from typing import Any

from app.markets import MarketContext
from adapters.base_adapter import GridDataPoint
from core.backtest import BacktestCandidate, backtest
from core.estimator import (GridLocation, WorkloadSpec, plan_workload,
                            planning_candidates)
from core.planner import PlanOption, PlanningCandidate, PlanningRequest
from core.portfolio import (PortfolioJob, PortfolioPolicy, SiteCapacity,
                            optimise_portfolio)
from hardware.calibration import CalibrationProfile

API_VERSION = "v1"
PRODUCT_VERSION = "0.8.0"
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


def portfolio_response(payload: dict[str, Any], context: MarketContext
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
    if not isinstance(facility_raw, dict):
        raise ValueError("facility must be an object")
    if not context.series:
        raise ValueError("selected market/location has no grid signal")

    max_power_kw = _number(facility_raw, "max_power_kw", minimum=0.000001)
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
    jobs: list[PortfolioJob] = []
    quality_rejected: dict[str, list[str]] = {}
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
        require_measured = job_raw.get("require_measured_quality", True)
        if not isinstance(require_measured, bool):
            raise ValueError("require_measured_quality must be boolean")
        variants_raw = job_raw.get("variants")
        if not isinstance(variants_raw, list) or not variants_raw:
            raise ValueError(f"job {job_id!r} needs a non-empty variants list")
        if len(variants_raw) > 100:
            raise ValueError(f"job {job_id!r} cannot exceed 100 variants")

        candidates: list[PlanningCandidate] = []
        seen_variants: set[str] = set()
        for variant_index, variant_raw in enumerate(variants_raw):
            if not isinstance(variant_raw, dict):
                raise ValueError(
                    f"job {job_id!r} variant {variant_index} must be an object"
                )
            candidate_key = variant_raw.get("candidate_key")
            hardware = variant_raw.get("hardware")
            if not isinstance(candidate_key, str) or not candidate_key.strip():
                raise ValueError(f"job {job_id!r} variant needs candidate_key")
            if candidate_key in seen_variants:
                raise ValueError(f"job {job_id!r} variant keys must be unique")
            seen_variants.add(candidate_key)
            if not isinstance(hardware, str) or not hardware.strip():
                raise ValueError(f"job {job_id!r} variant needs hardware")
            model_id = variant_raw.get("model_id", candidate_key)
            model_version = variant_raw.get("model_version", "unspecified")
            precision = variant_raw.get("precision", "unspecified")
            compute_unit = variant_raw.get("compute_unit", "unspecified")
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
                variant_raw, "quality_score", minimum=0, maximum=1,
            )
            quality_provenance = variant_raw.get("quality_provenance", "MEASURED")
            if quality_provenance not in {"MEASURED", "ESTIMATED"}:
                raise ValueError(
                    "quality_provenance must be MEASURED or ESTIMATED"
                )
            evaluation_suite = variant_raw.get("evaluation_suite")
            evaluation_version = variant_raw.get("evaluation_version")
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
            hardware_provenance = variant_raw.get(
                "hardware_provenance", "ESTIMATED"
            )
            if hardware_provenance not in {"MEASURED", "ESTIMATED", "SPEC"}:
                raise ValueError(
                    "hardware_provenance must be MEASURED, ESTIMATED or SPEC"
                )
            runtime_hours = _number(
                variant_raw, "runtime_hours", minimum=0.000001,
            )
            it_power_kw = _number(
                variant_raw, "it_power_kw", minimum=0,
            )
            pue = _number(
                variant_raw, "pue", default=1.2, minimum=1, maximum=5,
            )
            memory_required = variant_raw.get("memory_required_gb")
            memory_available = variant_raw.get("memory_available_gb")
            if (memory_required is None) != (memory_available is None):
                raise ValueError(
                    "memory_required_gb and memory_available_gb must be supplied together"
                )
            if memory_required is not None:
                memory_required = _number(
                    variant_raw, "memory_required_gb", minimum=0,
                )
                memory_available = _number(
                    variant_raw, "memory_available_gb", minimum=0,
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
            }
        if not candidates:
            detail = "; ".join(quality_rejected.get(job_id, []))
            raise ValueError(
                f"job {job_id!r} has no quality-eligible variants"
                + (f": {detail}" if detail else "")
            )
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
            }
            jobs.append(PortfolioJob(
                job_id=job_id,
                candidates=tuple(candidates),
                earliest_start=origin + timedelta(hours=earliest_delay),
                deadline=origin + timedelta(hours=deadline_hours),
                work_amount=_number(job_raw, "work_amount", minimum=0.000001),
                work_unit=str(job_raw.get("work_unit", "")),
                utility=_number(job_raw, "utility", minimum=0.000001),
                mandatory=mandatory,
                depends_on=depends_on,
            ))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid job {job_id!r}: {exc}") from exc

    result = optimise_portfolio(
        jobs,
        PortfolioPolicy(
            capacities=(SiteCapacity(
                context.market_key, context.location_name, max_power_kw,
            ),),
            max_search_combinations=search_limit,
            **policy_values,
        ),
    )
    return {
        "api_version": API_VERSION,
        "product_version": PRODUCT_VERSION,
        "algorithm": "exact-capacity-portfolio-v1",
        "objective_order": [
            "completed_utility_desc",
            "operational_carbon_asc",
            "electricity_cost_asc",
            "delay_asc",
        ],
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
        "assignments": [
            {
                "job_id": assignment.job.job_id,
                **job_metadata[assignment.job.job_id],
                **variant_metadata[(
                    assignment.job.job_id,
                    assignment.option.candidate.key,
                )],
                "work_amount": assignment.job.work_amount,
                "work_unit": assignment.job.work_unit,
                "utility": assignment.job.utility,
                "energy_wh_per_work_unit": (
                    assignment.option.facility_energy_kwh * 1000
                    / assignment.job.work_amount
                ),
                "carbon_g_per_work_unit": (
                    assignment.option.carbon_kg * 1000
                    / assignment.job.work_amount
                ),
                "cost_per_work_unit": (
                    assignment.option.cost / assignment.job.work_amount
                ),
                **_option(assignment.option),
            }
            for assignment in result.assignments
        ],
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
