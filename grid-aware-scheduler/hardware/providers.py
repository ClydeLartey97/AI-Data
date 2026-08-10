"""Hardware discovery behind an explicit provider boundary.

Detection and performance estimation are different evidence. A machine can
report that an accelerator exists and how much memory it has without proving
its sustained throughput or power curve. ``DetectedDevice`` therefore carries
provenance per field and never promotes a whole catalogue row to MEASURED.

The local provider uses read-only operating-system commands. It deliberately
does not retain serial numbers, UUIDs or other host identifiers returned by
those commands.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hardware import catalog
from hardware.base import Fleet, Group, Provenance


@dataclass(frozen=True)
class DetectedDevice:
    name: str
    count: int
    catalog_key: str | None
    memory_gb: float | None
    live_power_watts: float | None = None
    identity_provenance: str = Provenance.MEASURED.value
    memory_provenance: str = Provenance.MEASURED.value
    power_provenance: str = "UNAVAILABLE"
    performance_provenance: str = Provenance.ESTIMATED.value
    source: str = ""
    notes: tuple[str, ...] = ()


@dataclass
class DetectionResult:
    provider: str
    devices: list[DetectedDevice]
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = field(default_factory=list)

    def public_dict(self) -> dict:
        """JSON-safe result containing no host identifier."""
        return {
            "provider": self.provider,
            "observed_at": self.observed_at.isoformat(),
            "devices": [asdict(device) for device in self.devices],
            "warnings": self.warnings,
        }

    def fleet(self) -> Fleet:
        """Build a fleet while preserving catalogue performance provenance."""
        groups: list[Group] = []
        for evidence in self.devices:
            if not evidence.catalog_key or evidence.catalog_key not in catalog.CATALOG:
                continue
            device = catalog.CATALOG[evidence.catalog_key]
            if evidence.memory_gb is not None:
                device = replace(device, memory_gb=evidence.memory_gb)
            groups.append(Group(device, evidence.count))
        if not groups:
            raise ValueError("no detected device maps to the hardware catalogue")
        provenance = (
            Provenance.SIMULATED if self.provider == "simulated"
            else Provenance.ESTIMATED
        )
        return Fleet(groups, provenance=provenance, label="Detected local fleet")


class HardwareProvider(ABC):
    @abstractmethod
    def detect(self) -> DetectionResult:
        raise NotImplementedError


class LocalDetector(HardwareProvider):
    def __init__(self, *, runner: Callable | None = None,
                 platform_name: str | None = None) -> None:
        self._runner = runner or subprocess.run
        self._platform = platform_name or platform.system()

    def _run(self, args: list[str]) -> str:
        result = self._runner(
            args, capture_output=True, text=True, check=True, timeout=15
        )
        return result.stdout

    def detect(self) -> DetectionResult:
        devices: list[DetectedDevice] = []
        warnings: list[str] = []
        if self._platform == "Darwin":
            try:
                devices.extend(self._apple())
            except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(f"Apple hardware detection failed: {exc}")

        if shutil.which("nvidia-smi"):
            try:
                devices.extend(self._nvidia())
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                warnings.append(f"NVIDIA hardware detection failed: {exc}")

        if not devices:
            warnings.append("No supported accelerator was detected")
        return DetectionResult("local", devices, warnings=warnings)

    def _apple(self) -> list[DetectedDevice]:
        payload = json.loads(self._run([
            "system_profiler", "SPHardwareDataType", "SPDisplaysDataType", "-json"
        ]))
        hardware = (payload.get("SPHardwareDataType") or [{}])[0]
        displays = payload.get("SPDisplaysDataType") or []
        chip = str(hardware.get("chip_type") or "").strip()
        if not chip:
            raise ValueError("system_profiler did not return an Apple chip")
        memory = _number(str(hardware.get("physical_memory") or ""))
        gpu = next((entry for entry in displays if entry.get("sppci_device_type") == "spdisplays_gpu"), {})
        cores = _number(str(gpu.get("sppci_cores") or ""))
        key = _apple_catalog_key(chip)
        notes = tuple(filter(None, (
            f"{cores:.0f} GPU cores reported" if cores is not None else "",
            "Throughput and power remain catalogue estimates",
        )))
        performance = (
            catalog.CATALOG[key].provenance.value if key in catalog.CATALOG
            else "UNAVAILABLE"
        )
        return [DetectedDevice(
            name=chip,
            count=1,
            catalog_key=key,
            memory_gb=memory,
            performance_provenance=performance,
            source="system_profiler",
            notes=notes,
        )]

    def _nvidia(self) -> list[DetectedDevice]:
        output = self._run([
            "nvidia-smi",
            "--query-gpu=name,memory.total,power.draw",
            "--format=csv,noheader,nounits",
        ])
        found: list[DetectedDevice] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 3:
                continue
            name, memory_raw, power_raw = fields[:3]
            memory_mib = _number(memory_raw)
            power = _number(power_raw)
            key = _nvidia_catalog_key(name)
            found.append(DetectedDevice(
                name=name,
                count=1,
                catalog_key=key,
                memory_gb=memory_mib / 1024 if memory_mib is not None else None,
                live_power_watts=power,
                power_provenance=(Provenance.MEASURED.value if power is not None
                                  else "UNAVAILABLE"),
                performance_provenance=(
                    catalog.CATALOG[key].provenance.value if key else "UNAVAILABLE"
                ),
                source="nvidia-smi",
                notes=("Live power is one observation, not a calibrated curve",),
            ))
        return found


class SimulatedFleetProvider(HardwareProvider):
    """Load a development fleet from a small, explicit JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def detect(self) -> DetectionResult:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload.get("devices", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            raise ValueError("simulated fleet needs a non-empty devices list")
        devices: list[DetectedDevice] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("each simulated device must be an object")
            key = str(row.get("catalog_key", ""))
            if key not in catalog.CATALOG:
                raise ValueError(f"unknown hardware catalogue key {key!r}")
            count = int(row.get("count", 1))
            if count <= 0:
                raise ValueError("simulated device count must be positive")
            base = catalog.CATALOG[key]
            devices.append(DetectedDevice(
                name=base.name,
                count=count,
                catalog_key=key,
                memory_gb=float(row.get("memory_gb", base.memory_gb)),
                live_power_watts=(float(row["power_watts"])
                                  if row.get("power_watts") is not None else None),
                identity_provenance=Provenance.SIMULATED.value,
                memory_provenance=Provenance.SIMULATED.value,
                power_provenance=Provenance.SIMULATED.value,
                performance_provenance=Provenance.SIMULATED.value,
                source=str(self.path),
            ))
        return DetectionResult("simulated", devices)


def _number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        return None
    number = float(match.group(0))
    return number if number >= 0 else None


def _normal(value: str) -> str:
    cleaned = value.lower().replace("nvidia", "").replace("apple", "")
    return re.sub(r"[^a-z0-9]", "", cleaned)


def _apple_catalog_key(chip: str) -> str | None:
    normal = _normal(chip)
    matches = [key for key in catalog.CATALOG if key.startswith("m")
               and _normal(key) == normal]
    return matches[0] if matches else None


def _nvidia_catalog_key(name: str) -> str | None:
    normal = _normal(name)
    candidates = [
        (key, _normal(device.name))
        for key, device in catalog.CATALOG.items()
        if device.vendor == "NVIDIA"
    ]
    exact = next((key for key, device_name in candidates if device_name == normal), None)
    if exact:
        return exact
    contained = [
        (key, device_name) for key, device_name in candidates
        if device_name in normal or normal in device_name
    ]
    return max(contained, key=lambda item: len(item[1]))[0] if contained else None
