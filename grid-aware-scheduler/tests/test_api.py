from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from app.api import (market_response, plan_response, portfolio_response,
                     score_response)
from app.markets import MarketContext, market_locations
from core.evidence import (EvidenceProfile, QualityEvidence, WorkloadObservation,
                           build_evidence_profile)


def _context() -> MarketContext:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return MarketContext(
        "GB", "Great Britain", "london", "London",
        [GridDataPoint(start + timedelta(minutes=30 * i), 100, price)
         for i, price in enumerate((50, 50, 10, 10))],
        "GBP", "£", "National price", "London carbon", "test provenance",
        "Historical replay", market_locations("GB"),
    )


def _payload() -> dict:
    return {
        "workload": {
            "model_key": "llama31-8b", "task": "training",
            "precision": "bf16", "tokens": 1e6, "accelerator_count": 8,
        },
        "planning": {
            "deadline_hours": 2, "cost_weight": 1, "carbon_weight": 0,
        },
        "device_keys": ["h100-sxm"],
    }


def _portfolio_payload() -> dict:
    def job(job_id: str, quality: float = 0.9) -> dict:
        return {
            "job_id": job_id,
            "earliest_delay_hours": 0,
            "deadline_hours": 2,
            "work_amount": 256,
            "work_unit": "tokens",
            "workload_class": "language_generation",
            "run_mode": "inference",
            "utility": 1,
            "minimum_quality": 0.8,
            "mandatory": True,
            "variants": [{
                "candidate_key": f"{job_id}-m2-gpu",
                "hardware": "Apple M2 GPU",
                "model_id": "reference-language-model",
                "model_version": "1.0",
                "precision": "int4",
                "compute_unit": "gpu",
                "memory_required_gb": 2,
                "memory_available_gb": 8,
                "runtime_hours": 0.5,
                "it_power_kw": 1,
                "pue": 1,
                "quality_score": quality,
                "quality_provenance": "MEASURED",
                "evaluation_suite": "operator-eval",
                "evaluation_version": "1.0",
                "hardware_provenance": "MEASURED",
            }],
        }
    return {
        "facility": {"max_power_kw": 1},
        "jobs": [job("job-a"), job("job-b")],
    }


def test_plan_api_returns_versioned_auditable_result():
    response = plan_response(_payload(), _context())
    assert response["api_version"] == "v1"
    assert response["product_version"] == "0.12.0"
    assert response["algorithm"] == "exact-enumeration-v1"
    assert response["selected"]["hardware"] == "8x H100 SXM"
    assert response["selected"]["start"].endswith("+00:00")
    assert response["selected"]["hardware_provenance"] == "ESTIMATED"
    assert response["market"]["provenance"] == "test provenance"
    assert response["candidate_snapshot"][0]["runtime_hours"] == pytest.approx(
        response["selected"]["runtime_hours"]
    )


def test_plan_api_rejects_unknown_fields_cleanly():
    payload = _payload()
    payload["workload"]["invented"] = True
    with pytest.raises(ValueError, match="invalid planning field"):
        plan_response(payload, _context())


def test_plan_api_rejects_empty_device_scope():
    payload = _payload()
    payload["device_keys"] = []
    with pytest.raises(ValueError, match="cannot be empty"):
        plan_response(payload, _context())


def test_plan_api_enforces_hard_operating_policy():
    payload = _payload()
    payload["planning"]["max_cost"] = 0.000001
    with pytest.raises(ValueError, match="violate policy limits"):
        plan_response(payload, _context())


def test_market_api_carries_units_and_provenance():
    response = market_response(_context())
    assert response["currency"] == "GBP"
    assert response["points"][0]["carbon_intensity_g_per_kwh"] == 100
    assert response["provenance"] == "test provenance"


def test_portfolio_api_schedules_queue_against_capacity_and_grid_signals():
    response = portfolio_response(_portfolio_payload(), _context())
    assert response["algorithm"] == "exact-capacity-portfolio-v1"
    assert response["exact"] is True
    assert response["completed_work"] == {"tokens": 512}
    assert response["total_cost"] == pytest.approx(0.01)
    assert response["total_carbon_kg"] == pytest.approx(0.1)
    assert all(row["score"] is None for row in response["assignments"])
    assert len(response["assignments"]) == 2
    assert response["assignments"][0]["workload_class"] == "language_generation"
    assert response["assignments"][0]["model_id"] == "reference-language-model"
    assert response["assignments"][0]["precision"] == "int4"
    assert response["assignments"][0]["memory_ok"] is True
    assert response["assignments"][0]["energy_wh_per_work_unit"] == pytest.approx(
        500 / 256
    )
    assert {row["start"] for row in response["assignments"]} == {
        "2026-08-01T01:00:00+00:00",
        "2026-08-01T01:30:00+00:00",
    }


def test_portfolio_api_rejects_variant_below_quality_floor():
    payload = _portfolio_payload()
    payload["jobs"][0]["variants"][0]["quality_score"] = 0.7
    with pytest.raises(ValueError, match="no quality-eligible variants"):
        portfolio_response(payload, _context())


def test_portfolio_api_rejects_boolean_measurements():
    payload = _portfolio_payload()
    payload["facility"]["max_power_kw"] = True
    with pytest.raises(ValueError, match="finite number"):
        portfolio_response(payload, _context())


def test_portfolio_api_rejects_execution_variant_that_does_not_fit_memory():
    payload = _portfolio_payload()
    payload["jobs"][0]["variants"][0]["memory_required_gb"] = 9
    with pytest.raises(ValueError, match="model does not fit"):
        portfolio_response(payload, _context())


def test_portfolio_api_preserves_workflow_stage_dependencies():
    payload = _portfolio_payload()
    payload["jobs"][0]["workflow_id"] = "language-evaluation"
    payload["jobs"][0]["stage_name"] = "Prepare batch"
    payload["jobs"][1]["workflow_id"] = "language-evaluation"
    payload["jobs"][1]["stage_name"] = "Run inference"
    payload["jobs"][1]["depends_on"] = ["job-a"]
    response = portfolio_response(payload, _context())
    by_job = {row["job_id"]: row for row in response["assignments"]}
    assert by_job["job-b"]["depends_on"] == ["job-a"]
    assert datetime.fromisoformat(by_job["job-b"]["start"]) >= datetime.fromisoformat(
        by_job["job-a"]["finish"]
    )


def test_portfolio_api_places_ai_work_in_multi_source_generation_window():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    job = payload["jobs"][0]
    job["deadline_hours"] = 2
    job["variants"][0]["runtime_hours"] = 1
    job["variants"][0]["it_power_kw"] = 50
    payload["facility"] = {
        "max_power_kw": 150,
        "base_load_kw": 60,
        "energy_sources": [
            {
                "source_id": "solar-plant",
                "name": "Dedicated solar plant",
                "kind": "solar",
                "availability_kw": [0, 0, 120, 120],
                "cost_per_mwh": 0,
                "carbon_g_per_kwh": 0,
                "confidence": 1,
                "renewable": True,
                "carbon_free": True,
                "delivery_type": "dedicated_wire",
                "dispatchable": False,
                "provenance": "FORECAST",
            },
            {
                "source_id": "firm-hydro",
                "name": "Firm hydro",
                "kind": "hydro",
                "availability_kw": [5, 5, 5, 5],
                "carbon_free": True,
                "provenance": "CONTRACTED",
            },
        ],
    }
    response = portfolio_response(payload, _context())
    assignment = response["assignments"][0]
    dispatch = response["energy_dispatch"]
    assert response["algorithm"] == (
        "exact-workflow-with-geospatial-energy-dispatch-v3"
    )
    assert assignment["start"] == "2026-08-01T01:00:00+00:00"
    assert assignment["renewable_match_pct"] == pytest.approx(100)
    assert assignment["grid_kwh"] == 0
    assert dispatch["ai_renewable_match_pct"] == pytest.approx(100)
    assert {source["kind"] for source in dispatch["sources"]} == {
        "solar", "hydro", "grid",
    }
    baseline = response["earliest_run_counterfactual"]
    assert baseline["assignments"][0]["start"] == "2026-08-01T00:00:00+00:00"
    assert baseline["optimisation_delta"]["grid_energy_avoided_kwh"] > 0
    assert baseline["optimisation_delta"]["renewable_match_uplift_points"] > 0


def test_portfolio_api_uses_battery_charged_from_earlier_surplus():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    job = payload["jobs"][0]
    job["earliest_delay_hours"] = 0.5
    job["variants"][0]["it_power_kw"] = 40
    payload["facility"] = {
        "max_power_kw": 120,
        "base_load_kw": 60,
        "energy_sources": [{
            "source_id": "solar",
            "name": "Onsite solar",
            "kind": "solar",
            "availability_kw": [120, 0, 0, 0],
            "renewable": True,
            "carbon_free": True,
            "provenance": "FORECAST",
        }],
        "battery": {
            "capacity_kwh": 50,
            "max_charge_kw": 100,
            "max_discharge_kw": 100,
            "round_trip_efficiency": 1,
        },
    }
    response = portfolio_response(payload, _context())
    assignment = response["assignments"][0]
    assert assignment["start"] == "2026-08-01T00:30:00+00:00"
    assert assignment["battery_kwh"] == pytest.approx(20)
    assert assignment["renewable_match_pct"] == pytest.approx(100)


def test_portfolio_api_rejects_generation_profile_with_wrong_horizon():
    payload = _portfolio_payload()
    payload["facility"]["energy_sources"] = [{
        "source_id": "wind",
        "name": "Wind farm",
        "kind": "wind",
        "availability_kw": [1, 2],
    }]
    with pytest.raises(ValueError, match="exactly 4 values"):
        portfolio_response(payload, _context())


def test_portfolio_api_preserves_exact_site_and_source_geometry():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["facility"] = {
        "max_power_kw": 20,
        "site": {
            "site_id": "lon-ai-1",
            "name": "London AI facility",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "grid_connection_id": "gsp-london-1",
            "time_zone": "Europe/London",
        },
        "energy_sources": [{
            "source_id": "solar-1",
            "name": "Dedicated solar",
            "kind": "solar",
            "availability_kw": [10, 10, 10, 10],
            "latitude": 51.9,
            "longitude": -0.1278,
            "grid_connection_id": "solar-export-1",
            "delivery_loss_fraction": 0.08,
            "renewable": True,
            "carbon_free": True,
        }],
    }
    response = portfolio_response(payload, _context())
    precision = response["spatial_precision"]
    dispatch = response["energy_dispatch"]
    source = next(row for row in dispatch["sources"] if row["source_id"] == "solar-1")
    assert precision["physical_site_scope"] == "exact_wgs84_coordinates"
    assert precision["price_signal_scope"] == "national"
    assert precision["carbon_signal_scope"] == "grid_region"
    assert precision["decision_interval_minutes"] == 30
    assert precision["provider_native_resolution_minutes"] == 30
    assert dispatch["site"]["grid_connection_id"] == "gsp-london-1"
    assert source["distance_to_site_km"] == pytest.approx(43.65, abs=0.2)
    assert source["delivery_loss_kwh"] > 0


def test_portfolio_api_rejects_partial_source_coordinates():
    payload = _portfolio_payload()
    payload["facility"]["energy_sources"] = [{
        "source_id": "wind",
        "name": "Wind",
        "kind": "wind",
        "availability_kw": [1, 1, 1, 1],
        "latitude": 52,
    }]
    with pytest.raises(ValueError, match="must be supplied together"):
        portfolio_response(payload, _context())


def test_portfolio_energy_dispatch_tolerates_non_schedulable_grid_gap():
    context = _context()
    context.series[0] = GridDataPoint(
        context.series[0].timestamp, 100, None,
    )
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["facility"] = {
        "max_power_kw": 10,
        "base_load_kw": 2,
        "energy_sources": [],
    }
    response = portfolio_response(payload, context)
    assert response["assignments"][0]["start"] != "2026-08-01T00:00:00+00:00"
    grid = next(
        source for source in response["energy_dispatch"]["sources"]
        if source["kind"] == "grid"
    )
    assert "base-load-only accounting placeholders" in grid["provenance"]


def test_portfolio_api_applies_operator_energy_priority():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["facility"] = {
        "max_power_kw": 10,
        "energy_priority": "renewable",
        "energy_sources": [{
            "source_id": "solar",
            "name": "Dedicated solar",
            "kind": "solar",
            "availability_kw": [0, 0, 10, 10],
            "cost_per_mwh": 100,
            "renewable": True,
            "carbon_free": True,
        }],
    }
    renewable = portfolio_response(payload, _context())
    assert renewable["assignments"][0]["start"] == "2026-08-01T01:00:00+00:00"
    assert renewable["objective_order"][1] == "renewable_energy_match_pct_desc"

    payload["facility"]["energy_priority"] = "cost"
    cheapest = portfolio_response(payload, _context())
    assert cheapest["assignments"][0]["start"] == "2026-08-01T00:00:00+00:00"
    assert cheapest["objective_order"][1] == "electricity_cost_asc"


def test_portfolio_api_rejects_unknown_energy_priority():
    payload = _portfolio_payload()
    payload["facility"]["energy_priority"] = "magic"
    with pytest.raises(ValueError, match="energy_priority"):
        portfolio_response(payload, _context())


def test_portfolio_api_splits_checkpointable_work_across_generation_windows():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    job = payload["jobs"][0]
    job["checkpointable"] = True
    job["checkpoint_count"] = 2
    job["variants"][0]["runtime_hours"] = 1
    payload["facility"] = {
        "max_power_kw": 10,
        "energy_sources": [{
            "source_id": "wind",
            "name": "Variable wind",
            "kind": "wind",
            "availability_kw": [10, 0, 0, 10],
            "renewable": True,
            "carbon_free": True,
        }],
    }
    response = portfolio_response(payload, _context())
    assert len(response["assignments"]) == 2
    assert {row["parent_job_id"] for row in response["assignments"]} == {"job-a"}
    assert {row["checkpoint_index"] for row in response["assignments"]} == {1, 2}
    assert response["completed_work"] == {"tokens": 256}
    assert response["energy_dispatch"]["ai_renewable_match_pct"] == pytest.approx(100)
    assert {row["start"] for row in response["assignments"]} == {
        "2026-08-01T00:00:00+00:00",
        "2026-08-01T01:30:00+00:00",
    }


def test_portfolio_api_rejects_unapproved_checkpoint_splitting():
    payload = _portfolio_payload()
    payload["jobs"][0]["checkpoint_count"] = 2
    with pytest.raises(ValueError, match="must be checkpointable"):
        portfolio_response(payload, _context())


def _measured_evidence_profile(
    device: str = "m2",
    *,
    model_id: str = "measured-language-model",
    suite: str = "operator-eval",
    duration_seconds: float = 8,
    external_meter: bool = False,
) -> EvidenceProfile:
    observed = datetime(2026, 8, 1, tzinfo=timezone.utc)
    quality = QualityEvidence(
        metric="exact_match", value=0.9, score=0.9,
        higher_is_better=True, suite=suite, suite_version="1.0",
    )
    return build_evidence_profile([
        WorkloadObservation(
            run_id=f"measured-{index}",
            workload_class="language_generation",
            run_mode="inference",
            model_id=model_id,
            model_version="1.0",
            precision="int4",
            device_key=device,
            compute_unit="gpu",
            stack_fingerprint="mlx-0.32.0",
            shape_fingerprint="context-128_output-64_batch-1",
            work_amount=64,
            work_unit="tokens",
            duration_seconds=duration_seconds + index,
            it_energy_wh=0.04 + index * 0.005,
            peak_memory_mb=900,
            thermal_start="nominal",
            thermal_end="nominal",
            observed_at=observed + timedelta(minutes=index),
            quality=quality,
            energy_method=("external_meter" if external_meter
                           else "apple_powermetrics"),
            energy_scope=("device_input" if external_meter
                          else "apple_soc_subsystems"),
            energy_provenance=("MEASURED" if external_meter
                               else "MEASURED_ESTIMATE"),
        )
        for index in range(3)
    ])


def test_portfolio_api_derives_variant_from_governed_evidence_profile():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    job = payload["jobs"][0]
    profile = _measured_evidence_profile()
    job["variants"] = [{
        "candidate_key": "measured-m2-variant",
        "evidence_profile_id": profile.profile_id,
        "pue": 1.1,
        "memory_available_gb": 8,
    }]
    response = portfolio_response(
        payload, _context(), {profile.profile_id: profile},
    )
    assignment = response["assignments"][0]
    assert assignment["model_id"] == "measured-language-model"
    assert assignment["hardware_provenance"] == "MEASURED"
    assert assignment["evidence_profile_id"] == profile.profile_id
    assert assignment["evidence_sample_count"] == 3
    assert assignment["energy_method"] == "apple_powermetrics"
    assert assignment["runtime_hours"] == pytest.approx(
        job["work_amount"] / profile.work_rate_per_second / 3600
    )
    assert assignment["it_power_kw"] == pytest.approx(
        profile.average_it_power_watts / 1000
    )


def test_portfolio_api_rejects_unknown_or_conflicting_evidence_profile():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["jobs"][0]["variants"][0]["evidence_profile_id"] = "missing"
    with pytest.raises(ValueError, match="unknown measured evidence"):
        portfolio_response(payload, _context(), {})

    profile = _measured_evidence_profile()
    payload["jobs"][0]["variants"][0]["evidence_profile_id"] = profile.profile_id
    with pytest.raises(ValueError, match="hardware conflicts"):
        portfolio_response(
            payload, _context(), {profile.profile_id: profile},
        )


def test_portfolio_api_automatically_compares_compatible_governed_profiles():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    job = payload["jobs"][0]
    job["auto_evidence_profiles"] = True
    job["variants"] = []
    first = _measured_evidence_profile(
        "m2-8gb", model_id="reference-model-a", duration_seconds=7,
        external_meter=True,
    )
    second = _measured_evidence_profile(
        "m3-16gb", model_id="reference-model-b", duration_seconds=5,
        external_meter=True,
    )
    profiles = {first.profile_id: first, second.profile_id: second}

    response = portfolio_response(payload, _context(), profiles)

    assert response["eligible_variant_counts"] == {job["job_id"]: 2}
    assignment = response["assignments"][0]
    assert assignment["evidence_profile_id"] in profiles
    assert assignment["quality_provenance"] == "MEASURED"
    assert assignment["energy_method"] == "external_meter"


def test_portfolio_api_rejects_incomparable_quality_suites():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["jobs"][0]["auto_evidence_profiles"] = True
    payload["jobs"][0]["variants"] = []
    first = _measured_evidence_profile(model_id="model-a", suite="eval-a")
    second = _measured_evidence_profile(model_id="model-b", suite="eval-b")
    with pytest.raises(ValueError, match="different evaluation suite versions"):
        portfolio_response(
            payload, _context(),
            {first.profile_id: first, second.profile_id: second},
        )


def test_portfolio_api_rejects_mixed_energy_measurement_scopes():
    payload = _portfolio_payload()
    payload["jobs"] = payload["jobs"][:1]
    payload["jobs"][0]["auto_evidence_profiles"] = True
    payload["jobs"][0]["variants"] = []
    subsystem = _measured_evidence_profile(model_id="model-subsystem")
    device_input = _measured_evidence_profile(
        model_id="model-device-input", external_meter=True,
    )
    with pytest.raises(ValueError, match="measurement scope"):
        portfolio_response(
            payload, _context(),
            {subsystem.profile_id: subsystem,
             device_input.profile_id: device_input},
        )


def test_score_api_replays_persisted_decision_on_realised_points():
    context = _context()
    request = _payload()
    response = plan_response(request, context)
    decision = {
        "id": "decision-1", "created_at": "2026-07-31T23:00:00+00:00",
        "market": "GB", "location": "London", "request": request,
        "response": response,
        "signals": [
            {"ts": point.timestamp.isoformat(), "price": point.price,
             "carbon": point.carbon_intensity}
            for point in context.series
        ],
    }
    realised = [{
        "timestamp": point.timestamp.isoformat(),
        "price": 100 - point.price,
        "carbon_intensity_g_per_kwh": point.carbon_intensity,
    } for point in context.series]
    score = score_response(decision, {"realised_points": realised})
    assert score["decision_id"] == "decision-1"
    assert score["realised_selected"]["start"] == response["selected"]["start"]
    assert score["cost_regret"] >= 0


def test_score_api_requires_realised_points():
    with pytest.raises(ValueError, match="realised_points"):
        score_response({}, {})
