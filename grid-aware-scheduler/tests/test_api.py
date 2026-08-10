from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from app.api import (market_response, plan_response, portfolio_response,
                     score_response)
from app.markets import MarketContext, market_locations


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
    assert response["product_version"] == "0.8.0"
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
