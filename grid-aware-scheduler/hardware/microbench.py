"""Machine constants: achievable arithmetic throughput and memory bandwidth.

These are the two numbers a roofline needs, and they are the only part of the
benchmark programme that does not depend on a model being downloaded. They
are deliberately separate from workload measurement: a GEMM rate is a
property of the silicon and the framework, while tokens per second is a
property of a model running on them.

What makes these comparable across architectures is not the code — MLX here,
CUDA elsewhere — but that the work is identical: the same arithmetic, the
same shapes, the same precision, and a rate expressed in operations per
second rather than in a score.

Two failure modes are guarded explicitly. MLX is lazy, so a timing loop that
does not force evaluation measures graph construction rather than compute and
reports an absurd figure. And the first call on a device pays compilation and
allocation costs, so warm-up iterations are discarded rather than averaged in.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hardware import preflight

#: Square matrix sizes. 4096 fp32 needs three 64 MiB buffers, which is the
#: practical ceiling on an 8 GB part that must not be pushed into swap.
DEFAULT_GEMM_SIZES = (512, 1024, 2048)
DEFAULT_DTYPES = ("float32", "float16")
#: Elements in the bandwidth probe: 32 Mi float32 = 128 MiB per buffer.
DEFAULT_BANDWIDTH_ELEMENTS = 32 << 20
WARMUP_ITERATIONS = 3
MEASURED_ITERATIONS = 10


class MLXUnavailable(RuntimeError):
    """MLX is not installed, so no Apple-silicon measurement can be made."""


@dataclass(frozen=True)
class Measurement:
    name: str
    dtype: str
    size: int
    iterations: int
    seconds_median: float
    seconds_relative_mad: float
    rate: float
    rate_unit: str
    notes: tuple[str, ...] = ()


@dataclass
class MicrobenchReport:
    device: str
    observed_at: str
    stack: str
    measurements: list[Measurement] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["measurements"] = [asdict(m) for m in self.measurements]
        return payload

    def peak(self, name: str) -> Measurement | None:
        candidates = [m for m in self.measurements if m.name == name]
        return max(candidates, key=lambda m: m.rate) if candidates else None


def _import_mlx():
    try:
        import mlx.core as mx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MLXUnavailable(
            "MLX is not installed; install requirements-apple.txt to measure "
            "Apple silicon") from exc
    return mx


def _relative_mad(values: list[float], centre: float) -> float:
    if centre <= 0 or len(values) < 2:
        return 0.0
    return 1.4826 * statistics.median(abs(v - centre) for v in values) / centre


def _time_iterations(operation, evaluate, iterations: int, warmup: int) -> list[float]:
    """Time an operation, forcing evaluation so laziness cannot be timed."""
    for _ in range(warmup):
        evaluate(operation())
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        evaluate(operation())
        samples.append(time.perf_counter() - started)
    return samples


def gemm(size: int, dtype_name: str, *, iterations: int = MEASURED_ITERATIONS,
         warmup: int = WARMUP_ITERATIONS) -> Measurement:
    """Dense square matrix multiply. Rate is GFLOP/s at 2*N^3 flops."""
    mx = _import_mlx()
    dtype = getattr(mx, dtype_name)
    left = mx.random.normal((size, size)).astype(dtype)
    right = mx.random.normal((size, size)).astype(dtype)
    mx.eval(left, right)

    samples = _time_iterations(lambda: left @ right, mx.eval, iterations, warmup)
    median = statistics.median(samples)
    flops = 2.0 * size ** 3
    return Measurement(
        name="gemm",
        dtype=dtype_name,
        size=size,
        iterations=iterations,
        seconds_median=median,
        seconds_relative_mad=_relative_mad(samples, median),
        rate=flops / median / 1e9,
        rate_unit="GFLOP/s",
        notes=(f"{warmup} warm-up iterations discarded",),
    )


def memory_bandwidth(elements: int = DEFAULT_BANDWIDTH_ELEMENTS,
                     dtype_name: str = "float32", *,
                     iterations: int = MEASURED_ITERATIONS,
                     warmup: int = WARMUP_ITERATIONS) -> Measurement:
    """Streaming read bandwidth from a full-array reduction.

    A sum touches every element once, so bytes moved is elements x itemsize.
    This under-reports peak bandwidth compared with a read+write copy, and is
    reported as a sustained streaming read rather than a headline figure.
    """
    mx = _import_mlx()
    dtype = getattr(mx, dtype_name)
    data = mx.random.normal((elements,)).astype(dtype)
    mx.eval(data)
    itemsize = data.dtype.size
    samples = _time_iterations(lambda: mx.sum(data), mx.eval, iterations, warmup)
    median = statistics.median(samples)
    return Measurement(
        name="memory_bandwidth",
        dtype=dtype_name,
        size=elements,
        iterations=iterations,
        seconds_median=median,
        seconds_relative_mad=_relative_mad(samples, median),
        rate=elements * itemsize / median / 1e9,
        rate_unit="GB/s read",
        notes=("Streaming read only; a read+write copy would report higher",),
    )


def run(*, sizes=DEFAULT_GEMM_SIZES, dtypes=DEFAULT_DTYPES,
        bandwidth_elements: int = DEFAULT_BANDWIDTH_ELEMENTS,
        iterations: int = MEASURED_ITERATIONS,
        skip_preflight: bool = False,
        min_free_memory_gb: float = 2.0) -> MicrobenchReport:
    """Full sweep. Microbenchmarks need far less memory than a model, so the
    preflight floor is lower here than for workload measurement."""
    from hardware.apple_benchmark import stack_fingerprint

    warnings: list[str] = []
    context: dict = {}
    if not skip_preflight:
        gate = preflight.check(min_free_memory_gb=min_free_memory_gb)
        context = gate.context
        warnings.extend(gate.cautions)
        gate.raise_if_invalid()

    report = MicrobenchReport(
        device=context.get("device") or platform.machine(),
        observed_at=datetime.now(timezone.utc).isoformat(),
        stack=stack_fingerprint(),
        context=context,
        warnings=warnings,
    )
    for dtype_name in dtypes:
        for size in sizes:
            try:
                report.measurements.append(
                    gemm(size, dtype_name, iterations=iterations))
            except (MLXUnavailable, ValueError, RuntimeError) as exc:
                report.warnings.append(f"gemm {dtype_name} {size}: {exc}")
    try:
        report.measurements.append(
            memory_bandwidth(bandwidth_elements, iterations=iterations))
    except (MLXUnavailable, ValueError, RuntimeError) as exc:
        report.warnings.append(f"memory bandwidth: {exc}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure achievable arithmetic throughput and memory bandwidth.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=MEASURED_ITERATIONS)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_GEMM_SIZES))
    parser.add_argument("--skip-preflight", action="store_true",
                        help="record the run as unvalidated; use only for diagnosis")
    args = parser.parse_args()

    report = run(sizes=tuple(args.sizes), iterations=args.iterations,
                 skip_preflight=args.skip_preflight)
    for measurement in report.measurements:
        print(f"{measurement.name:>17} {measurement.dtype:>9} "
              f"{measurement.size:>9} : {measurement.rate:9.1f} "
              f"{measurement.rate_unit:<12} "
              f"(+/-{measurement.seconds_relative_mad * 100:.1f}%)")
    for warning in report.warnings:
        print(f"  warning: {warning}")
    if args.output:
        args.output.write_text(json.dumps(report.as_dict(), indent=2) + "\n",
                               encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
