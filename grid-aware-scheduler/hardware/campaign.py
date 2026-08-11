"""Unattended long-run characterisation: what a device sustains, not what it peaks at.

A short benchmark measures a cold machine sprinting. A scheduler places jobs
that run for hours, so the number it needs is the rate a device still holds
once it is hot. On a fanless part those are different numbers, and the gap
between them is the most useful thing here — it is also the figure almost
nobody publishes.

The design assumes nobody is watching. Samples are appended to disk as they
are taken, so a crash at hour three keeps hours one and two. Each phase is
bounded, cool-down is enforced between phases so thermal state does not carry
over, and the whole campaign refuses to start if the host is not in a state
that can produce valid measurements.
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

from hardware import microbench, preflight
from hardware.telemetry import TelemetryCollector

DEFAULT_SAMPLE_SECONDS = 10.0
DEFAULT_PHASE_MINUTES = 20.0
DEFAULT_COOLDOWN_MINUTES = 5.0
DEFAULT_REPEATS = 2


@dataclass
class Sample:
    elapsed_seconds: float
    rate: float
    rate_unit: str
    gpu_percent: float | None = None
    cpu_percent: float | None = None
    memory_available_gb: float | None = None
    battery_charge_mah: float | None = None
    on_ac_power: bool | None = None


@dataclass
class Phase:
    name: str
    operation: str
    dtype: str
    size: int
    started_at: str
    samples: list[Sample] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summarise(self) -> dict:
        """Peak, steady state, and how far the device fell between them."""
        if not self.samples:
            return {"name": self.name, "samples": 0}
        rates = [s.rate for s in self.samples]
        # Peak is the best rate ever observed, steady state the median of the
        # final third once thermal behaviour has settled. Taking peak from an
        # early window instead lets a still-warming first sample understate it
        # and report retention above 100%, which is meaningless.
        tail = rates[-max(1, len(rates) // 3):]
        peak, steady = max(rates), statistics.median(tail)
        return {
            "name": self.name,
            "operation": self.operation,
            "dtype": self.dtype,
            "size": self.size,
            "samples": len(rates),
            "duration_seconds": round(self.samples[-1].elapsed_seconds, 1),
            "rate_unit": self.samples[0].rate_unit,
            "peak": round(peak, 1),
            "steady_state": round(steady, 1),
            "retention_percent": round(steady / peak * 100, 1) if peak else None,
            "time_to_90_percent_seconds": self._time_to_fraction(0.9, peak),
            "min": round(min(rates), 1),
            "max": round(max(rates), 1),
        }

    def _time_to_fraction(self, fraction: float, peak: float) -> float | None:
        """When the rate first fell below a fraction of peak and stayed there."""
        if not peak:
            return None
        threshold = peak * fraction
        for index, sample in enumerate(self.samples):
            if sample.rate < threshold:
                remaining = self.samples[index:]
                if all(s.rate < threshold for s in remaining[:3]):
                    return round(sample.elapsed_seconds, 1)
        return None


def _operation(name: str, dtype: str, size: int):
    """Return a callable performing one unit of the named work, and its rate
    function, reusing the microbenchmark definitions rather than restating them."""
    mx = microbench._import_mlx()
    if name == "gemm":
        left = mx.random.normal((size, size)).astype(getattr(mx, dtype))
        right = mx.random.normal((size, size)).astype(getattr(mx, dtype))
        mx.eval(left, right)
        flops = 2.0 * size ** 3
        return (lambda: left @ right, mx.eval,
                lambda seconds: flops / seconds / 1e9, "GFLOP/s")
    if name == "gemv":
        vector = mx.random.normal((1, size)).astype(getattr(mx, dtype))
        matrix = mx.random.normal((size, size)).astype(getattr(mx, dtype))
        mx.eval(vector, matrix)
        read = size * size * matrix.dtype.size
        return (lambda: vector @ matrix, mx.eval,
                lambda seconds: read / seconds / 1e9, "GB/s effective")
    raise ValueError(f"unknown sustained operation {name!r}")


def run_phase(name: str, operation: str, dtype: str, size: int, *,
              minutes: float, sample_seconds: float,
              collector: TelemetryCollector,
              on_sample=None) -> Phase:
    """Drive one operation continuously, sampling the achieved rate."""
    work, evaluate, to_rate, unit = _operation(operation, dtype, size)
    phase = Phase(name=name, operation=operation, dtype=dtype, size=size,
                  started_at=datetime.now(timezone.utc).isoformat())

    for _ in range(microbench.WARMUP_ITERATIONS):
        evaluate(work())

    started = time.perf_counter()
    deadline = started + minutes * 60.0
    next_sample = started
    iterations, window_started = 0, started
    while time.perf_counter() < deadline:
        evaluate(work())
        iterations += 1
        now = time.perf_counter()
        if now >= next_sample + sample_seconds:
            window = now - window_started
            live = collector.snapshot()["devices"][0]["live"]
            battery = collector.battery()
            sample = Sample(
                elapsed_seconds=round(now - started, 2),
                rate=round(to_rate(window / iterations), 2),
                rate_unit=unit,
                gpu_percent=live.get("gpu_percent"),
                cpu_percent=live.get("cpu_percent"),
                memory_available_gb=live.get("memory_available_gb"),
                battery_charge_mah=battery.get("charge_mah"),
                on_ac_power=battery.get("on_ac_power"),
            )
            phase.samples.append(sample)
            if on_sample:
                on_sample(phase, sample)
            iterations, window_started, next_sample = 0, now, now
    return phase


def run(*, output: Path, phase_minutes: float = DEFAULT_PHASE_MINUTES,
        cooldown_minutes: float = DEFAULT_COOLDOWN_MINUTES,
        sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
        repeats: int = DEFAULT_REPEATS,
        skip_preflight: bool = False) -> dict:
    """Full overnight campaign, written to disk as it goes."""
    from hardware.apple_benchmark import stack_fingerprint

    collector = TelemetryCollector()
    context: dict = {}
    if not skip_preflight:
        gate = preflight.check(collector=collector, min_free_memory_gb=2.0)
        gate.raise_if_invalid()
        context = gate.context

    campaign = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "device": context.get("device") or platform.machine(),
        "stack": stack_fingerprint(),
        "context": context,
        "settings": {
            "phase_minutes": phase_minutes,
            "cooldown_minutes": cooldown_minutes,
            "sample_seconds": sample_seconds,
            "repeats": repeats,
        },
        "phases": [],
        "summaries": [],
        "warnings": [],
    }

    def persist() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(campaign, indent=1, default=str) + "\n",
                          encoding="utf-8")

    persist()
    plan = [
        ("sustained_gemm_fp16", "gemm", "float16", 2048),
        ("sustained_gemv_fp16", "gemv", "float16", 4096),
    ]
    for repeat in range(1, repeats + 1):
        for name, operation, dtype, size in plan:
            label = f"{name}_repeat{repeat}"
            print(f"[{datetime.now():%H:%M:%S}] {label}: {phase_minutes:g} min")
            try:
                phase = run_phase(
                    label, operation, dtype, size, minutes=phase_minutes,
                    sample_seconds=sample_seconds, collector=collector,
                    on_sample=lambda p, s: persist())
            except Exception as exc:
                campaign["warnings"].append(f"{label}: {exc}")
                persist()
                continue
            campaign["phases"].append(asdict(phase))
            summary = phase.summarise()
            campaign["summaries"].append(summary)
            persist()
            print(f"    peak {summary.get('peak')} {summary.get('rate_unit','')} "
                  f"-> steady {summary.get('steady_state')} "
                  f"({summary.get('retention_percent')}% retained)")

            if cooldown_minutes > 0:
                print(f"    cooling {cooldown_minutes:g} min")
                time.sleep(cooldown_minutes * 60.0)

    campaign["finished_at"] = datetime.now(timezone.utc).isoformat()
    persist()
    return campaign


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unattended sustained-throughput characterisation.")
    parser.add_argument("--output", type=Path,
                        default=Path("data/cache/campaign.json"))
    parser.add_argument("--phase-minutes", type=float, default=DEFAULT_PHASE_MINUTES)
    parser.add_argument("--cooldown-minutes", type=float,
                        default=DEFAULT_COOLDOWN_MINUTES)
    parser.add_argument("--sample-seconds", type=float, default=DEFAULT_SAMPLE_SECONDS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    campaign = run(output=args.output, phase_minutes=args.phase_minutes,
                   cooldown_minutes=args.cooldown_minutes,
                   sample_seconds=args.sample_seconds, repeats=args.repeats,
                   skip_preflight=args.skip_preflight)

    print("\n── sustained summary ──")
    for summary in campaign["summaries"]:
        print(f"  {summary['name']:<34} peak {summary['peak']:>8} -> "
              f"steady {summary['steady_state']:>8} {summary['rate_unit']:<16} "
              f"({summary['retention_percent']}% retained"
              + (f", fell below 90% at {summary['time_to_90_percent_seconds']}s"
                 if summary.get("time_to_90_percent_seconds") else ", no throttle seen")
              + ")")
    for warning in campaign["warnings"]:
        print(f"  warning: {warning}")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
