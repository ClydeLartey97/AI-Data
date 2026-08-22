"""Validated empirical throughput and power profiles.

A detected device is not a benchmark. Calibration is applied only when at
least three observations share an exact device, model, task, precision,
accelerator-count and software-stack fingerprint. The robust median is used so
one interrupted run cannot dominate a planning estimate.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from hardware import catalogue

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "calibration" / "profiles.json"
MIN_SAMPLES = 3


@dataclass(frozen=True)
class CalibrationObservation:
    device_key: str
    model_key: str
    task: str
    precision: str
    accelerator_count: int
    stack_fingerprint: str
    tokens: float
    duration_seconds: float
    average_it_power_watts: float
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.device_key not in catalogue.CATALOGUE:
            raise ValueError(f"unknown device {self.device_key!r}")
        if self.task not in ("training", "inference"):
            raise ValueError("task must be training or inference")
        if self.accelerator_count <= 0:
            raise ValueError("accelerator_count must be positive")
        if not self.stack_fingerprint.strip():
            raise ValueError("stack_fingerprint is required")
        if self.tokens <= 0 or not math.isfinite(self.tokens):
            raise ValueError("tokens must be finite and positive")
        if self.duration_seconds < 10 or not math.isfinite(self.duration_seconds):
            raise ValueError("duration_seconds must be finite and at least 10")
        if self.average_it_power_watts <= 0 or not math.isfinite(self.average_it_power_watts):
            raise ValueError("average_it_power_watts must be finite and positive")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

    @property
    def fingerprint(self) -> tuple[str, str, str, int, str, str]:
        return (self.device_key, self.model_key, self.task,
                self.accelerator_count, self.precision,
                self.stack_fingerprint)


@dataclass(frozen=True)
class CalibrationProfile:
    device_key: str
    model_key: str
    task: str
    precision: str
    accelerator_count: int
    stack_fingerprint: str
    sample_count: int
    tokens_per_second: float
    average_it_power_watts: float
    throughput_relative_mad: float
    power_relative_mad: float
    calibrated_at: datetime
    provenance: str = "MEASURED"

    def __post_init__(self) -> None:
        if self.device_key not in catalogue.CATALOGUE:
            raise ValueError(f"unknown device {self.device_key!r}")
        if self.task not in ("training", "inference"):
            raise ValueError("task must be training or inference")
        if self.accelerator_count <= 0 or self.sample_count < MIN_SAMPLES:
            raise ValueError("calibration profile has insufficient samples or devices")
        if not self.stack_fingerprint.strip():
            raise ValueError("stack_fingerprint is required")
        for name, value in (
            ("tokens_per_second", self.tokens_per_second),
            ("average_it_power_watts", self.average_it_power_watts),
        ):
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be finite and positive")
        for name, value in (
            ("throughput_relative_mad", self.throughput_relative_mad),
            ("power_relative_mad", self.power_relative_mad),
        ):
            if value < 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.calibrated_at.tzinfo is None:
            raise ValueError("calibrated_at must be timezone-aware")

    def matches(self, *, device_key: str, model_key: str, task: str,
                precision: str, accelerator_count: int,
                stack_fingerprint: str) -> bool:
        return (
            self.sample_count >= MIN_SAMPLES
            and self.device_key == device_key
            and self.model_key == model_key
            and self.task == task
            and self.precision == precision
            and self.accelerator_count == accelerator_count
            and self.stack_fingerprint == stack_fingerprint
        )


def _relative_mad(values: list[float], centre: float) -> float:
    if centre <= 0:
        return 0.0
    return 1.4826 * statistics.median(abs(value - centre) for value in values) / centre


def build_profile(observations: list[CalibrationObservation]) -> CalibrationProfile:
    if len(observations) < MIN_SAMPLES:
        raise ValueError(f"at least {MIN_SAMPLES} observations are required")
    fingerprints = {observation.fingerprint for observation in observations}
    if len(fingerprints) != 1:
        raise ValueError("calibration observations must have one exact fingerprint")
    first = observations[0]
    throughput = [o.tokens / o.duration_seconds for o in observations]
    powers = [o.average_it_power_watts for o in observations]
    throughput_median = statistics.median(throughput)
    power_median = statistics.median(powers)
    return CalibrationProfile(
        device_key=first.device_key,
        model_key=first.model_key,
        task=first.task,
        precision=first.precision,
        accelerator_count=first.accelerator_count,
        stack_fingerprint=first.stack_fingerprint,
        sample_count=len(observations),
        tokens_per_second=throughput_median,
        average_it_power_watts=power_median,
        throughput_relative_mad=_relative_mad(throughput, throughput_median),
        power_relative_mad=_relative_mad(powers, power_median),
        calibrated_at=max(o.observed_at for o in observations).astimezone(timezone.utc),
    )


def load_profiles(path: Path | None = None) -> list[CalibrationProfile]:
    target = path or DEFAULT_PATH
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("calibration file needs a profiles list")
    profiles = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each calibration profile must be an object")
        values = dict(row)
        values["calibrated_at"] = datetime.fromisoformat(str(values["calibrated_at"]))
        profile = CalibrationProfile(**values)
        if profile.sample_count < MIN_SAMPLES:
            raise ValueError("stored calibration profile has too few samples")
        profiles.append(profile)
    return profiles


def serialise_profiles(profiles: list[CalibrationProfile]) -> dict:
    rows = []
    for profile in profiles:
        row = asdict(profile)
        row["calibrated_at"] = profile.calibrated_at.isoformat()
        rows.append(row)
    return {"schema": "hardware-calibration-v1", "profiles": rows}


def profiles_from_payload(payload: dict) -> list[CalibrationProfile]:
    rows = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("calibration input needs a non-empty observations list")
    grouped: dict[tuple[str, str, str, int, str, str], list[CalibrationObservation]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each calibration observation must be an object")
        values = dict(row)
        try:
            values["observed_at"] = datetime.fromisoformat(str(values["observed_at"]))
            observation = CalibrationObservation(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid calibration observation: {exc}") from exc
        grouped.setdefault(observation.fingerprint, []).append(observation)
    insufficient = [f"{key}: {len(group)}" for key, group in grouped.items()
                    if len(group) < MIN_SAMPLES]
    if insufficient:
        raise ValueError("insufficient repeated observations: " + "; ".join(insufficient))
    return [build_profile(group) for group in grouped.values()]


def save_profiles(profiles: list[CalibrationProfile], path: Path | None = None) -> None:
    target = path or DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(serialise_profiles(profiles), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate repeated hardware observations into profiles."
    )
    parser.add_argument("observations", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    payload = json.loads(args.observations.read_text(encoding="utf-8"))
    profiles = profiles_from_payload(payload)
    save_profiles(profiles, args.output)
    print(f"Wrote {len(profiles)} validated calibration profile(s) to {args.output}")


if __name__ == "__main__":
    main()
