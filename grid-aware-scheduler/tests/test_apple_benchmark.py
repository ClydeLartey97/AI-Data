from __future__ import annotations

import json
import sys

import pytest

from core.evidence_store import list_profiles, summary
from hardware.apple_benchmark import (BenchmarkSpec,
                                      parse_powermetrics_average_watts,
                                      run_command_benchmark)


def _spec() -> BenchmarkSpec:
    return BenchmarkSpec(
        workload_class="language_generation",
        run_mode="inference",
        model_id="public-reference-model",
        model_version="1.0",
        precision="int4",
        device_key="m2",
        compute_unit="gpu",
        shape_fingerprint="context-128_output-64_batch-1",
        work_unit="tokens",
        quality_metric="exact_match",
        quality_higher_is_better=True,
        evaluation_suite="public-reference-eval",
        evaluation_suite_version="1.0",
    )


def test_powermetrics_parser_combines_mean_subsystem_power():
    trace = """
    CPU Power: 1200 mW
    GPU Power: 2.5 W
    ANE Power: 300 mW
    CPU Power: 1800 mW
    GPU Power: 3.5 W
    ANE Power: 500 mW
    """
    assert parse_powermetrics_average_watts(trace) == pytest.approx(4.9)
    with pytest.raises(ValueError, match="no CPU, GPU or ANE"):
        parse_powermetrics_average_watts("Energy impact: 42")


def test_command_runner_records_only_metadata_with_external_meter(tmp_path):
    pytest.importorskip("psutil")
    result_path = tmp_path / "result.json"
    store_path = tmp_path / "evidence.sqlite"
    code = (
        "import json, os, time; "
        "assert os.environ['AI_ENERGY_MODEL_ID']=='public-reference-model'; "
        "assert os.environ['AI_ENERGY_EVALUATION_SUITE']=='public-reference-eval'; "
        "time.sleep(0.08); "
        "open(os.environ['AI_ENERGY_RESULT_PATH'],'w').write(" 
        "json.dumps({'work_amount':64,'quality_value':0.9,'quality_score':0.9}))"
    )
    outcome = run_command_benchmark(
        _spec(), [sys.executable, "-c", code],
        result_path=result_path,
        external_energy_wh=0.02,
        store_path=store_path,
    )
    assert outcome["profile_ready"] is False
    assert outcome["fingerprint_sample_count"] == 1
    assert not result_path.exists()
    assert summary(store_path)["observation_count"] == 1
    assert list_profiles(store_path) == []


def test_command_runner_accepts_post_run_external_meter_reading(tmp_path):
    pytest.importorskip("psutil")
    result_path = tmp_path / "result.json"
    code = (
        "import json, os; "
        "open(os.environ['AI_ENERGY_RESULT_PATH'],'w').write(" 
        "json.dumps({'work_amount':8,'quality_value':1,'quality_score':1}))"
    )
    readings = []

    def reading():
        readings.append("after-workload")
        assert not result_path.exists()
        return 0.01

    outcome = run_command_benchmark(
        _spec(), [sys.executable, "-c", code],
        result_path=result_path,
        external_energy_supplier=reading,
        store_path=tmp_path / "evidence.sqlite",
    )
    assert readings == ["after-workload"]
    assert outcome["fingerprint_sample_count"] == 1


def test_command_runner_requires_real_energy_and_result_contract(tmp_path):
    with pytest.raises(ValueError, match="exactly one energy method"):
        run_command_benchmark(
            _spec(), [sys.executable, "-c", "pass"],
            result_path=tmp_path / "result.json",
        )
    with pytest.raises(ValueError, match="work_amount"):
        result_path = tmp_path / "invalid.json"
        result_path.write_text(json.dumps({"prompt": "must not be accepted"}))
        # Contract parsing is tested through the public type to avoid running a
        # command whose invalid file would be deliberately removed first.
        from hardware.apple_benchmark import WorkloadResult
        WorkloadResult.from_path(result_path)
    extra_path = tmp_path / "extra.json"
    extra_path.write_text(json.dumps({
        "work_amount": 1, "quality_value": 1, "quality_score": 1,
        "response": "must not be accepted",
    }))
    with pytest.raises(ValueError, match="must contain only"):
        WorkloadResult.from_path(extra_path)
