from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.evidence import QualityEvidence, WorkloadObservation
from core.evidence_store import (get_profile, ingest_observation, list_profiles,
                                 summary)


def _observation(index: int, *, thermal: str = "nominal") -> WorkloadObservation:
    return WorkloadObservation(
        run_id=f"run-{index}",
        workload_class="language_generation",
        run_mode="inference",
        model_id="public-reference-model",
        model_version="1.0",
        precision="int4",
        device_key="m2",
        compute_unit="gpu",
        stack_fingerprint="mlx-0.32.0_macos-26.1",
        shape_fingerprint="context-128_output-64_batch-1",
        work_amount=64,
        work_unit="tokens",
        duration_seconds=12 + index,
        it_energy_wh=0.08 + index * 0.002,
        peak_memory_mb=900 + index,
        thermal_start="nominal",
        thermal_end=thermal,
        observed_at=datetime(2026, 8, 10, tzinfo=timezone.utc)
        + timedelta(minutes=index),
        quality=QualityEvidence(
            metric="exact_match", value=0.75, score=0.75,
            higher_is_better=True, suite="public-reference-eval",
            suite_version="1.0",
        ),
        energy_method="apple_powermetrics",
        energy_scope="apple_soc_subsystems",
        energy_provenance="MEASURED_ESTIMATE",
    )


def test_store_builds_one_stable_profile_after_three_exact_runs(tmp_path):
    path = tmp_path / "evidence.sqlite"
    first = ingest_observation(_observation(0), path)
    second = ingest_observation(_observation(1), path)
    third = ingest_observation(_observation(2), path)
    assert first["profile_ready"] is False
    assert second["fingerprint_sample_count"] == 2
    assert third["profile_ready"] is True
    profile_id = third["profile"]["profile_id"]
    assert get_profile(profile_id, path).sample_count == 3
    assert list_profiles(path)[0]["cross_device_comparable"] is False
    assert summary(path) == {
        "observation_count": 3,
        "profile_count": 1,
        "pending_fingerprint_count": 0,
    }


def test_store_is_idempotent_but_rejects_run_id_rewrites(tmp_path):
    path = tmp_path / "evidence.sqlite"
    observation = _observation(0)
    ingest_observation(observation, path)
    duplicate = ingest_observation(observation, path)
    assert duplicate["duplicate"] is True
    with pytest.raises(ValueError, match="different evidence"):
        ingest_observation(WorkloadObservation(**{
            **observation.__dict__, "duration_seconds": 99,
        }), path)


def test_store_rejects_thermally_compromised_runs(tmp_path):
    with pytest.raises(ValueError, match="thermal runs"):
        ingest_observation(_observation(0, thermal="serious"),
                           tmp_path / "evidence.sqlite")
