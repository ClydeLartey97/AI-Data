"""Executable, metadata-only Apple-silicon workload measurement.

The runner never stores command output or workload content. A workload writes
only useful-work and quality results to a small JSON contract. Runtime and peak
RSS are measured around the command. Energy must come from either a calibrated
external meter or an authorised Apple ``powermetrics`` subsystem trace.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.evidence import QualityEvidence, WorkloadObservation
from core.evidence_store import ingest_observation

POWER_PATTERN = re.compile(
    r"\b(CPU|GPU|ANE)\s+Power:\s*([0-9]+(?:\.[0-9]+)?)\s*(mW|W)\b",
    re.IGNORECASE,
)


def _required_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if len(value) > 200:
        raise ValueError(f"{name} cannot exceed 200 characters")
    return value.strip()


def _finite(name: str, value: Any, *, minimum: float | None = None) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


@dataclass(frozen=True)
class BenchmarkSpec:
    workload_class: str
    run_mode: str
    model_id: str
    model_version: str
    precision: str
    device_key: str
    compute_unit: str
    shape_fingerprint: str
    work_unit: str
    quality_metric: str
    quality_higher_is_better: bool
    evaluation_suite: str
    evaluation_suite_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("workload_class", self.workload_class),
            ("run_mode", self.run_mode),
            ("model_id", self.model_id),
            ("model_version", self.model_version),
            ("precision", self.precision),
            ("device_key", self.device_key),
            ("compute_unit", self.compute_unit),
            ("shape_fingerprint", self.shape_fingerprint),
            ("work_unit", self.work_unit),
            ("quality_metric", self.quality_metric),
            ("evaluation_suite", self.evaluation_suite),
            ("evaluation_suite_version", self.evaluation_suite_version),
        ):
            _required_text(name, value)
        if not isinstance(self.quality_higher_is_better, bool):
            raise ValueError("quality_higher_is_better must be boolean")

    @classmethod
    def from_path(cls, path: Path) -> "BenchmarkSpec":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("benchmark specification must be a JSON object")
        try:
            return cls(**payload)
        except TypeError as exc:
            raise ValueError(f"invalid benchmark specification: {exc}") from exc


@dataclass(frozen=True)
class WorkloadResult:
    work_amount: float
    quality_value: float
    quality_score: float

    @classmethod
    def from_path(cls, path: Path) -> "WorkloadResult":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("workload result must be a JSON object")
        expected_keys = {"work_amount", "quality_value", "quality_score"}
        if set(payload) != expected_keys:
            raise ValueError(
                "workload result must contain only work_amount, quality_value "
                "and quality_score"
            )
        result = cls(
            work_amount=_finite("work_amount", payload.get("work_amount"),
                                minimum=0.000001),
            quality_value=_finite("quality_value", payload.get("quality_value")),
            quality_score=_finite("quality_score", payload.get("quality_score"),
                                  minimum=0),
        )
        if result.quality_score > 1:
            raise ValueError("quality_score must be at most 1")
        return result


def stack_fingerprint() -> str:
    packages = []
    for name in ("mlx", "mlx-lm", "coremltools"):
        try:
            packages.append(f"{name}-{importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            packages.append(f"{name}-absent")
    components = [
        f"python-{platform.python_version()}",
        f"macos-{platform.mac_ver()[0] or 'unknown'}",
        *packages,
    ]
    return "_".join(components)


def parse_powermetrics_average_watts(output: str) -> float:
    """Return mean combined CPU/GPU/ANE power from a text trace."""
    by_subsystem: dict[str, list[float]] = {"cpu": [], "gpu": [], "ane": []}
    for match in POWER_PATTERN.finditer(output):
        subsystem, raw_value, unit = match.groups()
        watts = float(raw_value) / 1000 if unit.lower() == "mw" else float(raw_value)
        if math.isfinite(watts) and watts >= 0:
            by_subsystem[subsystem.lower()].append(watts)
    present = [values for values in by_subsystem.values() if values]
    if not present:
        raise ValueError("powermetrics trace contains no CPU, GPU or ANE power samples")
    return sum(sum(values) / len(values) for values in present)


class _PeakMemoryMonitor:
    def __init__(self, pid: int, interval_seconds: float = 0.05) -> None:
        self.pid = pid
        self.interval_seconds = interval_seconds
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        import psutil
        try:
            process = psutil.Process(self.pid)
        except psutil.Error:
            return
        while not self._stop.is_set():
            try:
                processes = [process, *process.children(recursive=True)]
                rss = sum(item.memory_info().rss for item in processes
                          if item.is_running())
                self.peak_bytes = max(self.peak_bytes, rss)
            except psutil.Error:
                pass
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> float:
        self._stop.set()
        self._thread.join(timeout=2)
        return self.peak_bytes / (1024 * 1024)


class _PowermetricsTrace:
    def __init__(self, sample_rate_ms: int = 500) -> None:
        self.sample_rate_ms = sample_rate_ms
        self._process: subprocess.Popen | None = None
        self._path: Path | None = None

    def start(self) -> None:
        if platform.system() != "Darwin" or not Path("/usr/bin/powermetrics").exists():
            raise ValueError("Apple powermetrics is unavailable on this host")
        handle = tempfile.NamedTemporaryFile(prefix="ai-energy-power-", suffix=".txt",
                                             delete=False)
        handle.close()
        self._path = Path(handle.name)
        output = self._path.open("w", encoding="utf-8")
        try:
            try:
                self._process = subprocess.Popen(
                    [
                        "sudo", "-n", "/usr/bin/powermetrics",
                        "--samplers", "cpu_power,gpu_power,ane_power",
                        "--sample-rate", str(self.sample_rate_ms),
                        "--buffer-size", "1",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            finally:
                output.close()
        except BaseException:
            self._path.unlink(missing_ok=True)
            raise
        time.sleep(min(0.65, self.sample_rate_ms / 1000 + 0.15))
        if self._process.poll() is not None:
            message = self._path.read_text(encoding="utf-8", errors="replace")
            self._path.unlink(missing_ok=True)
            raise ValueError(
                "powermetrics could not start without interactive authorisation: "
                + message.strip()[:180]
            )

    def stop(self, duration_seconds: float) -> float:
        if self._process is None or self._path is None:
            raise RuntimeError("powermetrics trace was not started")
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        output = self._path.read_text(encoding="utf-8", errors="replace")
        self._path.unlink(missing_ok=True)
        average_watts = parse_powermetrics_average_watts(output)
        return average_watts * duration_seconds / 3600


def run_command_benchmark(
    spec: BenchmarkSpec,
    command: list[str],
    *,
    result_path: Path,
    external_energy_wh: float | None = None,
    external_energy_supplier: Callable[[], float] | None = None,
    use_powermetrics: bool = False,
    thermal_start: str = "unknown",
    thermal_end: str = "unknown",
    store_path: Path | None = None,
) -> dict[str, Any]:
    if not command:
        raise ValueError("a workload command is required")
    methods = sum((
        external_energy_wh is not None,
        external_energy_supplier is not None,
        use_powermetrics,
    ))
    if methods != 1:
        raise ValueError(
            "select exactly one energy method: external value, external supplier "
            "or powermetrics"
        )
    if result_path.exists():
        result_path.unlink()
    power_trace = _PowermetricsTrace() if use_powermetrics else None
    if power_trace:
        power_trace.start()
    started = time.perf_counter()
    environment = dict(os.environ)
    environment["AI_ENERGY_RESULT_PATH"] = str(result_path)
    environment.update({
        "AI_ENERGY_WORKLOAD_CLASS": spec.workload_class,
        "AI_ENERGY_RUN_MODE": spec.run_mode,
        "AI_ENERGY_MODEL_ID": spec.model_id,
        "AI_ENERGY_MODEL_VERSION": spec.model_version,
        "AI_ENERGY_WORK_UNIT": spec.work_unit,
        "AI_ENERGY_EVALUATION_SUITE": spec.evaluation_suite,
        "AI_ENERGY_EVALUATION_VERSION": spec.evaluation_suite_version,
        "AI_ENERGY_SHAPE_FINGERPRINT": spec.shape_fingerprint,
    })
    try:
        process = subprocess.Popen(command, env=environment)
    except BaseException:
        if power_trace:
            try:
                power_trace.stop(max(time.perf_counter() - started, 0.000001))
            except (RuntimeError, ValueError):
                pass
        raise
    memory = _PeakMemoryMonitor(process.pid)
    memory.start()
    return_code = process.wait()
    duration_seconds = time.perf_counter() - started
    peak_memory_mb = memory.stop()
    power_energy_wh: float | None = None
    power_error: ValueError | None = None
    if power_trace:
        try:
            power_energy_wh = power_trace.stop(duration_seconds)
        except ValueError as exc:
            power_error = exc
    if return_code != 0:
        result_path.unlink(missing_ok=True)
        raise RuntimeError(f"workload command exited with status {return_code}")
    if power_error is not None:
        result_path.unlink(missing_ok=True)
        raise power_error
    if not result_path.exists():
        raise ValueError(
            "workload did not write the metadata-only result JSON named by "
            "AI_ENERGY_RESULT_PATH"
        )
    try:
        result = WorkloadResult.from_path(result_path)
    finally:
        result_path.unlink(missing_ok=True)

    if power_trace:
        if power_energy_wh is None:
            raise RuntimeError("powermetrics completed without an energy result")
        energy_wh = power_energy_wh
        energy_method = "apple_powermetrics"
        energy_scope = "apple_soc_subsystems"
        energy_provenance = "MEASURED_ESTIMATE"
    else:
        if external_energy_supplier is not None:
            external_energy_wh = external_energy_supplier()
        energy_wh = _finite(
            "external_energy_wh", external_energy_wh, minimum=0.000000001,
        )
        energy_method = "external_meter"
        energy_scope = "device_input"
        energy_provenance = "MEASURED"

    observation = WorkloadObservation(
        run_id=str(uuid.uuid4()),
        workload_class=spec.workload_class,
        run_mode=spec.run_mode,
        model_id=spec.model_id,
        model_version=spec.model_version,
        precision=spec.precision,
        device_key=spec.device_key,
        compute_unit=spec.compute_unit,
        stack_fingerprint=stack_fingerprint(),
        shape_fingerprint=spec.shape_fingerprint,
        work_amount=result.work_amount,
        work_unit=spec.work_unit,
        duration_seconds=duration_seconds,
        it_energy_wh=energy_wh,
        peak_memory_mb=peak_memory_mb,
        thermal_start=thermal_start,
        thermal_end=thermal_end,
        observed_at=datetime.now(timezone.utc),
        quality=QualityEvidence(
            metric=spec.quality_metric,
            value=result.quality_value,
            score=result.quality_score,
            higher_is_better=spec.quality_higher_is_better,
            suite=spec.evaluation_suite,
            suite_version=spec.evaluation_suite_version,
        ),
        energy_method=energy_method,
        energy_scope=energy_scope,
        energy_provenance=energy_provenance,
    )
    return ingest_observation(observation, store_path)


def run_mlx_probe(matrix_size: int = 512, iterations: int = 20) -> dict[str, Any]:
    """Exercise the MLX GPU path without creating scheduler evidence."""
    if not 64 <= matrix_size <= 4096:
        raise ValueError("matrix_size must be between 64 and 4096")
    if not 1 <= iterations <= 1000:
        raise ValueError("iterations must be between 1 and 1000")
    import mlx.core as mx
    left = mx.random.normal((matrix_size, matrix_size))
    right = mx.random.normal((matrix_size, matrix_size))
    mx.eval(left, right)
    started = time.perf_counter()
    value = left
    for _ in range(iterations):
        value = mx.matmul(value, right)
        mx.eval(value)
    duration = time.perf_counter() - started
    operations = 2 * matrix_size ** 3 * iterations
    return {
        "probe": "mlx-matrix-multiply",
        "matrix_size": matrix_size,
        "iterations": iterations,
        "duration_seconds": duration,
        "operations": operations,
        "operations_per_second": operations / duration,
        "stack_fingerprint": stack_fingerprint(),
        "performance_provenance": "MEASURED",
        "energy_provenance": "UNAVAILABLE",
        "scheduler_profile_created": False,
        "note": (
            "This proves the local MLX execution path only. It has no task "
            "quality or watt-hour measurement and cannot schedule AI work."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a privacy-preserving Apple-silicon benchmark.",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    probe = subparsers.add_parser("probe", help="Verify the local MLX GPU path")
    probe.add_argument("--matrix-size", type=int, default=512)
    probe.add_argument("--iterations", type=int, default=20)

    record = subparsers.add_parser(
        "record", help="Measure and ingest one quality-scored workload run",
    )
    record.add_argument("spec", type=Path)
    record.add_argument("--result-json", type=Path, required=True)
    energy = record.add_mutually_exclusive_group(required=True)
    energy.add_argument("--external-energy-wh", type=float)
    energy.add_argument("--prompt-external-energy", action="store_true")
    energy.add_argument("--powermetrics", action="store_true")
    record.add_argument("--thermal-start", default="unknown")
    record.add_argument("--thermal-end", default="unknown")
    record.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.mode == "probe":
        print(json.dumps(run_mlx_probe(args.matrix_size, args.iterations), indent=2))
        return
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    outcome = run_command_benchmark(
        BenchmarkSpec.from_path(args.spec),
        command,
        result_path=args.result_json,
        external_energy_wh=args.external_energy_wh,
        external_energy_supplier=(
            lambda: float(input("Calibrated external-meter interval energy (Wh): "))
            if args.prompt_external_energy else None
        ),
        use_powermetrics=args.powermetrics,
        thermal_start=args.thermal_start,
        thermal_end=args.thermal_end,
    )
    print(json.dumps(outcome, indent=2))


if __name__ == "__main__":
    main()
