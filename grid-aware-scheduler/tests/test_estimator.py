from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.estimator import (GridLocation, WorkloadSpec, estimate_device,
                            memory_required_gb, plan_workload)
from core.planner import PlanningRequest
from hardware import catalogue


def _grid(prices=(50, 50, 10, 10)) -> GridLocation:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    series = [
        GridDataPoint(start + timedelta(minutes=30 * i), 100, price)
        for i, price in enumerate(prices)
    ]
    return GridLocation("GB", "London", series, "GBP", "MEASURED")


def test_training_memory_uses_explicit_state_bytes_and_headroom():
    spec = WorkloadSpec(
        "llama31-8b", "training", "bf16", 1e6, 8,
        training_state_bytes_per_param=16,
        activation_buffer_headroom=0.2,
    )
    assert memory_required_gb(spec) == pytest.approx(153.6)
    sharded = estimate_device(spec, catalogue.CATALOGUE["h100-sxm"])
    assert sharded.memory_ok is True
    replicated_spec = WorkloadSpec(
        "llama31-8b", "training", "bf16", 1e6, 8,
        memory_mode="replicated",
        training_state_bytes_per_param=16,
        activation_buffer_headroom=0.2,
    )
    assert estimate_device(replicated_spec, catalogue.CATALOGUE["h100-sxm"]).memory_ok is False


def test_kv_cache_precision_is_independent_of_weight_precision():
    bf16 = WorkloadSpec(
        "llama31-8b", "inference", "int4", 1e6, 1,
        context_length=32768, batch_size=32, kv_precision="bf16",
    )
    fp8 = WorkloadSpec(
        "llama31-8b", "inference", "int4", 1e6, 1,
        context_length=32768, batch_size=32, kv_precision="fp8",
    )
    weight_memory = bf16.model.weight_gb("int4")
    assert memory_required_gb(bf16) - weight_memory == pytest.approx(
        2 * (memory_required_gb(fp8) - weight_memory)
    )


def test_server_side_estimator_builds_and_plans_real_candidates():
    spec = WorkloadSpec(
        "llama31-8b", "training", "bf16", 1e6, 8,
        pue=1.3, system_efficiency=0.85,
    )
    result = plan_workload(
        spec, [_grid()], PlanningRequest(2, cost_weight=1, carbon_weight=0),
        device_keys=["h100-sxm"],
    )
    assert result.selected.start_index == 2
    assert result.selected.candidate.pue == pytest.approx(1.3)
    assert result.selected.candidate.hardware == "8x H100 SXM"


def test_estimator_rejects_invalid_efficiency():
    with pytest.raises(ValueError, match="system_efficiency"):
        WorkloadSpec("llama31-8b", "training", "bf16", 1e6, 8,
                     system_efficiency=1.2)


@pytest.mark.parametrize("field,value,message", [
    ("accelerator_count", True, "accelerator_count"),
    ("context_length", 1.5, "context length"),
    ("tokens", float("inf"), "tokens"),
    ("pue", 5.1, "PUE"),
])
def test_estimator_rejects_non_finite_or_unsafe_dimensions(field, value, message):
    values = {
        "model_key": "llama31-8b", "task": "training",
        "precision": "bf16", "tokens": 1e6, "accelerator_count": 8,
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        WorkloadSpec(**values)
