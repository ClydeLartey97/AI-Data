#!/usr/bin/env python3
"""Measure an NVIDIA GPU's compute ceiling, memory bandwidth and power draw.

The sibling of `measure_this_mac.py`, and deliberately the same shape: one
file, no repository checkout, a throwaway environment, three repeats, a spread
warning, a block you can photograph and a JSON file you can email yourself.
Runs on Windows and Linux.

**Why a separate script rather than one with a branch in it.** The two
architectures are not comparable and must never be scaled into each other. An
NVIDIA SM is not an Apple GPU core, so no per-unit rate crosses between them,
and `hardware/derive.py` refuses cross-vendor scaling outright. Keeping the
collectors apart makes that boundary structural instead of relying on someone
remembering it. What the two *do* share is the method — identical work,
identical timing discipline, repeated, with the spread reported — because that
is what makes any two measurements comparable at all.

**What this gives that the Apple script cannot: watts.**

This is the more valuable half and the reason to bother. On a Mac,
`powermetrics` needs administrator rights, so a machine you do not own yields
no energy figure at all, and every power number downstream stays ESTIMATED.
`nvidia-smi` reports board power to any user with no elevation whatsoever. So
this produces the first genuinely MEASURED **joules per unit of work** in the
project — the quantity the entire grid-scheduling argument rests on and has
never actually had.

**The scope caveat, which matters and is carried into the output.** This is
`board` scope: the GPU card's own draw, excluding CPU, RAM, fans and power
supply losses. A wall meter reads `outlet` scope and will be substantially
higher. The project already separates these (see `docs/discovery.md`), and a
board reading must never be compared against a wall reading as though equal.

**The laziness trap, in its CUDA form.** MLX queues work and returns
immediately, which once made a matmul look thirty times faster than the
hardware's theoretical peak. CUDA does exactly the same thing through streams.
Every timed region here calls `torch.cuda.synchronize()` inside it for the
same reason, and the reported rate is checked against the device's theoretical
peak so a physically impossible figure is refused rather than printed.

Two commands in a terminal:

    curl -fsSL https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_this_pc.py -o measure_this_pc.py
    python measure_this_pc.py

PyTorch is a large download — around 2.5 GB, because it bundles its own CUDA
runtime. That is the deliberate choice: it means the script does not care
which CUDA toolkit the machine has, which is the difference between working
and not working on a computer you did not configure.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Matched to measure_this_mac.py so the two are directly comparable as method,
# though never as numbers. Larger squares than the Mac script because a
# discrete card has dedicated memory and is not sharing it with the OS.
GEMM_SIZES = (1024, 2048, 4096)
DTYPES = ("float32", "float16")
BANDWIDTH_ELEMENTS = 64 << 20          # 64 Mi float32 = 256 MiB
WARMUP_ITERATIONS = 3
MEASURED_ITERATIONS = 10
REPEATS = 3
#: Power is sampled while the device is under sustained load. An idle reading
#: is meaningless for joules-per-token.
POWER_SAMPLE_INTERVAL = 0.1

VENV_MARKER = "AI_ENERGY_BENCH_VENV"
TORCH_INDEX = "https://download.pytorch.org/whl/cu121"


def _run(command: list[str], timeout: float = 15.0) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# Host description, from nvidia-smi. Needs nothing installed.
# --------------------------------------------------------------------------

def describe_host() -> dict:
    """Card identity and limits, before anything heavy is downloaded.

    Read first so a machine with no usable GPU can be rejected in seconds
    rather than after a 2.5 GB download.
    """
    query = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,power.limit,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ])
    cards = []
    for line in query.splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 5:
            continue
        cards.append({
            "name": fields[0],
            "memory_total_mib": _number(fields[1]),
            "power_limit_w": _number(fields[2]),
            "driver": fields[3],
            "compute_capability": fields[4],
        })
    return {
        "cards": cards,
        "card_count": len(cards),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


def _number(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


# --------------------------------------------------------------------------
# Power sampling, the capability the Apple script does not have.
# --------------------------------------------------------------------------

class PowerSampler:
    """Poll board power in the background while work runs.

    Board scope: the card's own draw. Not the wall, not the system. The scope
    travels with every figure it produces so a board reading is never compared
    against an outlet reading as though the two were the same measurement.
    """

    def __init__(self, index: int = 0,
                 interval: float = POWER_SAMPLE_INTERVAL) -> None:
        self.index = index
        self.interval = interval
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _poll(self) -> None:
        while not self._stop.is_set():
            raw = _run([
                "nvidia-smi", f"--id={self.index}",
                "--query-gpu=power.draw",
                "--format=csv,noheader,nounits",
            ], timeout=5.0)
            value = _number(raw.strip().splitlines()[0]) if raw.strip() else None
            if value is not None and value > 0:
                self.samples.append(value)
            self._stop.wait(self.interval)

    def __enter__(self) -> "PowerSampler":
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def result(self) -> dict:
        if not self.samples:
            return {"available": False,
                    "note": "nvidia-smi reported no usable power reading; "
                            "some cards and virtualised GPUs do not expose it"}
        return {
            "available": True,
            "median_watts": statistics.median(self.samples),
            "peak_watts": max(self.samples),
            "samples": len(self.samples),
            "scope": "board",
            "method": "nvidia-smi power.draw",
            "note": "Board scope: the card only. Excludes CPU, RAM, fans and "
                    "PSU losses, so it is NOT comparable to a wall meter.",
        }


# --------------------------------------------------------------------------
# Bootstrap.
# --------------------------------------------------------------------------

def bootstrap_and_reexec() -> None:
    if os.environ.get(VENV_MARKER):
        return
    if not shutil.which("nvidia-smi"):
        sys.exit("nvidia-smi was not found, so there is no NVIDIA GPU to "
                 "measure here. On an Apple Mac use measure_this_mac.py "
                 "instead.")
    host = describe_host()
    if not host["cards"]:
        sys.exit("nvidia-smi ran but reported no cards.")
    print(f"Found: {', '.join(c['name'] for c in host['cards'])}\n")

    workspace = Path(tempfile.mkdtemp(prefix="gpu-bench-"))
    print(f"Creating a temporary environment in {workspace}")
    print("Nothing is installed system-wide and no elevation is needed.\n")
    subprocess.run([sys.executable, "-m", "venv", str(workspace / "venv")],
                   check=True)
    binary = "Scripts" if platform.system() == "Windows" else "bin"
    python = workspace / "venv" / binary / (
        "python.exe" if platform.system() == "Windows" else "python")
    print("Downloading PyTorch with its bundled CUDA runtime.")
    print("This is about 2.5 GB and is the slow part — usually a few minutes.")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check",
                    "--index-url", TORCH_INDEX, "torch"], check=True)

    environment = dict(os.environ, **{VENV_MARKER: str(workspace)})
    try:
        subprocess.run([str(python), os.path.abspath(__file__)],
                       env=environment, check=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        print("\nRemoved the temporary environment. Nothing left behind.")
    sys.exit(0)


# --------------------------------------------------------------------------
# Measurement.
# --------------------------------------------------------------------------

def _relative_spread(samples: list[float], median: float) -> float:
    if not samples or median <= 0:
        return 0.0
    return statistics.median([abs(v - median) for v in samples]) / median


def _time(torch, operation, iterations: int, warmup: int) -> list[float]:
    """Time an operation, synchronising inside the measured region.

    CUDA is asynchronous: a kernel launch returns before the work is done. The
    synchronize call is not defensive tidiness, it is the measurement. Without
    it this times the launch queue and reports a rate far above what the
    silicon can physically do.
    """
    for _ in range(warmup):
        operation()
    torch.cuda.synchronize()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        torch.cuda.synchronize()
        samples.append(time.perf_counter() - started)
    return samples


def measure_gemm(torch, size: int, dtype_name: str) -> dict:
    dtype = getattr(torch, dtype_name)
    device = torch.device("cuda")
    left = torch.randn((size, size), device=device, dtype=dtype)
    right = torch.randn((size, size), device=device, dtype=dtype)
    samples = _time(torch, lambda: left @ right,
                    MEASURED_ITERATIONS, WARMUP_ITERATIONS)
    median = statistics.median(samples)
    del left, right
    torch.cuda.empty_cache()
    return {
        "name": "gemm", "dtype": dtype_name, "size": size,
        "seconds_median": median,
        "relative_spread": _relative_spread(samples, median),
        "rate": 2.0 * size ** 3 / median / 1e9,
        "unit": "GFLOP/s",
    }


def measure_bandwidth(torch) -> dict:
    """Streaming read, from a full reduction touching every element once."""
    device = torch.device("cuda")
    data = torch.randn(BANDWIDTH_ELEMENTS, device=device, dtype=torch.float32)
    samples = _time(torch, lambda: data.sum(),
                    MEASURED_ITERATIONS, WARMUP_ITERATIONS)
    median = statistics.median(samples)
    itemsize = data.element_size()
    del data
    torch.cuda.empty_cache()
    return {
        "name": "memory_bandwidth", "dtype": "float32",
        "size": BANDWIDTH_ELEMENTS,
        "seconds_median": median,
        "relative_spread": _relative_spread(samples, median),
        "rate": BANDWIDTH_ELEMENTS * itemsize / median / 1e9,
        "unit": "GB/s read",
    }


def run_once(torch) -> list[dict]:
    results = []
    for size in GEMM_SIZES:
        for dtype_name in DTYPES:
            results.append(measure_gemm(torch, size, dtype_name))
    results.append(measure_bandwidth(torch))
    return results


def device_units(torch) -> dict:
    """SM count and clocks — the divisor for any figure derived from this.

    The streaming-multiprocessor count is to an NVIDIA card what the GPU core
    count is to an Apple part: the unit the vendor replicates within one
    architecture generation, and therefore what a per-unit rate is divided by.
    Getting it wrong corrupts every sibling scaled from this measurement.
    """
    properties = torch.cuda.get_device_properties(0)
    return {
        "name": properties.name,
        "streaming_multiprocessors": properties.multi_processor_count,
        "total_memory_gb": properties.total_memory / 1e9,
        "compute_capability": f"{properties.major}.{properties.minor}",
    }


# --------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------

def headline(repeats: list[list[dict]], power: dict) -> dict:
    largest = max(GEMM_SIZES)

    def across(name, dtype=None, size=None):
        values = []
        for run in repeats:
            for entry in run:
                if entry["name"] != name:
                    continue
                if dtype and entry["dtype"] != dtype:
                    continue
                if size and entry["size"] != size:
                    continue
                values.append(entry["rate"])
        return values

    summary = {"gemm_size": largest}
    for label, dtype in (("gemm_fp16_gflops", "float16"),
                         ("gemm_fp32_gflops", "float32")):
        values = across("gemm", dtype, largest)
        if values:
            median = statistics.median(values)
            summary[label] = median
            summary[f"{label}_spread"] = (
                (max(values) - min(values)) / median if median else 0.0)
    values = across("memory_bandwidth")
    if values:
        median = statistics.median(values)
        summary["memory_bandwidth_gbs"] = median
        summary["memory_bandwidth_gbs_spread"] = (
            (max(values) - min(values)) / median if median else 0.0)

    # The figure that does not exist anywhere else in this project.
    if power.get("available") and summary.get("gemm_fp16_gflops"):
        watts = power["median_watts"]
        if watts > 0:
            summary["gflops_per_watt"] = summary["gemm_fp16_gflops"] / watts
            summary["power_scope"] = "board"
    return summary


def report(host: dict, units: dict, summary: dict, power: dict) -> str:
    lines = [
        "=" * 68,
        "  NVIDIA GPU MEASUREMENT — copy this block",
        "=" * 68,
        f"  Card            {units.get('name', '?')}",
        f"  SMs             {units.get('streaming_multiprocessors', '?')}"
        "   <- the divisor for anything scaled from this",
        f"  Memory          {units.get('total_memory_gb', 0):.1f} GB",
        f"  Compute cap.    {units.get('compute_capability', '?')}",
        f"  Driver          {host['cards'][0].get('driver', '?') if host.get('cards') else '?'}",
        f"  OS              {host.get('platform', '?')}",
        "-" * 68,
    ]
    for label, key in (("GEMM fp16", "gemm_fp16_gflops"),
                       ("GEMM fp32", "gemm_fp32_gflops")):
        if key in summary:
            spread = summary.get(f"{key}_spread", 0.0)
            lines.append(f"  {label}       {summary[key]:>11,.1f} GFLOP/s "
                         f"(spread {spread:.1%})")
    if "memory_bandwidth_gbs" in summary:
        spread = summary.get("memory_bandwidth_gbs_spread", 0.0)
        lines.append(f"  Bandwidth       {summary['memory_bandwidth_gbs']:>11,.1f} GB/s   "
                     f"(spread {spread:.1%})")
    lines.append("-" * 68)
    if power.get("available"):
        lines += [
            f"  Board power     {power['median_watts']:>11,.1f} W median, "
            f"{power['peak_watts']:,.1f} W peak",
            f"  Efficiency      {summary.get('gflops_per_watt', 0):>11,.1f} GFLOP/s per watt",
            "  Scope           board only — excludes CPU, RAM, fans, PSU.",
            "                  NOT comparable to a wall-meter reading.",
        ]
    else:
        lines.append(f"  Board power     unavailable — {power.get('note', '')}")
    lines += [
        "-" * 68,
        f"  {largest_note(summary)} {MEASURED_ITERATIONS} iterations, "
        f"{WARMUP_ITERATIONS} warm-up discarded, {REPEATS} repeats.",
        "=" * 68,
    ]
    spreads = [v for k, v in summary.items() if k.endswith("_spread")]
    if spreads and max(spreads) > 0.05:
        lines += [
            "  WARNING: spread above 5%. Something else was using the GPU.",
            "  Close other applications and run it again — a loaded device can",
            "  report a HIGHER figure than a quiet one, not just a noisier one.",
            "=" * 68,
        ]
    return "\n".join(lines)


def largest_note(summary: dict) -> str:
    size = summary.get("gemm_size")
    return f"  Measured at {size}x{size} square,"


def main() -> int:
    bootstrap_and_reexec()
    try:
        import torch
    except ImportError:
        print("PyTorch is not available, so no measurement can be made.")
        return 1
    if not torch.cuda.is_available():
        print("PyTorch cannot see a CUDA device. If this machine has an "
              "NVIDIA GPU, its driver may be too old for the bundled runtime.")
        return 1

    host = describe_host()
    units = device_units(torch)
    print(f"Measuring {units['name']} "
          f"({units['streaming_multiprocessors']} SMs) — "
          f"{REPEATS} repeats.\n")

    repeats = []
    with PowerSampler() as sampler:
        for index in range(REPEATS):
            print(f"  run {index + 1} of {REPEATS}...", flush=True)
            repeats.append(run_once(torch))
    power = sampler.result()

    summary = headline(repeats, power)
    print("\n" + report(host, units, summary, power))

    payload = {
        "host": host, "device": units, "summary": summary,
        "power": power, "runs": repeats,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "gemm_sizes": list(GEMM_SIZES), "dtypes": list(DTYPES),
            "bandwidth_elements": BANDWIDTH_ELEMENTS,
            "warmup_iterations": WARMUP_ITERATIONS,
            "measured_iterations": MEASURED_ITERATIONS,
            "repeats": REPEATS,
            "sibling": "measure_this_mac.py — same method, never comparable "
                       "numbers: an SM is not an Apple GPU core.",
        },
    }
    name = re.sub(r"[^a-z0-9]+", "-", units["name"].lower()).strip("-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for directory in (Path.home() / "Desktop", Path.home(), Path.cwd()):
        try:
            destination = directory / f"measurement-{name}-{stamp}.json"
            destination.write_text(json.dumps(payload, indent=2))
            print(f"\nFull result saved to: {destination}")
            print("Email that file to yourself, or photograph the block above.")
            break
        except OSError:
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
