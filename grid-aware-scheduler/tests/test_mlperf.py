from __future__ import annotations

import json

import pytest

from hardware import mlperf
from hardware.base import Provenance


def _row(**overrides):
    row = {
        "Submitter": "NVIDIA", "System": "DGX H200", "Model": "llama2-70b-99",
        "Scenario": "Offline", "Accelerator": "NVIDIA H200-SXM-141GB (x8)",
        "Total Accelerators": 8, "Performance_Result": 34400.0,
        "Performance_Units": "Tokens/s", "Accuracy": "ROUGE1: 44.5",
        "errors": 0, "version": "v5.0",
    }
    row.update(overrides)
    return row


def test_per_accelerator_rate_divides_by_the_stated_count():
    results = mlperf.parse([_row()])
    assert len(results) == 1
    assert results[0].per_accelerator == 4300.0
    # The "(x8)" suffix is presentation, not part of the device name.
    assert results[0].accelerator == "NVIDIA H200-SXM-141GB"


def test_rows_without_an_accelerator_count_are_dropped_not_guessed():
    assert mlperf.parse([_row(**{"Total Accelerators": None})]) == []
    # Nodes x per-node is acceptable because both are stated.
    recovered = mlperf.parse([_row(**{"Total Accelerators": None,
                                      "a#": 4, "Nodes": 2})])
    assert recovered[0].accelerator_count == 8


def test_non_throughput_units_and_errored_rows_are_ignored():
    assert mlperf.parse([_row(Performance_Units="Latency (ms)")]) == []
    assert mlperf.parse([_row(errors=3)]) == []
    assert mlperf.parse([_row(Performance_Result=0)]) == []
    assert mlperf.parse([_row(Accelerator="")]) == []


def test_scenarios_are_never_grouped_together():
    """Offline batches freely; Server holds a latency target. Mixing them
    would invent a throughput neither submission achieved."""
    profiles = mlperf.build_profiles(mlperf.parse([
        _row(Scenario="Offline", Performance_Result=34400.0),
        _row(Scenario="Server", Performance_Result=32000.0),
    ]))
    assert len(profiles) == 2
    assert {p.scenario for p in profiles} == {"Offline", "Server"}


def test_profiles_carry_published_provenance_not_measured():
    profile = mlperf.build_profiles(mlperf.parse([_row()]))[0]
    assert profile.provenance == Provenance.PUBLISHED.value
    assert profile.provenance != Provenance.MEASURED.value


def test_power_is_per_accelerator_when_reported():
    results = mlperf.parse([_row(Power_Result=4000.0, Power_Units="Watts")])
    assert results[0].watts_per_accelerator == 500.0
    assert mlperf.parse([_row()])[0].watts_per_accelerator is None


def test_scaling_curve_is_relative_to_the_smallest_configuration():
    rows = [
        _row(**{"Total Accelerators": 8, "Performance_Result": 8000.0}),
        _row(**{"Total Accelerators": 64, "Performance_Result": 51200.0}),
    ]
    profile = mlperf.build_profiles(mlperf.parse(rows))[0]
    scaling = profile.scaling_efficiency()
    assert scaling["baseline_accelerators"] == 8
    assert scaling["largest_accelerators"] == 64
    # 800 per accelerator at 64 against 1000 at 8 is 80% efficiency.
    assert scaling["efficiency_at_largest"] == pytest.approx(0.8)


def test_a_single_configuration_has_no_scaling_curve():
    profile = mlperf.build_profiles(mlperf.parse([_row()]))[0]
    assert profile.scaling_efficiency() is None


def test_download_uses_the_cache_and_needs_no_network(tmp_path):
    cached = tmp_path / "summary_v5.0.json"
    cached.write_text(json.dumps([_row()]), encoding="utf-8")

    def explode(*args, **kwargs):
        raise AssertionError("network must not be touched when cached")

    rows = mlperf.download("v5.0", cache_dir=tmp_path, opener=explode)
    assert len(rows) == 1


def test_unreachable_round_is_reported_not_silently_empty(tmp_path):
    def refuse(*args, **kwargs):
        raise OSError("no network")

    with pytest.raises(RuntimeError, match="could not fetch"):
        mlperf.download("v9.9", cache_dir=tmp_path, opener=refuse)
