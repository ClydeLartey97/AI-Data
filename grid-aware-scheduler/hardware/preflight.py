"""Whether this host can produce a valid measurement right now.

A benchmark that runs while the machine is swapping, thermally throttled or
in low-power mode still returns a number. That is the danger: the number
looks like the others and is not comparable to them. This module refuses the
run instead, and records the conditions of the runs it does allow, because a
measurement whose starting conditions were not captured is not reproducible.

Nothing here needs privilege. Blockers make a run invalid; cautions are
recorded alongside the result and left to the caller's judgement.
"""
from __future__ import annotations

import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from hardware.telemetry import TelemetryCollector

#: Headroom below which a model's own allocation would push the host into
#: swap, which measures the SSD rather than the accelerator.
MIN_FREE_MEMORY_GB = 3.0
#: Swap already in active use means the working set does not fit today.
MAX_ACTIVE_SWAP_GB = 1.0
#: Another process competing for the accelerator invalidates the comparison.
MAX_BUSY_PERCENT = 25.0
MIN_FREE_STORAGE_GB = 5.0


@dataclass
class Preflight:
    valid: bool
    blockers: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise RuntimeError(
                "host cannot produce a valid measurement: " + "; ".join(self.blockers))


def _low_power_mode(runner: Callable) -> bool | None:
    try:
        result = runner(["pmset", "-g"], capture_output=True, text=True,
                        check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if "lowpowermode" in line:
            return line.split()[-1].strip() == "1"
    return None


def _thermal_warning(runner: Callable) -> bool | None:
    """`pmset -g therm` stays silent until the system has actually throttled."""
    try:
        result = runner(["pmset", "-g", "therm"], capture_output=True, text=True,
                        check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.lower()
    if "no thermal warning level has been recorded" in text:
        return False
    return "thermal" in text and "warning" in text


def check(*, collector: TelemetryCollector | None = None,
          runner: Callable | None = None,
          require_discharging: bool = False,
          min_free_memory_gb: float = MIN_FREE_MEMORY_GB) -> Preflight:
    """Decide whether to measure, and capture the conditions either way."""
    collector = collector or TelemetryCollector()
    runner = runner or subprocess.run
    blockers: list[str] = []
    cautions: list[str] = []

    snapshot = collector.snapshot()
    device = snapshot["devices"][0]
    live, static = device.get("live", {}), device.get("static", {})
    battery = collector.battery()

    free_memory = live.get("memory_available_gb")
    if free_memory is None:
        blockers.append("available memory could not be read")
    elif free_memory < min_free_memory_gb:
        blockers.append(
            f"only {free_memory:.2f} GB memory free; {min_free_memory_gb:.1f} GB "
            "required so the run does not measure swap")

    swap = live.get("swap_used_gb")
    if swap is not None and swap > MAX_ACTIVE_SWAP_GB:
        blockers.append(
            f"{swap:.2f} GB of swap already in use; the working set does not "
            "currently fit in memory")

    for label, key in (("GPU", "gpu_percent"), ("CPU", "cpu_percent")):
        busy = live.get(key)
        if busy is not None and busy > MAX_BUSY_PERCENT:
            blockers.append(
                f"{label} already {busy:.0f}% busy; another process would "
                "contend for the device under test")

    storage = live.get("storage_free_gb")
    if storage is not None and storage < MIN_FREE_STORAGE_GB:
        cautions.append(f"only {storage:.0f} GB storage free")

    if _low_power_mode(runner):
        blockers.append("low power mode is enabled, which caps performance")
    if _thermal_warning(runner):
        blockers.append("the system has already recorded a thermal warning")

    if battery.get("available"):
        on_ac = battery.get("on_ac_power")
        if require_discharging and not battery.get("energy_integration_possible"):
            blockers.append(
                "device-input energy needs the host discharging on battery; "
                f"it is currently {'on AC power' if on_ac else 'charging'}")
        elif on_ac is False and not require_discharging:
            cautions.append(
                "running on battery, where macOS may cap performance; "
                "throughput is not comparable with runs made on AC power")

    context = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "device": device.get("name"),
        "platform": platform.platform(terse=True),
        "static": static,
        "memory_available_gb": free_memory,
        "swap_used_gb": swap,
        "storage_free_gb": storage,
        "gpu_percent_at_start": live.get("gpu_percent"),
        "cpu_percent_at_start": live.get("cpu_percent"),
        "power_source": ("ac" if battery.get("on_ac_power")
                         else "battery" if battery.get("available") else "unknown"),
        "battery": battery,
        "low_power_mode": _low_power_mode(runner),
    }
    return Preflight(valid=not blockers, blockers=blockers,
                     cautions=cautions, context=context)
