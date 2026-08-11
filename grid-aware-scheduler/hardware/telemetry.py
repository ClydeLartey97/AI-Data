"""Live host telemetry — what capacity is free right now.

This is the feasibility input the planner already models. A device's installed
memory decides what could ever fit; its *available* memory decides what fits
now, and the same holds for storage headroom and how busy the accelerator
already is.

Provenance discipline is unchanged from the rest of `hardware/`. Every value
here is read from the operating system, so it is MEASURED — but it is
measurement of *occupancy*, never of performance. A GPU reporting 30% busy
says nothing about its throughput or its power curve; only repeated
calibration runs (`hardware/calibration.py`) can promote those.

Collection is read-only and unprivileged: `psutil` for memory, storage and
CPU, `ioreg` for Apple GPU occupancy, `nvidia-smi` for NVIDIA. Nothing here
needs sudo, and nothing here retains a hostname, serial number, UUID or user
path. A source that is unavailable is reported as UNAVAILABLE rather than
guessed.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

try:  # psutil is an optional profiling dependency (requirements-apple.txt)
    import psutil
except ImportError:  # pragma: no cover - exercised by the unavailable path
    psutil = None

GIB = float(1 << 30)
MEASURED = "MEASURED"
UNAVAILABLE = "UNAVAILABLE"

#: Apple GPU occupancy keys exposed by IOAccelerator, read-only via ioreg.
_IOREG_KEYS = {
    "Device Utilization %": "gpu_percent",
    "Renderer Utilization %": "gpu_renderer_percent",
    "Tiler Utilization %": "gpu_tiler_percent",
    "In use system memory": "gpu_memory_in_use_bytes",
    "Alloc system memory": "gpu_memory_allocated_bytes",
}


@dataclass(frozen=True)
class HostIdentity:
    """Slow-changing facts, collected once and reused across ticks."""

    name: str
    kind: str
    cpu_cores: int | None = None
    gpu_cores: int | None = None
    memory_total_gb: float | None = None
    storage_total_gb: float | None = None
    source: str = ""
    notes: tuple[str, ...] = ()


@dataclass
class TelemetryCollector:
    """Reads live occupancy. Injectable so tests never touch real hardware."""

    runner: Callable = subprocess.run
    platform_name: str = field(default_factory=platform.system)
    _identity: HostIdentity | None = None

    # -- shell -------------------------------------------------------------

    def _run(self, args: list[str], timeout: float = 5.0) -> str:
        result = self.runner(args, capture_output=True, text=True,
                             check=True, timeout=timeout)
        return result.stdout

    # -- static identity ---------------------------------------------------

    def identity(self) -> HostIdentity:
        if self._identity is None:
            self._identity = self._build_identity()
        return self._identity

    def _build_identity(self) -> HostIdentity:
        cores = psutil.cpu_count(logical=True) if psutil else None
        memory = psutil.virtual_memory().total / GIB if psutil else None
        try:
            storage = shutil.disk_usage("/").total / GIB
        except OSError:
            storage = None
        name, kind, gpu_cores, source = "Local host", "host", None, "platform"
        if self.platform_name == "Darwin":
            try:
                payload = json.loads(self._run([
                    "system_profiler", "SPHardwareDataType",
                    "SPDisplaysDataType", "-json",
                ]))
                hardware = (payload.get("SPHardwareDataType") or [{}])[0]
                chip = str(hardware.get("chip_type") or "").strip()
                displays = payload.get("SPDisplaysDataType") or []
                gpu = next((entry for entry in displays
                            if entry.get("sppci_device_type") == "spdisplays_gpu"), {})
                gpu_cores = _int(str(gpu.get("sppci_cores") or ""))
                if chip:
                    name, kind, source = chip, "SoC", "system_profiler"
            except (OSError, subprocess.SubprocessError, ValueError,
                    json.JSONDecodeError):
                pass
        return HostIdentity(
            name=name, kind=kind, cpu_cores=cores, gpu_cores=gpu_cores,
            memory_total_gb=_round(memory), storage_total_gb=_round(storage),
            source=source,
            notes=("Occupancy only; throughput and power remain uncalibrated",),
        )

    # -- live values -------------------------------------------------------

    def _apple_gpu(self) -> tuple[dict, list[str]]:
        try:
            output = self._run(["ioreg", "-r", "-d", "1", "-w", "0",
                                "-c", "IOAccelerator"], timeout=3.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return {}, [f"Apple GPU occupancy unavailable: {exc}"]
        live: dict[str, float] = {}
        for key, name in _IOREG_KEYS.items():
            match = re.search(rf'"{re.escape(key)}"\s*=\s*(\d+)', output)
            if match:
                live[name] = float(match.group(1))
        if not live:
            return {}, ["IOAccelerator reported no occupancy statistics"]
        for source, target in (("gpu_memory_in_use_bytes", "gpu_memory_in_use_gb"),
                               ("gpu_memory_allocated_bytes", "gpu_memory_allocated_gb")):
            if source in live:
                live[target] = _round(live.pop(source) / GIB)
        return live, []

    def _nvidia(self) -> tuple[list[dict], list[str]]:
        if not shutil.which("nvidia-smi"):
            return [], []
        query = ("index,name,utilization.gpu,memory.used,memory.total,"
                 "power.draw,temperature.gpu")
        try:
            output = self._run(["nvidia-smi", f"--query-gpu={query}",
                                "--format=csv,noheader,nounits"])
        except (OSError, subprocess.SubprocessError) as exc:
            return [], [f"NVIDIA telemetry unavailable: {exc}"]
        devices = []
        for line in output.splitlines():
            fields = [value.strip() for value in line.split(",")]
            if len(fields) < 7:
                continue
            index, name, util, used, total, power, temperature = fields[:7]
            used_gb, total_gb = _float(used), _float(total)
            devices.append({
                "id": f"nvidia-{index}",
                "name": name,
                "kind": "GPU",
                "static": {
                    "memory_total_gb": _round(total_gb / 1024) if total_gb else None,
                },
                "live": _drop_none({
                    "gpu_percent": _float(util),
                    "memory_used_gb": _round(used_gb / 1024) if used_gb else None,
                    "memory_available_gb": (
                        _round((total_gb - used_gb) / 1024)
                        if None not in (total_gb, used_gb) else None),
                    "power_watts": _float(power),
                    "temperature_c": _float(temperature),
                }),
                "provenance": MEASURED,
                "notes": ["Live board power is one observation, not a curve"],
            })
        return devices, []

    def snapshot(self) -> dict:
        """One complete reading. Safe to call on a timer."""
        warnings: list[str] = []
        identity = self.identity()
        live: dict[str, float] = {}

        if psutil is not None:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            live.update({
                "memory_available_gb": _round(memory.available / GIB),
                "memory_used_gb": _round((memory.total - memory.available) / GIB),
                "memory_used_percent": _round(memory.percent, 1),
                "swap_used_gb": _round(swap.used / GIB),
                # interval=None compares against the previous call, so a timed
                # stream reports real deltas without blocking the request.
                "cpu_percent": _round(psutil.cpu_percent(interval=None), 1),
            })
        else:
            warnings.append("psutil is not installed; memory and CPU are unavailable")

        try:
            usage = shutil.disk_usage("/")
            live["storage_free_gb"] = _round(usage.free / GIB)
            live["storage_used_percent"] = _round(
                100.0 * (usage.total - usage.free) / usage.total, 1)
        except (OSError, ZeroDivisionError):
            warnings.append("Storage headroom unavailable")

        if self.platform_name == "Darwin":
            gpu_live, gpu_warnings = self._apple_gpu()
            live.update(gpu_live)
            warnings.extend(gpu_warnings)

        devices = [{
            "id": "local-host",
            "name": identity.name,
            "kind": identity.kind,
            "static": _drop_none({
                "cpu_cores": identity.cpu_cores,
                "gpu_cores": identity.gpu_cores,
                "memory_total_gb": identity.memory_total_gb,
                "storage_total_gb": identity.storage_total_gb,
            }),
            "live": live,
            "provenance": MEASURED if live else UNAVAILABLE,
            "notes": list(identity.notes),
        }]
        nvidia_devices, nvidia_warnings = self._nvidia()
        devices.extend(nvidia_devices)
        warnings.extend(nvidia_warnings)

        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "devices": devices,
            "warnings": warnings,
            "measurement_scope": "occupancy",
        }


def _drop_none(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def _round(value: float | None, places: int = 2) -> float | None:
    return round(value, places) if isinstance(value, (int, float)) else None


def _float(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # reject NaN


def _int(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None
