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

The zero-setup launcher is the preferred entry point. It supplies its own
temporary Python runtime, so Xcode, Homebrew and Python are not prerequisites:

    bash measure_mac.command

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
import argparse
import importlib.metadata
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
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
SCHEMA = "ai-energy-hardware-measurement-v1"
COLLECTOR_VERSION = "2.0"
MAX_RELATIVE_SPREAD = 0.05
MIN_FREE_MEMORY_GB = 3.0
MAX_ACTIVE_SWAP_MB = 1024.0
MAX_BUSY_PERCENT = 25.0

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
    cpu_percent = None
    storage_free_gb = None
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
    approximate_free_gb = round(free_pages * page_size / 1e9, 2)
    swap_used_mb = float(swap_used.group(1)) if swap_used else None

    # The launcher supplies psutil in the temporary environment. Keep the
    # operating-system fallbacks above so this file remains useful by itself.
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1.0)
        approximate_free_gb = round(psutil.virtual_memory().available / 1e9, 2)
        swap_used_mb = round(psutil.swap_memory().used / 1e6, 1)
        storage_free_gb = round(shutil.disk_usage(Path.home()).free / 1e9, 2)
    except ImportError:
        pass

    power = _run(["pmset", "-g", "batt"])
    settings = _run(["pmset", "-g"])
    thermal = _run(["pmset", "-g", "therm"])
    low_power_match = re.search(r"lowpowermode\s+(\d+)", settings)
    no_thermal_warning = "no thermal warning level has been recorded" in thermal.lower()
    return {
        "load_average_1m": load,
        "cpu_percent": cpu_percent,
        "approx_free_memory_gb": approximate_free_gb,
        "swap_used_mb": swap_used_mb,
        "storage_free_gb": storage_free_gb,
        "power_source": ("ac" if "AC Power" in power else
                         "battery" if "Battery Power" in power else "unknown"),
        "low_power_mode": (low_power_match.group(1) == "1"
                           if low_power_match else None),
        "thermal_warning": (False if no_thermal_warning else
                            True if "thermal" in thermal.lower() and
                            "warning" in thermal.lower() else None),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_conditions(conditions: dict, host: dict | None = None) -> dict:
    """Return a machine-readable preflight decision before expensive setup."""
    blockers: list[str] = []
    cautions: list[str] = []
    free_gb = conditions.get("approx_free_memory_gb")
    if free_gb is None or free_gb <= 0:
        blockers.append("available memory could not be read")
    elif free_gb < MIN_FREE_MEMORY_GB:
        blockers.append(
            f"only {free_gb:.2f} GB memory is free; {MIN_FREE_MEMORY_GB:.1f} GB is required")
    swap_mb = conditions.get("swap_used_mb")
    if swap_mb is not None and swap_mb > MAX_ACTIVE_SWAP_MB:
        blockers.append(
            f"{swap_mb:.0f} MB of swap is active; restart or close applications first")
    cpu = conditions.get("cpu_percent")
    if cpu is not None and cpu > MAX_BUSY_PERCENT:
        blockers.append(
            f"CPU is already {cpu:.0f}% busy; close other applications first")
    elif cpu is None:
        cautions.append("CPU utilisation could not be sampled; repeatability checks still apply")
    if conditions.get("low_power_mode") is True:
        blockers.append("low power mode is enabled and can cap performance")
    if conditions.get("thermal_warning") is True:
        blockers.append("macOS reports a thermal warning")
    if conditions.get("power_source") == "battery":
        cautions.append("machine is on battery; connect power for comparable throughput")
    if host is not None:
        if not host.get("chip"):
            blockers.append("Apple chip identity could not be read")
        if not host.get("gpu_cores"):
            blockers.append("GPU core count could not be read; scaling this result would be unsafe")
    return {
        "accepted": not blockers,
        "stage": "preflight",
        "blockers": blockers,
        "cautions": cautions,
        "thresholds": {
            "minimum_free_memory_gb": MIN_FREE_MEMORY_GB,
            "maximum_active_swap_mb": MAX_ACTIVE_SWAP_MB,
            "maximum_cpu_busy_percent": MAX_BUSY_PERCENT,
            "maximum_relative_spread": MAX_RELATIVE_SPREAD,
        },
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
                    "--disable-pip-version-check", "mlx==0.32.2", "psutil"],
                   check=True)

    environment = dict(os.environ, **{VENV_MARKER: str(workspace)})
    try:
        completed = subprocess.run(
            [str(python), os.path.abspath(__file__), *sys.argv[1:]],
            env=environment, check=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        print(f"\nRemoved the temporary environment. Nothing left behind.")
    sys.exit(completed.returncode)


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


def report(host: dict, conditions: dict, summary: dict,
           validation: dict) -> str:
    lines = [
        "=" * 66,
        "  APPLE SILICON GPU MEASUREMENT — copy this block",
        "=" * 66,
        f"  Result          {'ACCEPTED' if validation['accepted'] else 'REJECTED'}",
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
    if not validation["accepted"]:
        lines += [
            "  REJECTED: this run is saved for diagnosis but must not be used",
            "  as calibration data.",
            *(f"  - {reason}" for reason in validation["blockers"]),
            "=" * 66,
        ]
    return "\n".join(lines)


def validate_summary(summary: dict, preflight: dict) -> dict:
    """Reject a completed run if repeats disagree beyond the project rule."""
    blockers = list(preflight["blockers"])
    spreads = {key: value for key, value in summary.items()
               if key.endswith("_spread")}
    for key, value in spreads.items():
        if value > MAX_RELATIVE_SPREAD:
            blockers.append(
                f"{key} was {value:.1%}; maximum allowed spread is "
                f"{MAX_RELATIVE_SPREAD:.0%}")
    return {
        **preflight,
        "accepted": not blockers,
        "stage": "postflight",
        "blockers": blockers,
        "observed_relative_spread": spreads,
        "eligible_metrics": {
            "throughput": not blockers,
            "energy": False,
        },
    }


def _destination(output: str | None, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"measurement-{label}-{stamp}.json"
    if output:
        requested = Path(output).expanduser()
        return requested if requested.suffix.lower() == ".json" else requested / filename
    for directory in (Path.home() / "Desktop", Path.home(), Path.cwd()):
        if directory.exists():
            return directory / filename
    return Path.cwd() / filename


def _save(payload: dict, output: str | None, label: str) -> Path:
    destination = _destination(output, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return destination


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure an Apple-silicon GPU with validity checks.")
    parser.add_argument(
        "--check-only", action="store_true",
        help="check whether the machine is ready without downloading MLX")
    parser.add_argument(
        "--output", metavar="PATH",
        help="directory or exact .json path for the result (default: Desktop)")
    return parser.parse_args(argv)


def baseline_measurements(summary: dict) -> list[dict]:
    """Translate the headline into the existing append-only baseline contract."""
    size = summary.get("gemm_size")
    rows = []
    for dtype, key in (("float16", "gemm_fp16_gflops"),
                       ("float32", "gemm_fp32_gflops")):
        if summary.get(key) is not None:
            rows.append({
                "name": "gemm", "dtype": dtype, "size": size,
                "rate": summary[key], "unit": "GFLOP/s",
            })
    if summary.get("memory_bandwidth_gbs") is not None:
        rows.append({
            "name": "memory_bandwidth", "dtype": "float32",
            "size": BANDWIDTH_ELEMENTS,
            "rate": summary["memory_bandwidth_gbs"], "unit": "GB/s read",
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        print("REJECTED: this collector only measures Apple-silicon Macs.")
        return 2
    version = platform.mac_ver()[0]
    try:
        macos_major = int(version.split(".", 1)[0])
    except (TypeError, ValueError):
        macos_major = 0
    if macos_major and macos_major < 14:
        print("REJECTED: macOS 14 or newer is required by MLX.")
        return 2

    host = describe_host()
    conditions = host_conditions()
    preflight = validate_conditions(conditions, host)
    label = re.sub(r"[^a-z0-9]+", "-", (host.get("chip") or "apple").lower()).strip("-")
    if not preflight["accepted"]:
        payload = {
            "schema": SCHEMA, "collector_version": COLLECTOR_VERSION,
            "measurement_id": str(uuid.uuid4()),
            "status": "rejected", "platform": "apple-mlx",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "host": host, "conditions": conditions, "validation": preflight,
            "summary": {}, "runs": [],
        }
        payload["validation"]["eligible_metrics"] = {
            "throughput": False, "energy": False}
        destination = _save(payload, args.output, label)
        print("REJECTED — the machine is not quiet enough for valid data:")
        for reason in preflight["blockers"]:
            print(f"  - {reason}")
        print(f"\nDiagnostic result saved to: {destination}")
        return 2
    if args.check_only:
        print("READY — the Mac passed the preflight checks.")
        for caution in preflight["cautions"]:
            print(f"  Note: {caution}")
        return 0

    bootstrap_and_reexec()
    try:
        import mlx.core as mx
    except ImportError:
        print("MLX is not available, so no measurement can be made.")
        return 1

    print(f"Measuring {host.get('chip') or 'this Mac'} — "
          f"{REPEATS} repeats, a few minutes.\n")

    repeats = []
    for index in range(REPEATS):
        print(f"  run {index + 1} of {REPEATS}...", flush=True)
        repeats.append(run_once(mx))

    summary = headline(repeats)
    validation = validate_summary(summary, preflight)
    print("\n" + report(host, conditions, summary, validation))
    try:
        mlx_version = importlib.metadata.version("mlx")
    except importlib.metadata.PackageNotFoundError:
        mlx_version = "unknown"

    payload = {
        "schema": SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "measurement_id": str(uuid.uuid4()),
        "status": "accepted" if validation["accepted"] else "rejected",
        "platform": "apple-mlx",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "device": host.get("chip") or "unknown Apple GPU",
        "stack": f"mlx-{mlx_version}_macos-{platform.mac_ver()[0]}",
        "observed_at": conditions["captured_at"],
        "context": conditions,
        "measurements": baseline_measurements(summary),
        "host": host,
        "conditions": conditions,
        "validation": validation,
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
    destination = _save(payload, args.output, label)
    print(f"\nFull result saved to: {destination}")
    if validation["accepted"]:
        print("This file is eligible for calibration review.")
        return 0
    print("Do not ingest this file. Close applications, cool the Mac, and run again.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
