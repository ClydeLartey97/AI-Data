from __future__ import annotations

import json
from pathlib import Path

from scripts import measure_this_mac as mac
from scripts import measure_this_pc as pc
from scripts import import_hardware_measurement as measurement_import


MAC_HEALTHY = {
    "approx_free_memory_gb": 20.0,
    "swap_used_mb": 0.0,
    "cpu_percent": 4.0,
    "power_source": "ac",
    "low_power_mode": False,
    "thermal_warning": False,
}

PC_HEALTHY = {
    "memory_available_gb": 32.0,
    "swap_used_gb": 0.0,
    "cpu_percent": 3.0,
    "gpu_percent": 1.0,
    "gpu_memory_used_mib": 400.0,
    "gpu_memory_total_mib": 24_576.0,
    "gpu_temperature_c": 38.0,
    "compute_process_count": 0,
}


def test_mac_preflight_accepts_a_quiet_powered_machine():
    result = mac.validate_conditions(MAC_HEALTHY, {"chip": "Apple M1 Ultra", "gpu_cores": 64})
    assert result["accepted"]
    assert result["blockers"] == []
    assert result["thresholds"]["maximum_relative_spread"] == 0.05


def test_mac_preflight_rejects_pressure_contention_and_power_caps():
    result = mac.validate_conditions({
        **MAC_HEALTHY,
        "approx_free_memory_gb": 1.0,
        "swap_used_mb": 2_048.0,
        "cpu_percent": 70.0,
        "low_power_mode": True,
        "thermal_warning": True,
    })
    assert not result["accepted"]
    assert len(result["blockers"]) == 5


def test_mac_postflight_rejects_unstable_repeats():
    preflight = mac.validate_conditions(MAC_HEALTHY)
    result = mac.validate_summary({
        "gemm_fp16_gflops_spread": 0.051,
        "gemm_fp32_gflops_spread": 0.01,
    }, preflight)
    assert not result["accepted"]
    assert result["stage"] == "postflight"
    assert any("5.1%" in reason for reason in result["blockers"])


def test_mac_preflight_refuses_unknown_gpu_core_count():
    result = mac.validate_conditions(MAC_HEALTHY, {"chip": "Apple M1 Ultra", "gpu_cores": None})
    assert not result["accepted"]
    assert any("core count" in reason for reason in result["blockers"])


def test_pc_preflight_accepts_an_idle_cool_gpu():
    result = pc.validate_conditions(PC_HEALTHY)
    assert result["accepted"]
    assert result["blockers"] == []


def test_pc_preflight_rejects_busy_hot_or_shared_gpu():
    result = pc.validate_conditions({
        **PC_HEALTHY,
        "gpu_percent": 60.0,
        "gpu_temperature_c": 85.0,
        "compute_process_count": 1,
    })
    assert not result["accepted"]
    assert len(result["blockers"]) == 3


def test_pc_power_unavailable_is_disclosed_without_losing_throughput():
    preflight = pc.validate_conditions(PC_HEALTHY)
    result = pc.validate_summary({"gemm_fp16_gflops_spread": 0.01}, preflight,
                                 {"available": False})
    assert result["accepted"]
    assert any("power was unavailable" in note for note in result["cautions"])
    assert result["eligible_metrics"] == {"throughput": True, "energy": False}


def test_pc_energy_requires_enough_matched_power_samples():
    preflight = pc.validate_conditions(PC_HEALTHY)
    result = pc.validate_summary({"gemm_fp16_gflops_spread": 0.01}, preflight,
                                 {"available": True, "samples": 8})
    assert result["eligible_metrics"] == {"throughput": True, "energy": True}


def test_output_can_be_a_directory_or_an_exact_json_path(tmp_path: Path):
    directory_destination = mac._destination(str(tmp_path), "apple-m1-ultra")
    assert directory_destination.parent == tmp_path
    assert directory_destination.name.startswith("measurement-apple-m1-ultra-")

    exact = tmp_path / "result.json"
    assert pc._destination(str(exact), "nvidia") == exact


def test_launchers_are_self_contained_and_preflight_before_gpu_download():
    root = Path(__file__).parents[1]
    mac_launcher = (root / "scripts" / "measure_mac.command").read_text()
    pc_launcher = (root / "scripts" / "measure_dell.ps1").read_text()

    assert "UV_UNMANAGED_INSTALL" in mac_launcher
    assert "--check-only" in mac_launcher
    assert mac_launcher.index("--check-only") < mac_launcher.index("mlx==0.32.2")
    assert "UV_UNMANAGED_INSTALL" in pc_launcher
    assert "--check-only" in pc_launcher
    assert pc_launcher.index("--check-only") < pc_launcher.index("--with torch")
    assert "--torch-backend auto" in pc_launcher
    assert "Get-CimInstance Win32_VideoController" in pc_launcher
    assert "windows-inventory-only" in pc_launcher


def _accepted_payload() -> dict:
    return {
        "schema": "ai-energy-hardware-measurement-v1",
        "measurement_id": "11111111-1111-4111-8111-111111111111",
        "status": "accepted",
        "device": "Apple M1 Ultra",
        "stack": "mlx-0.32.2_macos-15.0",
        "observed_at": "2026-09-05T12:00:00+00:00",
        "context": {"captured_at": "2026-09-05T12:00:00+00:00"},
        "validation": {
            "accepted": True,
            "eligible_metrics": {"throughput": True, "energy": False},
        },
        "measurements": [
            {"name": "gemm", "dtype": "float16", "rate": 10_000.0},
        ],
    }


def test_accepted_standalone_result_imports_into_baseline_history(tmp_path: Path):
    source = tmp_path / "accepted.json"
    source.write_text(json.dumps(_accepted_payload()))
    database = tmp_path / "baselines.sqlite"

    run_id, state = measurement_import.import_one(source, database)

    assert run_id == 1
    assert state["device"] == "Apple M1 Ultra"
    assert state["run_count"] == 1
    assert not state["established"]


def test_rejected_standalone_result_cannot_be_imported(tmp_path: Path):
    payload = _accepted_payload()
    payload["status"] = "rejected"
    payload["validation"]["accepted"] = False
    payload["validation"]["blockers"] = ["machine was busy"]
    source = tmp_path / "rejected.json"
    source.write_text(json.dumps(payload))

    import pytest
    with pytest.raises(ValueError, match="machine was busy"):
        measurement_import.import_one(source, tmp_path / "baselines.sqlite")


def test_same_measurement_file_cannot_count_as_three_runs(tmp_path: Path):
    source = tmp_path / "accepted.json"
    source.write_text(json.dumps(_accepted_payload()))
    database = tmp_path / "baselines.sqlite"
    measurement_import.import_one(source, database)

    import pytest
    with pytest.raises(ValueError, match="already imported"):
        measurement_import.import_one(source, database)
