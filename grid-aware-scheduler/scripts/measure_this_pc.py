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
Power and completed work are sampled during the same sustained FP16 operation;
dividing a peak from one workload by watts from another would not be a valid
efficiency measurement.

**The laziness trap, in its CUDA form.** MLX queues work and returns
immediately, which once made a matmul look thirty times faster than the
hardware's theoretical peak. CUDA does exactly the same thing through streams.
Every timed region here calls `torch.cuda.synchronize()` inside it for the
same reason. Three whole-benchmark repeats and a strict spread gate protect
against queue-timing mistakes and background contention.

The zero-setup PowerShell launcher is the preferred entry point. It supplies
its own temporary Python runtime and selects a PyTorch build compatible with
the installed NVIDIA driver:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\\measure_dell.ps1

PyTorch is a large download — around 2.5 GB, because it bundles its own CUDA
runtime. That is the deliberate choice: it means the script does not care
which CUDA toolkit the machine has, which is the difference between working
and not working on a computer you did not configure.
"""
from __future__ import annotations

import json
import argparse
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
import uuid
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
SCHEMA = "ai-energy-hardware-measurement-v1"
COLLECTOR_VERSION = "2.0"
MAX_RELATIVE_SPREAD = 0.05
MIN_FREE_MEMORY_GB = 3.0
MAX_ACTIVE_SWAP_GB = 1.0
MAX_BUSY_PERCENT = 25.0
MAX_GPU_TEMPERATURE_C = 80.0
#: Power is sampled while the device is under sustained load. An idle reading
#: is meaningless for joules-per-token.
POWER_SAMPLE_INTERVAL = 0.1
POWER_LOAD_SECONDS = 5.0

VENV_MARKER = "AI_ENERGY_BENCH_VENV"
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"


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
        "cpu": describe_cpu(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


def describe_cpu() -> dict:
    """Host CPU identity and core count.

    Recorded rather than measured, and it is not idle detail. The board power
    figure this script reports covers the **card only**. A facility pays for
    the whole node, and on a large training host the CPU can draw a few
    hundred watts that `nvidia-smi` never sees — so joules per token derived
    from board power alone is an understatement, and knowing what host it sat
    in is what lets a reader judge by how much.

    It also bounds the measurement's validity. A GPU benchmark on a machine
    whose CPU is saturated is measuring contention, which is why
    `hardware/preflight.py` refuses a run above 25% busy.

    Deliberately identity only. CPU package power needs elevated access on
    Windows, and this script's whole premise is that it runs without any.
    """
    name = platform.processor() or ""
    if platform.system() == "Windows":
        name = os.environ.get("PROCESSOR_IDENTIFIER", name)
    elif platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    return {
        "name": name.strip() or "unknown",
        "logical_cores": os.cpu_count(),
        "architecture": platform.machine(),
        "power_scope_note": "CPU draw is NOT included in the board power "
                            "figure reported below, and is not measured here.",
    }


def _number(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


def host_conditions() -> dict:
    """Capture contention and headroom before downloading PyTorch."""
    conditions = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": None,
        "memory_available_gb": None,
        "swap_used_gb": None,
        "storage_free_gb": None,
        "gpu_percent": None,
        "gpu_memory_used_mib": None,
        "gpu_memory_total_mib": None,
        "gpu_temperature_c": None,
        "compute_process_count": 0,
    }
    try:
        import psutil
        conditions.update({
            "cpu_percent": psutil.cpu_percent(interval=1.0),
            "memory_available_gb": round(psutil.virtual_memory().available / 1e9, 2),
            "swap_used_gb": round(psutil.swap_memory().used / 1e9, 2),
            "storage_free_gb": round(shutil.disk_usage(Path.home()).free / 1e9, 2),
        })
    except ImportError:
        pass

    query = _run([
        "nvidia-smi",
        "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ])
    if query.strip():
        fields = [field.strip() for field in query.splitlines()[0].split(",")]
        if len(fields) >= 4:
            conditions.update({
                "gpu_percent": _number(fields[0]),
                "gpu_memory_used_mib": _number(fields[1]),
                "gpu_memory_total_mib": _number(fields[2]),
                "gpu_temperature_c": _number(fields[3]),
            })
    processes = _run([
        "nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    process_rows = [
        line.strip() for line in processes.splitlines()
        if line.strip() and "no running processes" not in line.lower()
    ]
    conditions["compute_process_count"] = len(process_rows)
    return conditions


def validate_conditions(conditions: dict) -> dict:
    blockers: list[str] = []
    cautions: list[str] = []
    memory = conditions.get("memory_available_gb")
    if memory is None:
        blockers.append("available system memory could not be read")
    elif memory < MIN_FREE_MEMORY_GB:
        blockers.append(
            f"only {memory:.2f} GB system memory is free; {MIN_FREE_MEMORY_GB:.1f} GB is required")
    swap = conditions.get("swap_used_gb")
    if swap is not None and swap > MAX_ACTIVE_SWAP_GB:
        blockers.append(
            f"{swap:.2f} GB of swap is active; restart or close applications first")
    cpu = conditions.get("cpu_percent")
    if cpu is not None and cpu > MAX_BUSY_PERCENT:
        blockers.append(f"CPU is already {cpu:.0f}% busy; close other applications first")
    gpu = conditions.get("gpu_percent")
    if gpu is None:
        blockers.append("NVIDIA GPU utilisation could not be read")
    elif gpu > MAX_BUSY_PERCENT:
        blockers.append(f"GPU is already {gpu:.0f}% busy; close GPU applications first")
    temperature = conditions.get("gpu_temperature_c")
    if temperature is not None and temperature > MAX_GPU_TEMPERATURE_C:
        blockers.append(
            f"GPU is already {temperature:.0f} C; let it cool below {MAX_GPU_TEMPERATURE_C:.0f} C")
    if conditions.get("compute_process_count"):
        blockers.append("another CUDA compute process is using the GPU")
    used = conditions.get("gpu_memory_used_mib")
    total = conditions.get("gpu_memory_total_mib")
    if used is not None and total and used / total > 0.10:
        cautions.append(f"{used:.0f} MiB of {total:.0f} MiB GPU memory is already allocated")
    return {
        "accepted": not blockers,
        "stage": "preflight",
        "blockers": blockers,
        "cautions": cautions,
        "thresholds": {
            "minimum_free_memory_gb": MIN_FREE_MEMORY_GB,
            "maximum_active_swap_gb": MAX_ACTIVE_SWAP_GB,
            "maximum_cpu_busy_percent": MAX_BUSY_PERCENT,
            "maximum_gpu_busy_percent": MAX_BUSY_PERCENT,
            "maximum_gpu_temperature_c": MAX_GPU_TEMPERATURE_C,
            "maximum_relative_spread": MAX_RELATIVE_SPREAD,
        },
    }


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
                    "--extra-index-url", TORCH_INDEX, "torch", "psutil"],
                   check=True)

    environment = dict(os.environ, **{VENV_MARKER: str(workspace)})
    try:
        completed = subprocess.run(
            [str(python), os.path.abspath(__file__), *sys.argv[1:]],
            env=environment, check=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        print("\nRemoved the temporary environment. Nothing left behind.")
    sys.exit(completed.returncode)


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


def measure_matched_power(torch) -> dict:
    """Measure board watts and completed FP16 work in one sustained interval."""
    size = max(GEMM_SIZES)
    device = torch.device("cuda")
    left = torch.randn((size, size), device=device, dtype=torch.float16)
    right = torch.randn((size, size), device=device, dtype=torch.float16)

    # Bring clocks up before opening the sampling window. A short cold burst
    # understates both performance and power on cards with dynamic clocks.
    warm_until = time.perf_counter() + 1.0
    while time.perf_counter() < warm_until:
        for _ in range(10):
            product = left @ right
        torch.cuda.synchronize()

    operations = 0
    started = time.perf_counter()
    with PowerSampler() as sampler:
        while time.perf_counter() - started < POWER_LOAD_SECONDS:
            for _ in range(10):
                product = left @ right
            torch.cuda.synchronize()
            operations += 10
    elapsed = time.perf_counter() - started
    result = sampler.result()
    result.update({
        "workload": "dense_gemm",
        "dtype": "float16",
        "gemm_size": size,
        "matched_elapsed_seconds": elapsed,
        "matched_operations": operations,
        "matched_gflops": (operations * 2.0 * size ** 3 / elapsed / 1e9
                            if elapsed > 0 else None),
    })
    del product, left, right
    torch.cuda.empty_cache()
    return result


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
    if power.get("available") and power.get("matched_gflops"):
        watts = power["median_watts"]
        if watts > 0:
            summary["gflops_per_watt"] = power["matched_gflops"] / watts
            summary["power_scope"] = "board"
            summary["power_workload"] = "matched sustained fp16 dense_gemm"
    return summary


def report(host: dict, units: dict, summary: dict, power: dict,
           validation: dict) -> str:
    lines = [
        "=" * 68,
        "  NVIDIA GPU MEASUREMENT — copy this block",
        "=" * 68,
        f"  Result          {'ACCEPTED' if validation['accepted'] else 'REJECTED'}",
        f"  Card            {units.get('name', '?')}",
        f"  SMs             {units.get('streaming_multiprocessors', '?')}"
        "   <- the divisor for anything scaled from this",
        f"  Memory          {units.get('total_memory_gb', 0):.1f} GB",
        f"  Compute cap.    {units.get('compute_capability', '?')}",
        f"  Driver          {host['cards'][0].get('driver', '?') if host.get('cards') else '?'}",
        f"  Host CPU        {host.get('cpu', {}).get('name', '?')[:44]}",
        f"  CPU cores       {host.get('cpu', {}).get('logical_cores', '?')} logical",
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
            "  Power pairing   watts and work measured in the same sustained",
            "                  FP16 matrix operation",
            "  Scope           board only — excludes the host CPU, RAM, fans",
            "                  and PSU losses. The facility pays for all of",
            "                  those, so treat this as a floor on node draw,",
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
    if not validation["accepted"]:
        lines += [
            "  REJECTED: this run is saved for diagnosis but must not be used",
            "  as calibration data.",
            *(f"  - {reason}" for reason in validation["blockers"]),
            "=" * 68,
        ]
    return "\n".join(lines)


def largest_note(summary: dict) -> str:
    size = summary.get("gemm_size")
    return f"  Measured at {size}x{size} square,"


def validate_summary(summary: dict, preflight: dict, power: dict) -> dict:
    blockers = list(preflight["blockers"])
    cautions = list(preflight["cautions"])
    spreads = {key: value for key, value in summary.items()
               if key.endswith("_spread")}
    for key, value in spreads.items():
        if value > MAX_RELATIVE_SPREAD:
            blockers.append(
                f"{key} was {value:.1%}; maximum allowed spread is "
                f"{MAX_RELATIVE_SPREAD:.0%}")
    if not power.get("available"):
        cautions.append("board power was unavailable; throughput remains usable but energy does not")
    elif power.get("samples", 0) < 5:
        cautions.append("fewer than five board-power samples were captured; energy is not eligible")
    throughput_eligible = not blockers
    energy_eligible = (throughput_eligible and power.get("available", False)
                       and power.get("samples", 0) >= 5)
    return {
        **preflight,
        "accepted": not blockers,
        "stage": "postflight",
        "blockers": blockers,
        "cautions": cautions,
        "observed_relative_spread": spreads,
        "eligible_metrics": {
            "throughput": throughput_eligible,
            "energy": energy_eligible,
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
        description="Measure an NVIDIA GPU with validity and power checks.")
    parser.add_argument(
        "--check-only", action="store_true",
        help="check whether the machine is ready without downloading PyTorch")
    parser.add_argument(
        "--output", metavar="PATH",
        help="directory or exact .json path for the result (default: Desktop)")
    return parser.parse_args(argv)


def baseline_measurements(summary: dict, power: dict) -> list[dict]:
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
    if power.get("available"):
        rows.append({
            "name": "board_power", "dtype": "float16",
            "size": power.get("gemm_size"),
            "rate": power["median_watts"], "unit": "W",
            "scope": "board", "matched_workload": power.get("workload"),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if not shutil.which("nvidia-smi"):
        print("REJECTED: nvidia-smi was not found. This collector requires an NVIDIA GPU driver.")
        return 2

    # A direct invocation keeps its older self-bootstrap path. The zero-setup
    # launcher sets the marker and reaches this point with psutil already
    # available, allowing preflight before the large PyTorch download.
    if not os.environ.get(VENV_MARKER):
        bootstrap_and_reexec()

    host = describe_host()
    if not host["cards"]:
        print("REJECTED: nvidia-smi ran but reported no NVIDIA cards.")
        return 2
    conditions = host_conditions()
    preflight = validate_conditions(conditions)
    label = re.sub(r"[^a-z0-9]+", "-", host["cards"][0]["name"].lower()).strip("-")
    if not preflight["accepted"]:
        payload = {
            "schema": SCHEMA, "collector_version": COLLECTOR_VERSION,
            "measurement_id": str(uuid.uuid4()),
            "status": "rejected", "platform": "nvidia-cuda",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "host": host, "conditions": conditions, "validation": preflight,
            "summary": {}, "power": {"available": False}, "runs": [],
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
        print("READY — the NVIDIA machine passed the preflight checks.")
        for caution in preflight["cautions"]:
            print(f"  Note: {caution}")
        return 0

    try:
        import torch
    except ImportError:
        print("PyTorch is not available, so no measurement can be made.")
        return 1
    if not torch.cuda.is_available():
        print("PyTorch cannot see a CUDA device. If this machine has an "
              "NVIDIA GPU, its driver may be too old for the bundled runtime.")
        return 1

    units = device_units(torch)
    print(f"Measuring {units['name']} "
          f"({units['streaming_multiprocessors']} SMs) — "
          f"{REPEATS} repeats.\n")

    repeats = []
    for index in range(REPEATS):
        print(f"  run {index + 1} of {REPEATS}...", flush=True)
        repeats.append(run_once(torch))
    print("  matched power run...", flush=True)
    power = measure_matched_power(torch)

    summary = headline(repeats, power)
    validation = validate_summary(summary, preflight, power)
    print("\n" + report(host, units, summary, power, validation))

    payload = {
        "schema": SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "measurement_id": str(uuid.uuid4()),
        "status": "accepted" if validation["accepted"] else "rejected",
        "platform": "nvidia-cuda",
        "host": host, "device": units, "summary": summary,
        "conditions": conditions, "validation": validation,
        "power": power, "runs": repeats,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "device": units["name"],
        "stack": (f"torch-{torch.__version__}_cuda-{torch.version.cuda or 'none'}_"
                  f"driver-{host['cards'][0].get('driver', 'unknown')}"),
        "observed_at": conditions["captured_at"],
        "context": conditions,
        "measurements": baseline_measurements(summary, power),
        "methodology": {
            "gemm_sizes": list(GEMM_SIZES), "dtypes": list(DTYPES),
            "bandwidth_elements": BANDWIDTH_ELEMENTS,
            "warmup_iterations": WARMUP_ITERATIONS,
            "measured_iterations": MEASURED_ITERATIONS,
            "repeats": REPEATS,
            "power_load_seconds": POWER_LOAD_SECONDS,
            "power_pairing": "work and nvidia-smi board power captured during "
                             "the same sustained fp16 dense GEMM interval",
            "sibling": "measure_this_mac.py — same method, never comparable "
                       "numbers: an SM is not an Apple GPU core.",
        },
    }
    destination = _save(payload, args.output, label)
    print(f"\nFull result saved to: {destination}")
    if validation["accepted"]:
        print("This file is eligible for calibration review.")
        return 0
    print("Do not ingest this file. Close applications, cool the GPU, and run again.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
