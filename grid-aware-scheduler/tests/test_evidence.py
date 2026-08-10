from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.evidence import (QualityEvidence, WorkloadObservation,
                           build_evidence_profile,
                           planning_candidate_from_evidence)


def _quality(score: float = 0.92) -> QualityEvidence:
    return QualityEvidence(
        metric="task_accuracy",
        value=score,
        score=score,
        higher_is_better=True,
        suite="operator-eval",
        suite_version="1.0",
    )


def _observations(count: int = 3) -> list[WorkloadObservation]:
    start = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    return [
        WorkloadObservation(
            run_id=f"run-{index}",
            workload_class="text_generation",
            run_mode="inference",
            model_id="reference-language-model",
            model_version="1.0",
            precision="int4",
            device_key="apple-m2-8gb",
            compute_unit="gpu",
            stack_fingerprint="mlx-test-stack",
            shape_fingerprint="context-512_output-256_batch-1",
            work_amount=256,
            work_unit="tokens",
            duration_seconds=20 + index * 2,
            it_energy_wh=0.5 + index * 0.05,
            peak_memory_mb=3_100 + index * 20,
            thermal_start="nominal",
            thermal_end="fair",
            observed_at=start + timedelta(hours=index),
            quality=_quality(),
        )
        for index in range(count)
    ]


def _series() -> list[GridDataPoint]:
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    return [
        GridDataPoint(
            timestamp=start + timedelta(minutes=30 * index),
            price=50 + index,
            carbon_intensity=200 - index,
        )
        for index in range(4)
    ]


def test_quality_requires_measured_versioned_normalised_score():
    with pytest.raises(ValueError, match="between 0 and 1"):
        _quality(1.1)
    with pytest.raises(ValueError, match="must be MEASURED"):
        QualityEvidence(
            metric="accuracy", value=0.9, score=0.9,
            higher_is_better=True, suite="eval", suite_version="1",
            provenance="ESTIMATED",
        )


def test_observation_contract_is_metadata_only_and_modality_neutral():
    observation = _observations(1)[0]
    assert observation.metadata_only is True
    assert observation.work_rate_per_second == pytest.approx(12.8)
    assert observation.average_it_power_watts == pytest.approx(90)
    assert observation.energy_wh_per_work_unit == pytest.approx(0.5 / 256)
    assert not any(
        field in observation.__dataclass_fields__
        for field in ("prompt", "text", "image", "audio", "label", "content")
    )
    with pytest.raises(ValueError, match="metadata-only"):
        WorkloadObservation(**{**observation.__dict__, "metadata_only": False})


def test_profile_requires_repeated_exact_fingerprint():
    with pytest.raises(ValueError, match="at least 3"):
        build_evidence_profile(_observations(2))
    mixed = _observations()
    mixed[2] = WorkloadObservation(
        **{**mixed[2].__dict__, "compute_unit": "neural_engine"}
    )
    with pytest.raises(ValueError, match="exact evidence fingerprint"):
        build_evidence_profile(mixed)


def test_profile_uses_robust_measured_work_and_energy_rates():
    profile = build_evidence_profile(_observations())
    assert profile.sample_count == 3
    assert profile.work_rate_per_second == pytest.approx(256 / 22)
    assert profile.energy_wh_per_work_unit == pytest.approx(0.55 / 256)
    assert profile.average_it_power_watts == pytest.approx(90)
    assert profile.peak_memory_mb == 3_140
    assert profile.quality_score == pytest.approx(0.92)
    assert profile.provenance == "MEASURED"


def test_measured_profile_becomes_grid_aware_planning_candidate():
    profile = build_evidence_profile(_observations())
    candidate = planning_candidate_from_evidence(
        profile,
        work_amount=512,
        market="GB",
        location="london",
        series=_series(),
        currency="GBP",
        grid_provenance="FORECAST",
        pue=1.1,
    )
    assert candidate.runtime_hours == pytest.approx(44 / 3600)
    assert candidate.it_power_kw == pytest.approx(0.09)
    assert candidate.facility_energy_kwh == pytest.approx(0.00121)
    assert candidate.hardware_provenance == "MEASURED"
    assert any("512 tokens" in note for note in candidate.notes)
