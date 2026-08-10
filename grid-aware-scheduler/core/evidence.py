"""Privacy-preserving measured evidence for heterogeneous AI workloads.

The existing hardware calibration path is deliberately narrow and token based.
This module defines the broader evidence contract needed by language, vision,
speech and training workloads.  It stores measurements and fingerprints, never
the prompt, image, audio, label or other customer content used by a run.
"""
from __future__ import annotations

import math
import statistics
import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone

from adapters.base_adapter import GridDataPoint
from core.planner import PlanningCandidate

SCHEMA_VERSION = "workload-evidence-v1"
MIN_PROFILE_SAMPLES = 3
WORK_UNITS = frozenset({
    "tokens",
    "images",
    "audio_seconds",
    "samples",
    "training_examples",
    "optimizer_steps",
})
RUN_MODES = frozenset({"inference", "evaluation", "fine_tuning", "training"})
THERMAL_STATES = frozenset({"nominal", "fair", "serious", "critical", "unknown"})
ENERGY_METHODS = frozenset({
    "external_meter", "apple_powermetrics", "nvidia_smi_integrated",
})
ENERGY_SCOPES = frozenset({
    "device_input", "apple_soc_subsystems", "gpu_board",
})
ENERGY_PROVENANCE = frozenset({"MEASURED", "MEASURED_ESTIMATE"})


def _required_text(name: str, value: str, maximum: int = 200) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if len(value) > maximum:
        raise ValueError(f"{name} cannot exceed {maximum} characters")


def _positive(name: str, value: float, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or (value < 0 if allow_zero else value <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")


@dataclass(frozen=True)
class QualityEvidence:
    """One versioned task-quality result.

    ``value`` retains the native metric, such as word error rate.  ``score`` is
    a suite-defined, normalised 0-to-1 value used only for comparable routing.
    The evidence producer, not this application, owns that transformation.
    """

    metric: str
    value: float
    score: float
    higher_is_better: bool
    suite: str
    suite_version: str
    provenance: str = "MEASURED"

    def __post_init__(self) -> None:
        for name, value in (
            ("quality metric", self.metric),
            ("evaluation suite", self.suite),
            ("evaluation suite version", self.suite_version),
        ):
            _required_text(name, value)
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("quality value must be a number")
        if not math.isfinite(self.value):
            raise ValueError("quality value must be finite")
        if (isinstance(self.score, bool) or not isinstance(self.score, (int, float))
                or not math.isfinite(self.score) or not 0 <= self.score <= 1):
            raise ValueError("quality score must be finite and between 0 and 1")
        if not isinstance(self.higher_is_better, bool):
            raise ValueError("higher_is_better must be boolean")
        if self.provenance != "MEASURED":
            raise ValueError("workload evidence quality must be MEASURED")

    @property
    def fingerprint(self) -> tuple[str, str, bool]:
        return self.suite, self.suite_version, self.higher_is_better


@dataclass(frozen=True)
class WorkloadObservation:
    """One content-free energy and performance observation."""

    run_id: str
    workload_class: str
    run_mode: str
    model_id: str
    model_version: str
    precision: str
    device_key: str
    compute_unit: str
    stack_fingerprint: str
    shape_fingerprint: str
    work_amount: float
    work_unit: str
    duration_seconds: float
    it_energy_wh: float
    peak_memory_mb: float
    thermal_start: str
    thermal_end: str
    observed_at: datetime
    quality: QualityEvidence
    energy_method: str
    energy_scope: str
    energy_provenance: str
    schema_version: str = SCHEMA_VERSION
    metadata_only: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("workload_class", self.workload_class),
            ("model_id", self.model_id),
            ("model_version", self.model_version),
            ("precision", self.precision),
            ("device_key", self.device_key),
            ("compute_unit", self.compute_unit),
            ("stack_fingerprint", self.stack_fingerprint),
            ("shape_fingerprint", self.shape_fingerprint),
        ):
            _required_text(name, value)
        if self.run_mode not in RUN_MODES:
            raise ValueError(f"run_mode must be one of {sorted(RUN_MODES)}")
        if not isinstance(self.quality, QualityEvidence):
            raise ValueError("quality must be QualityEvidence")
        if self.work_unit not in WORK_UNITS:
            raise ValueError(f"work_unit must be one of {sorted(WORK_UNITS)}")
        _positive("work_amount", self.work_amount)
        _positive("duration_seconds", self.duration_seconds)
        _positive("it_energy_wh", self.it_energy_wh)
        _positive("peak_memory_mb", self.peak_memory_mb, allow_zero=True)
        if self.thermal_start not in THERMAL_STATES or self.thermal_end not in THERMAL_STATES:
            raise ValueError(f"thermal states must be one of {sorted(THERMAL_STATES)}")
        _validate_energy_method(
            self.energy_method, self.energy_scope, self.energy_provenance,
        )
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be a timezone-aware datetime")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.metadata_only is not True:
            raise ValueError("evidence records must be metadata-only")

    @property
    def average_it_power_watts(self) -> float:
        return self.it_energy_wh * 3600 / self.duration_seconds

    @property
    def work_rate_per_second(self) -> float:
        return self.work_amount / self.duration_seconds

    @property
    def energy_wh_per_work_unit(self) -> float:
        return self.it_energy_wh / self.work_amount

    @property
    def fingerprint(self) -> tuple:
        return (
            self.workload_class,
            self.run_mode,
            self.model_id,
            self.model_version,
            self.precision,
            self.device_key,
            self.compute_unit,
            self.stack_fingerprint,
            self.shape_fingerprint,
            self.work_unit,
            self.quality.metric,
            self.quality.fingerprint,
            self.energy_method,
            self.energy_scope,
            self.energy_provenance,
        )


@dataclass(frozen=True)
class EvidenceProfile:
    """Robust aggregate of repeated exact-fingerprint observations."""

    workload_class: str
    run_mode: str
    model_id: str
    model_version: str
    precision: str
    device_key: str
    compute_unit: str
    stack_fingerprint: str
    shape_fingerprint: str
    work_unit: str
    quality_metric: str
    quality_suite: str
    quality_suite_version: str
    quality_higher_is_better: bool
    quality_value: float
    quality_score: float
    sample_count: int
    work_rate_per_second: float
    energy_wh_per_work_unit: float
    average_it_power_watts: float
    peak_memory_mb: float
    throughput_relative_mad: float
    energy_relative_mad: float
    profiled_at: datetime
    energy_method: str
    energy_scope: str
    energy_provenance: str
    schema_version: str = SCHEMA_VERSION
    provenance: str = "MEASURED"

    def __post_init__(self) -> None:
        for name, value in (
            ("workload_class", self.workload_class),
            ("model_id", self.model_id),
            ("model_version", self.model_version),
            ("precision", self.precision),
            ("device_key", self.device_key),
            ("compute_unit", self.compute_unit),
            ("stack_fingerprint", self.stack_fingerprint),
            ("shape_fingerprint", self.shape_fingerprint),
            ("quality_metric", self.quality_metric),
            ("quality_suite", self.quality_suite),
            ("quality_suite_version", self.quality_suite_version),
        ):
            _required_text(name, value)
        if self.run_mode not in RUN_MODES:
            raise ValueError(f"run_mode must be one of {sorted(RUN_MODES)}")
        if self.work_unit not in WORK_UNITS:
            raise ValueError(f"work_unit must be one of {sorted(WORK_UNITS)}")
        if not isinstance(self.quality_higher_is_better, bool):
            raise ValueError("quality_higher_is_better must be boolean")
        if (isinstance(self.quality_value, bool)
                or not isinstance(self.quality_value, (int, float))
                or not math.isfinite(self.quality_value)):
            raise ValueError("quality_value must be finite")
        if (not isinstance(self.sample_count, int)
                or isinstance(self.sample_count, bool)):
            raise ValueError("sample_count must be an integer")
        if self.sample_count < MIN_PROFILE_SAMPLES:
            raise ValueError("evidence profile has insufficient samples")
        for name, value in (
            ("work_rate_per_second", self.work_rate_per_second),
            ("energy_wh_per_work_unit", self.energy_wh_per_work_unit),
            ("average_it_power_watts", self.average_it_power_watts),
        ):
            _positive(name, value)
        _positive("peak_memory_mb", self.peak_memory_mb, allow_zero=True)
        for name, value in (
            ("throughput_relative_mad", self.throughput_relative_mad),
            ("energy_relative_mad", self.energy_relative_mad),
        ):
            _positive(name, value, allow_zero=True)
        if (isinstance(self.quality_score, bool)
                or not isinstance(self.quality_score, (int, float))
                or not math.isfinite(self.quality_score)
                or not 0 <= self.quality_score <= 1):
            raise ValueError("quality_score must be finite and between 0 and 1")
        if (not isinstance(self.profiled_at, datetime)
                or self.profiled_at.tzinfo is None):
            raise ValueError("profiled_at must be timezone-aware")
        _validate_energy_method(
            self.energy_method, self.energy_scope, self.energy_provenance,
        )
        if self.schema_version != SCHEMA_VERSION or self.provenance != "MEASURED":
            raise ValueError("evidence profile must use the measured v1 contract")

    @property
    def cross_device_comparable(self) -> bool:
        return self.energy_method == "external_meter"

    @property
    def profile_id(self) -> str:
        fingerprint = (
            self.workload_class, self.run_mode, self.model_id,
            self.model_version, self.precision, self.device_key,
            self.compute_unit, self.stack_fingerprint,
            self.shape_fingerprint, self.work_unit, self.quality_metric,
            self.quality_suite, self.quality_suite_version,
            self.quality_higher_is_better, self.energy_method,
            self.energy_scope, self.energy_provenance,
        )
        digest = hashlib.sha256(
            json.dumps(fingerprint, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        return f"evidence-{digest}"


def _validate_energy_method(method: str, scope: str, provenance: str) -> None:
    if method not in ENERGY_METHODS:
        raise ValueError(f"energy_method must be one of {sorted(ENERGY_METHODS)}")
    if scope not in ENERGY_SCOPES:
        raise ValueError(f"energy_scope must be one of {sorted(ENERGY_SCOPES)}")
    if provenance not in ENERGY_PROVENANCE:
        raise ValueError(
            f"energy_provenance must be one of {sorted(ENERGY_PROVENANCE)}"
        )
    expected = {
        "external_meter": ("device_input", "MEASURED"),
        "apple_powermetrics": ("apple_soc_subsystems", "MEASURED_ESTIMATE"),
        "nvidia_smi_integrated": ("gpu_board", "MEASURED"),
    }[method]
    if (scope, provenance) != expected:
        raise ValueError(
            f"{method} requires energy_scope={expected[0]!r} and "
            f"energy_provenance={expected[1]!r}"
        )


def _relative_mad(values: list[float], centre: float) -> float:
    if centre <= 0:
        return 0.0
    return 1.4826 * statistics.median(abs(value - centre) for value in values) / centre


def build_evidence_profile(observations: list[WorkloadObservation]) -> EvidenceProfile:
    """Aggregate at least three runs with one exact evidence fingerprint."""
    if len(observations) < MIN_PROFILE_SAMPLES:
        raise ValueError(f"at least {MIN_PROFILE_SAMPLES} observations are required")
    if len({observation.fingerprint for observation in observations}) != 1:
        raise ValueError("observations must share one exact evidence fingerprint")
    first = observations[0]
    rates = [observation.work_rate_per_second for observation in observations]
    energy_rates = [
        observation.energy_wh_per_work_unit for observation in observations
    ]
    powers = [observation.average_it_power_watts for observation in observations]
    throughput = statistics.median(rates)
    energy = statistics.median(energy_rates)
    quality_values = [observation.quality.value for observation in observations]
    quality_scores = [observation.quality.score for observation in observations]
    return EvidenceProfile(
        workload_class=first.workload_class,
        run_mode=first.run_mode,
        model_id=first.model_id,
        model_version=first.model_version,
        precision=first.precision,
        device_key=first.device_key,
        compute_unit=first.compute_unit,
        stack_fingerprint=first.stack_fingerprint,
        shape_fingerprint=first.shape_fingerprint,
        work_unit=first.work_unit,
        quality_metric=first.quality.metric,
        quality_suite=first.quality.suite,
        quality_suite_version=first.quality.suite_version,
        quality_higher_is_better=first.quality.higher_is_better,
        quality_value=statistics.median(quality_values),
        quality_score=statistics.median(quality_scores),
        sample_count=len(observations),
        work_rate_per_second=throughput,
        energy_wh_per_work_unit=energy,
        average_it_power_watts=statistics.median(powers),
        peak_memory_mb=max(observation.peak_memory_mb for observation in observations),
        throughput_relative_mad=_relative_mad(rates, throughput),
        energy_relative_mad=_relative_mad(energy_rates, energy),
        profiled_at=max(observation.observed_at for observation in observations).astimezone(
            timezone.utc
        ),
        energy_method=first.energy_method,
        energy_scope=first.energy_scope,
        energy_provenance=first.energy_provenance,
    )


def planning_candidate_from_evidence(
    profile: EvidenceProfile,
    *,
    work_amount: float,
    market: str,
    location: str,
    series: list[GridDataPoint],
    currency: str,
    grid_provenance: str,
    pue: float = 1.2,
) -> PlanningCandidate:
    """Convert measured useful-work evidence into a normal planner candidate."""
    _positive("work_amount", work_amount)
    runtime_hours = work_amount / profile.work_rate_per_second / 3600
    return PlanningCandidate(
        key=(
            f"{profile.device_key}:{profile.model_id}:{profile.model_version}:"
            f"{profile.precision}:{profile.compute_unit}:{market}:{location}"
        ),
        hardware=f"{profile.device_key} ({profile.compute_unit})",
        market=market,
        location=location,
        series=series,
        runtime_hours=runtime_hours,
        it_power_kw=profile.average_it_power_watts / 1000,
        pue=pue,
        currency=currency,
        hardware_provenance="MEASURED",
        grid_provenance=grid_provenance,
        notes=(
            f"Measured across {profile.sample_count} exact-fingerprint runs",
            f"Useful work {work_amount:g} {profile.work_unit}",
            f"Quality {profile.quality_metric} {profile.quality_value:g}",
            f"Normalised quality score {profile.quality_score:.3f}",
            f"Throughput robust variation +/-{profile.throughput_relative_mad:.1%}",
            f"Energy robust variation +/-{profile.energy_relative_mad:.1%}",
            f"Energy method {profile.energy_method}; scope {profile.energy_scope}",
            ("Cross-device energy comparison permitted"
             if profile.cross_device_comparable
             else "Energy is restricted to same-device configuration comparison"),
        ),
    )


def observation_to_dict(observation: WorkloadObservation) -> dict:
    row = asdict(observation)
    row["observed_at"] = observation.observed_at.isoformat()
    return row


def observation_from_dict(row: dict) -> WorkloadObservation:
    if not isinstance(row, dict):
        raise ValueError("evidence observation must be an object")
    values = dict(row)
    quality_raw = values.get("quality")
    if not isinstance(quality_raw, dict):
        raise ValueError("evidence observation needs a quality object")
    try:
        values["quality"] = QualityEvidence(**quality_raw)
        values["observed_at"] = datetime.fromisoformat(
            str(values["observed_at"])
        )
        return WorkloadObservation(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid evidence observation: {exc}") from exc


def profile_to_dict(profile: EvidenceProfile) -> dict:
    row = asdict(profile)
    row["profiled_at"] = profile.profiled_at.isoformat()
    row["profile_id"] = profile.profile_id
    row["cross_device_comparable"] = profile.cross_device_comparable
    return row


def profile_from_dict(row: dict) -> EvidenceProfile:
    if not isinstance(row, dict):
        raise ValueError("evidence profile must be an object")
    values = {
        key: value for key, value in row.items()
        if key not in {"profile_id", "cross_device_comparable"}
    }
    try:
        values["profiled_at"] = datetime.fromisoformat(
            str(values["profiled_at"])
        )
        profile = EvidenceProfile(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid evidence profile: {exc}") from exc
    supplied_id = row.get("profile_id")
    if supplied_id is not None and supplied_id != profile.profile_id:
        raise ValueError("evidence profile ID does not match its fingerprint")
    return profile
