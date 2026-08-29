#!/usr/bin/env python3
"""Measure an Apple-silicon Mac's GPU compute and memory ceiling. One file, no setup.

**Scope, stated first because it is the easiest thing to misread.** This
measures the **GPU** via an MLX dense GEMM, plus the memory system it shares
with everything else on the die. Apple silicon also carries CPU performance
cores, CPU efficiency cores and a Neural Engine — four different architectures
in one package. The Neural Engine in particular runs a great deal of Apple's
real inference and is **not measured here**: MLX does not target it, and
reaching it needs Core ML with an explicit compute-unit constraint. Read the
numbers below as the GPU's ceiling, never as the device's total capability.

**Written for a machine you do not own and cannot configure.** A shared or
school Mac generally means no admin rights, no repository checkout, no
credentials, and limited time sitting in front of it. So this script carries
no dependency on the rest of the project, installs everything it needs into a
throwaway virtual environment inside your own home directory (which needs no
administrator password), prints a result block you can photograph or paste,
saves a JSON copy to the Desktop, and deletes its environment afterwards.

Get it onto the machine and run it with two commands in Terminal:

    curl -fsSL https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_this_mac.py -o ~/Desktop/measure_this_mac.py
    python3 ~/Desktop/measure_this_mac.py

Nothing is installed system-wide and nothing is left behind except the JSON
result on the Desktop. Takes a few minutes, most of it downloading MLX.

**The measurement matches `hardware/microbench.py` exactly** — same square
sizes, same warm-up count, same iteration count, same forced evaluation, same
streaming-read probe. That is the whole point: a number measured differently
is not comparable to the M2 anchor already recorded in `hardware/derive.py`,
and an incomparable number is worse than none because it invites a false
conclusion about the newer part.

Two traps are handled because this project has already been caught by both.
MLX is lazy, so every timed region forces `mx.eval` inside it — timing a
matmul without that measured 28 microseconds once and implied a rate about
thirty times the hardware's theoretical peak, which looks like a result rather
than an error. And a busy machine flatters as readily as it penalises, so the
run is repeated and the spread reported: a single reading on a loaded host
once came out *higher* than the validated quiet-machine figure.
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
import time
from datetime import datetime, timezone
from pathlib import Path

# Matched to hardware/microbench.py. Do not change without changing both.
GEMM_SIZES = (512, 1024, 2048)
DTYPES = ("float32", "float16")
BANDWIDTH_ELEMENTS = 32 << 20          # 32 Mi float32 = 128 MiB
WARMUP_ITERATIONS = 3
MEASURED_ITERATIONS = 10
#: Whole-benchmark repeats. Three is the project's rule for a valid profile:
#: an unrepeated reading can flatter a device as easily as penalise it.
REPEATS = 3

VENV_MARKER = "AI_ENERGY_BENCH_VENV"


# --------------------------------------------------------------------------
# Host description. Needs nothing installed, so it works even if MLX fails.
# --------------------------------------------------------------------------

def _run(command: list[str], timeout: float = 15.0) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def describe_host() -> dict:
    """Chip, cores and memory, straight from the operating system.

    The GPU core count matters more than anything else here: it is the divisor
    for every figure derived from this measurement. An M1 Ultra ships with 48
    or 64 GPU cores and an M1 Max with 24 or 32, so assuming the top
    configuration would corrupt the per-core rate for the whole family — the
    same error that once made this project report 72% of peak instead of 90%.
    """
    profile = _run(["system_profiler", "SPHardwareDataType"])
    display = _run(["system_profiler", "SPDisplaysDataType"])

    def field(pattern: str, text: str) -> str | None:
        found = re.search(pattern, text)
        return found.group(1).strip() if found else None

    gpu_cores = field(r"Total Number of Cores:\s*(\d+)", display)
    return {
        "chip": field(r"(?:Chip|Processor Name):\s*(.+)", profile),
        "model": field(r"Model Name:\s*(.+)", profile),
        "model_identifier": field(r"Model Identifier:\s*(.+)", profile),
        "memory": field(r"Memory:\s*(.+)", profile),
        "cpu_cores": field(r"Total Number of Cores:\s*(.+)", profile),
        "gpu_cores": int(gpu_cores) if gpu_cores and gpu_cores.isdigit() else None,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


def host_conditions() -> dict:
    """Starting load, so a reader can tell whether to trust the numbers.

    A measurement without its starting conditions is not reproducible. This is
    the light version of `hardware/preflight.py`, using only what ships with
    macOS, because a shared machine may not let you install anything before
    you have already decided whether it is worth measuring.
    """
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    page_size = 4096
    free_pages = 0
    vm = _run(["vm_stat"])
    size_match = re.search(r"page size of (\d+) bytes", vm)
    if size_match:
        page_size = int(size_match.group(1))
    for name in ("Pages free", "Pages inactive", "Pages speculative"):
        found = re.search(rf"{name}:\s+(\d+)", vm)
        if found:
            free_pages += int(found.group(1))
    swap = _run(["sysctl", "-n", "vm.swapusage"])
    swap_used = re.search(r"used\s*=\s*([\d.]+)M", swap)
    return {
        "load_average_1m": load,
        "approx_free_memory_gb": round(free_pages * page_size / 1e9, 2),
        "swap_used_mb": float(swap_used.group(1)) if swap_used else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Bootstrap: put MLX somewhere harmless, then re-run inside it.
# --------------------------------------------------------------------------

def bootstrap_and_reexec() -> None:
    """Build a throwaway environment in the user's home and re-enter it.

    Deliberately not `pip install --user` and never `sudo`: a shared machine
    should be left exactly as it was found, and a virtual environment can be
    deleted completely afterwards.
    """
    if os.environ.get(VENV_MARKER):
        return
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        sys.exit("This measures Apple-silicon Macs. This machine is "
                 f"{platform.system()} {platform.machine()}.")

    workspace = Path(tempfile.mkdtemp(prefix="apple-bench-"))
    print(f"Creating a temporary environment in {workspace}")
    print("Nothing is installed system-wide, and no password is needed.\n")
    subprocess.run([sys.executable, "-m", "venv", str(workspace / "venv")],
                   check=True)
    python = workspace / "venv" / "bin" / "python"
    print("Downloading MLX (about 150 MB, usually a minute or two)...")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "mlx"], check=True)

    environment = dict(os.environ, **{VENV_MARKER: str(workspace)})
    try:
        subprocess.run([str(python), os.path.abspath(__file__)],
                       env=environment, check=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        print(f"\nRemoved the temporary environment. Nothing left behind.")
    sys.exit(0)


# --------------------------------------------------------------------------
# The measurements themselves.
# --------------------------------------------------------------------------

def _relative_spread(samples: list[float], median: float) -> float:
    if not samples or median <= 0:
        return 0.0
    deviations = [abs(value - median) for value in samples]
    return statistics.median(deviations) / median


def _time(operation, evaluate, iterations: int, warmup: int) -> list[float]:
    """Time an operation, forcing evaluation so laziness cannot be timed.

    The forced `evaluate` inside the measured region is not optional. MLX
    queues work and returns immediately; timing without it measures the queue.
    """
    for _ in range(warmup):
        evaluate(operation())
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        evaluate(operation())
        samples.append(time.perf_counter() - started)
    return samples


def measure_gemm(mx, size: int, dtype_name: str) -> dict:
    """Dense square matrix multiply. Rate is GFLOP/s at 2*N^3 flops."""
    dtype = getattr(mx, dtype_name)
    left = mx.random.normal((size, size)).astype(dtype)
    right = mx.random.normal((size, size)).astype(dtype)
    mx.eval(left, right)
    samples = _time(lambda: left @ right, mx.eval,
                    MEASURED_ITERATIONS, WARMUP_ITERATIONS)
    median = statistics.median(samples)
    return {
        "name": "gemm", "dtype": dtype_name, "size": size,
        "seconds_median": median,
        "relative_spread": _relative_spread(samples, median),
        "rate": 2.0 * size ** 3 / median / 1e9,
        "unit": "GFLOP/s",
    }


def measure_bandwidth(mx) -> dict:
    """Streaming read, from a full-array reduction touching every element once."""
    data = mx.random.normal((BANDWIDTH_ELEMENTS,)).astype(mx.float32)
    mx.eval(data)
    itemsize = data.dtype.size
    samples = _time(lambda: mx.sum(data), mx.eval,
                    MEASURED_ITERATIONS, WARMUP_ITERATIONS)
    median = statistics.median(samples)
    return {
        "name": "memory_bandwidth", "dtype": "float32",
        "size": BANDWIDTH_ELEMENTS,
        "seconds_median": median,
        "relative_spread": _relative_spread(samples, median),
        "rate": BANDWIDTH_ELEMENTS * itemsize / median / 1e9,
        "unit": "GB/s read",
    }


def run_once(mx) -> list[dict]:
    results = []
    for size in GEMM_SIZES:
        for dtype_name in DTYPES:
            results.append(measure_gemm(mx, size, dtype_name))
    results.append(measure_bandwidth(mx))
    return results


# --------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------

def headline(repeats: list[list[dict]]) -> dict:
    """The two numbers that become an anchor, plus their run-to-run spread.

    The largest square size is used because small matrices do not saturate the
    device — this project measured 372 GFLOP/s at 512 against 2,287 at 2048 on
    the same chip, a six-fold difference in efficiency purely from work size.
    Quoting a small-matrix figure would understate the hardware badly.
    """
    largest = max(GEMM_SIZES)

    def across(name: str, dtype: str | None = None, size: int | None = None):
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

    summary = {}
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
    summary["gemm_size"] = largest
    return summary


def report(host: dict, conditions: dict, summary: dict) -> str:
    lines = [
        "=" * 66,
        "  APPLE SILICON GPU MEASUREMENT — copy this block",
        "=" * 66,
        f"  Chip            {host.get('chip') or 'unknown'}",
        f"  Model           {host.get('model') or '?'} "
        f"({host.get('model_identifier') or '?'})",
        f"  Memory          {host.get('memory') or '?'}",
        f"  CPU cores       {host.get('cpu_cores') or '?'}",
        f"  GPU cores       {host.get('gpu_cores') if host.get('gpu_cores') else '? — READ THIS OFF THE MACHINE'}",
        f"  macOS           {host.get('platform') or '?'}",
        "-" * 66,
    ]
    for label, key in (("GEMM fp16", "gemm_fp16_gflops"),
                       ("GEMM fp32", "gemm_fp32_gflops")):
        if key in summary:
            spread = summary.get(f"{key}_spread", 0.0)
            lines.append(f"  {label}       {summary[key]:>10,.1f} GFLOP/s "
                         f"(spread {spread:.1%} over {REPEATS} runs)")
    if "memory_bandwidth_gbs" in summary:
        spread = summary.get("memory_bandwidth_gbs_spread", 0.0)
        lines.append(f"  Bandwidth       {summary['memory_bandwidth_gbs']:>10,.1f} GB/s   "
                     f"(spread {spread:.1%} over {REPEATS} runs)")
    lines += [
        "-" * 66,
        f"  Measured at {summary.get('gemm_size')}x{summary.get('gemm_size')} square, "
        f"{MEASURED_ITERATIONS} iterations,",
        f"  {WARMUP_ITERATIONS} warm-up discarded, {REPEATS} repeats.",
        f"  Starting load average {conditions.get('load_average_1m')}, "
        f"~{conditions.get('approx_free_memory_gb')} GB free.",
        "  Scope: GPU only. The Neural Engine is not measured.",
        "=" * 66,
    ]
    spreads = [value for key, value in summary.items() if key.endswith("_spread")]
    if spreads and max(spreads) > 0.05:
        lines += [
            "  WARNING: spread above 5%. Something else was using the machine.",
            "  Close other applications and run it again — a loaded host can",
            "  report a HIGHER figure than a quiet one, not just a noisier one.",
            "=" * 66,
        ]
    return "\n".join(lines)


def main() -> int:
    bootstrap_and_reexec()
    try:
        import mlx.core as mx
    except ImportError:
        print("MLX is not available, so no measurement can be made.")
        return 1

    host = describe_host()
    conditions = host_conditions()
    print(f"Measuring {host.get('chip') or 'this Mac'} — "
          f"{REPEATS} repeats, a few minutes.\n")

    repeats = []
    for index in range(REPEATS):
        print(f"  run {index + 1} of {REPEATS}...", flush=True)
        repeats.append(run_once(mx))

    summary = headline(repeats)
    print("\n" + report(host, conditions, summary))

    payload = {
        "host": host,
        "conditions": conditions,
        "summary": summary,
        "runs": repeats,
        "methodology": {
            "gemm_sizes": list(GEMM_SIZES),
            "dtypes": list(DTYPES),
            "bandwidth_elements": BANDWIDTH_ELEMENTS,
            "warmup_iterations": WARMUP_ITERATIONS,
            "measured_iterations": MEASURED_ITERATIONS,
            "repeats": REPEATS,
            "matches": "hardware/microbench.py",
        },
    }
    chip = (host.get("chip") or "apple").lower().replace(" ", "-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for directory in (Path.home() / "Desktop", Path.home(), Path.cwd()):
        try:
            destination = directory / f"measurement-{chip}-{stamp}.json"
            destination.write_text(json.dumps(payload, indent=2))
            print(f"\nFull result saved to: {destination}")
            print("Email that file to yourself, or photograph the block above.")
            break
        except OSError:
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
