from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.estimator import WorkloadSpec, estimate_device
from hardware import catalog
from hardware.calibration import (CalibrationObservation, build_profile,
                                  load_profiles, profiles_from_payload,
                                  serialise_profiles)


def _observations(count: int = 3):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [
        CalibrationObservation(
            device_key="h100-sxm", model_key="llama31-8b",
            task="training", precision="bf16", accelerator_count=8,
            stack_fingerprint="torch-2.8_cuda-13_kernel-a1",
            tokens=1_000_000, duration_seconds=100 + i * 5,
            average_it_power_watts=4_000 + i * 100,
            observed_at=start + timedelta(hours=i),
        )
        for i in range(count)
    ]


def test_calibration_requires_repeated_exact_fingerprint():
    with pytest.raises(ValueError, match="at least 3"):
        build_profile(_observations(2))
    mixed = _observations()
    mixed[2] = CalibrationObservation(
        **{**mixed[2].__dict__, "precision": "fp8"}
    )
    with pytest.raises(ValueError, match="exact fingerprint"):
        build_profile(mixed)


def test_exact_calibration_promotes_only_runtime_and_power_evidence():
    profile = build_profile(_observations())
    spec = WorkloadSpec(
        "llama31-8b", "training", "bf16", 2_000_000, 8,
        calibration_stack="torch-2.8_cuda-13_kernel-a1",
    )
    estimate = estimate_device(spec, catalog.CATALOG["h100-sxm"], profile)
    assert estimate.provenance == "MEASURED"
    assert estimate.runtime_hours == pytest.approx(210 / 3600)
    assert estimate.it_power_kw == pytest.approx(4.1)
    assert any("3 exact-fingerprint runs" in note for note in estimate.assumptions)


def test_calibration_does_not_leak_across_software_stacks():
    profile = build_profile(_observations())
    spec = WorkloadSpec(
        "llama31-8b", "training", "bf16", 2_000_000, 8,
        calibration_stack="different-stack",
    )
    estimate = estimate_device(spec, catalog.CATALOG["h100-sxm"], profile)
    assert estimate.provenance == "ESTIMATED"


def test_calibration_profile_round_trip(tmp_path):
    profile = build_profile(_observations())
    path = tmp_path / "profiles.json"
    import json
    path.write_text(json.dumps(serialise_profiles([profile])), encoding="utf-8")
    loaded = load_profiles(path)
    assert loaded == [profile]


def test_observation_payload_rejects_under_sampled_group():
    rows = []
    for observation in _observations(2):
        row = dict(observation.__dict__)
        row["observed_at"] = observation.observed_at.isoformat()
        rows.append(row)
    with pytest.raises(ValueError, match="insufficient repeated observations"):
        profiles_from_payload({"observations": rows})
