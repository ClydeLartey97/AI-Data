from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.planner import PlanningCandidate, PlanningRequest
from core.routing import (ESTIMATED, QualityCandidate, RoutingRequest,
                          optimise_route)


def _planning(key: str, power: float) -> PlanningCandidate:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    series = [
        GridDataPoint(start + timedelta(minutes=30 * i), 100, 50)
        for i in range(4)
    ]
    return PlanningCandidate(
        key, key, "GB", "London", series,
        runtime_hours=1.0, it_power_kw=power,
    )


def _quality(key: str, quality: float, power: float = 1.0, **kwargs
             ) -> QualityCandidate:
    return QualityCandidate(
        _planning(key, power), key, "quantitative_reasoning", quality,
        "operator-eval", "2026-08", **kwargs,
    )


def _request(floor: float = 0.8) -> RoutingRequest:
    return RoutingRequest(
        "quantitative_reasoning", floor,
        PlanningRequest(2, cost_weight=1, carbon_weight=0),
    )


def test_quality_floor_is_enforced_before_energy_optimisation():
    cheap_but_weak = _quality("weak", 0.7, power=0.1)
    qualifying = _quality("strong", 0.9, power=2.0)
    result = optimise_route([cheap_but_weak, qualifying], _request())
    assert result.selected.model_key == "strong"
    assert "below required" in result.rejected["weak"]


def test_estimated_quality_is_rejected_by_default():
    estimated = _quality("estimated", 0.95, quality_provenance=ESTIMATED)
    measured = _quality("measured", 0.85)
    result = optimise_route([estimated, measured], _request())
    assert result.selected.model_key == "measured"
    assert result.rejected["estimated"] == "quality evidence is not measured"


def test_estimated_quality_can_be_enabled_explicitly():
    estimated = _quality("estimated", 0.95, power=0.1,
                         quality_provenance=ESTIMATED)
    request = RoutingRequest(
        "quantitative_reasoning", 0.8,
        PlanningRequest(2, cost_weight=1, carbon_weight=0),
        require_measured_quality=False,
    )
    assert optimise_route([estimated], request).selected.model_key == "estimated"


def test_quality_scores_from_different_versions_are_not_compared():
    first = _quality("a", 0.9)
    second = QualityCandidate(
        _planning("b", 1), "b", "quantitative_reasoning", 0.9,
        "operator-eval", "2026-09",
    )
    with pytest.raises(ValueError, match="different evaluation suites"):
        optimise_route([first, second], _request())


def test_missing_workload_class_fails_closed():
    candidate = _quality("a", 0.9)
    request = RoutingRequest(
        "code_generation", 0.8,
        PlanningRequest(2, cost_weight=1, carbon_weight=0),
    )
    with pytest.raises(ValueError, match="no quality-eligible route"):
        optimise_route([candidate], request)
