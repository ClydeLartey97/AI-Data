from __future__ import annotations

import pytest

from hardware import baseline_store


def _report(gflops=2500.0, bandwidth=80.0, validated=True, stack="mlx-0.32",
            observed="2026-08-11T10:00:00+00:00"):
    return {
        "device": "Apple M2",
        "stack": stack,
        "observed_at": observed,
        "context": {"captured_at": observed} if validated else {},
        "measurements": [
            {"name": "gemm", "dtype": "float16", "size": 2048, "rate": gflops},
            {"name": "gemm", "dtype": "float32", "size": 2048, "rate": gflops * 0.9},
            {"name": "memory_bandwidth", "dtype": "float32", "size": 1,
             "rate": bandwidth},
        ],
    }


def test_one_run_is_a_reading_not_a_baseline(tmp_path):
    path = tmp_path / "b.sqlite"
    baseline_store.record_run(_report(), path)
    state = baseline_store.baseline("Apple M2", path=path)
    assert state["established"] is False
    assert state["run_count"] == 1
    assert "3 separate runs" in state["reason"]


def test_three_validated_runs_establish_a_ceiling(tmp_path):
    path = tmp_path / "b.sqlite"
    for index, rate in enumerate((2400.0, 2500.0, 2600.0)):
        baseline_store.record_run(
            _report(gflops=rate, observed=f"2026-08-1{index+1}T10:00:00+00:00"), path)

    state = baseline_store.baseline("Apple M2", path=path)
    assert state["established"] is True
    assert state["scope"] == "ceiling"
    fp16 = state["metrics"]["gemm_fp16_gflops"]
    assert fp16["median"] == 2500.0
    assert fp16["samples"] == 3
    assert fp16["spread_percent"] == pytest.approx(8.0, abs=0.1)
    # The ceiling must never be mistaken for a workload rate.
    assert "not a workload throughput" in state["note"].lower()


def test_unvalidated_diagnostic_runs_never_count_towards_a_baseline(tmp_path):
    path = tmp_path / "b.sqlite"
    for _ in range(4):
        baseline_store.record_run(_report(validated=False), path)

    assert baseline_store.summary(path)["run_count"] == 4
    assert baseline_store.summary(path)["validated_run_count"] == 0
    state = baseline_store.baseline("Apple M2", path=path)
    assert state["established"] is False
    assert state["run_count"] == 0


def test_runs_from_different_software_stacks_are_not_comparable(tmp_path):
    path = tmp_path / "b.sqlite"
    baseline_store.record_run(_report(stack="mlx-0.32"), path)
    baseline_store.record_run(_report(stack="mlx-0.32"), path)
    baseline_store.record_run(_report(stack="mlx-0.40"), path)

    state = baseline_store.baseline("Apple M2", path=path)
    assert state["established"] is False
    assert "different software stacks" in state["reason"]


def test_history_is_append_only_and_newest_first(tmp_path):
    path = tmp_path / "b.sqlite"
    for index in range(3):
        baseline_store.record_run(
            _report(observed=f"2026-08-1{index+1}T10:00:00+00:00"), path)
    runs = baseline_store.history("Apple M2", path=path)
    assert [run["run_id"] for run in runs] == [3, 2, 1]
    public = [name for name in dir(baseline_store) if not name.startswith("_")]
    assert not any("delete" in name or "update" in name for name in public)


def test_malformed_reports_are_rejected(tmp_path):
    path = tmp_path / "b.sqlite"
    with pytest.raises(ValueError, match="device"):
        baseline_store.record_run(dict(_report(), device=""), path)
    with pytest.raises(ValueError, match="measurement"):
        baseline_store.record_run(dict(_report(), measurements=[]), path)
    with pytest.raises(ValueError, match="timestamp"):
        baseline_store.record_run(dict(_report(), observed_at="sometime"), path)
    assert baseline_store.summary(path)["run_count"] == 0
