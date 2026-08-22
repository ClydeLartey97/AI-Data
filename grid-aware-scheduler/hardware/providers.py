"""Hardware discovery behind an explicit provider boundary.

Detection and performance estimation are different evidence. A machine can
report that an accelerator exists and how much memory it has without proving
its sustained throughput or power curve. ``DetectedDevice`` therefore carries
provenance per field and never promotes a whole catalogue row to MEASURED.

The local provider uses read-only operating-system commands. It deliberately
does not retain serial numbers, UUIDs or other host identifiers returned by
those commands.

``RedfishFleetProvider`` extends the same discipline to facility scale:
GET-only walks of operator-declared BMC endpoints, an anonymous tier limited
to the DSP0266 service root, and keyed-digest device identity in place of raw
serials. The full boundary design is docs/discovery.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import platform
import re
import secrets
import shutil
import ssl
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hardware import catalogue
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
    #: Which instrument the live power figure came from — "board", "system"
    #: or "outlet" (docs/discovery.md). Scopes are never comparable as equals.
    power_scope: str | None = None
    #: Stable pseudonymous fleet identity: an HMAC-SHA256 keyed digest of the
    #: protocol-native durable identifier. The raw serial/UUID is discarded at
    #: read time and never stored or serialised.
    device_digest: str | None = None


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
            if not evidence.catalog_key or evidence.catalog_key not in catalogue.CATALOGUE:
                continue
            device = catalogue.CATALOGUE[evidence.catalog_key]
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
            catalogue.CATALOGUE[key].provenance.value if key in catalogue.CATALOGUE
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
                    catalogue.CATALOGUE[key].provenance.value if key else "UNAVAILABLE"
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
            if key not in catalogue.CATALOGUE:
                raise ValueError(f"unknown hardware catalogue key {key!r}")
            count = int(row.get("count", 1))
            if count <= 0:
                raise ValueError("simulated device count must be positive")
            base = catalogue.CATALOGUE[key]
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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ACCELERATOR_TYPES = frozenset({"GPU", "FPGA", "Accelerator", "DSP"})
_KNOWN_DISCOVERY_PROTOCOLS = frozenset({"redfish", "snmp", "dcgm", "lldp", "ipmi"})
DEFAULT_SITE_KEY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "cache" / "discovery-site-key"
)


class RequestBudgetExceeded(RuntimeError):
    """An endpoint asked for more requests than one poll is allowed."""


@dataclass(frozen=True)
class RedfishEndpoint:
    """One operator-declared Redfish endpoint (docs/discovery.md).

    Plain HTTP is permitted only for loopback verification runs against a
    served mockup tree; any non-loopback endpoint requires TLS.
    """

    host: str
    port: int = 443
    tls: bool = True
    tls_fingerprint_sha256: str | None = None
    credential_env: str | None = None

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("endpoint host is required")
        if not 0 < self.port < 65536:
            raise ValueError("endpoint port must be in 1-65535")
        if not self.tls and self.host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"plain HTTP is loopback-only; {self.host!r} requires TLS"
            )

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"


def load_site_key(path: Path | None = None) -> bytes:
    """Load, or create once, the local key for pseudonymous device identity.

    The key lives outside version control. Rotating it re-keys the whole
    inventory and breaks joins to prior history.
    """
    target = path or DEFAULT_SITE_KEY_PATH
    if target.exists():
        key = bytes.fromhex(target.read_text(encoding="utf-8").strip())
        if len(key) < 16:
            raise ValueError("discovery site key is too short")
        return key
    key = secrets.token_bytes(32)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(key.hex() + "\n", encoding="utf-8")
    target.chmod(0o600)
    return key


def _fetch_over_http(endpoint: RedfishEndpoint, path: str,
                     headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """GET one resource. GET is the only verb this module ever sends."""
    if endpoint.tls:
        if endpoint.tls_fingerprint_sha256:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context()
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            endpoint.host, endpoint.port, timeout=timeout, context=context)
    else:
        connection = http.client.HTTPConnection(
            endpoint.host, endpoint.port, timeout=timeout)
    try:
        connection.connect()
        if endpoint.tls and endpoint.tls_fingerprint_sha256:
            certificate = connection.sock.getpeercert(binary_form=True)
            observed = hashlib.sha256(certificate or b"").hexdigest()
            expected = endpoint.tls_fingerprint_sha256.lower().replace(":", "")
            if observed != expected:
                raise ValueError("TLS certificate fingerprint mismatch")
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class RedfishFleetProvider(HardwareProvider):
    """Read-only facility inventory over Redfish (docs/discovery.md).

    Tier 0 without credentials: the anonymous service root only. With an
    operator-supplied read-only credential, the walk descends to Systems,
    accelerator Processors and Chassis power. It never sends anything but
    GET, never probes an undeclared address, and never retains a raw serial,
    UUID or asset tag — fleet identity is a keyed digest.
    """

    def __init__(self, endpoints: list[RedfishEndpoint], *,
                 site_key: bytes | None = None,
                 fetcher: Callable | None = None,
                 timeout: float = 10.0,
                 max_requests_per_endpoint: int = 32) -> None:
        if not endpoints:
            raise ValueError("at least one Redfish endpoint is required")
        self.endpoints = list(endpoints)
        self._site_key = site_key if site_key is not None else load_site_key()
        self._fetcher = fetcher or (
            lambda endpoint, path, headers: _fetch_over_http(
                endpoint, path, headers, timeout))
        self._budget = max_requests_per_endpoint

    @classmethod
    def from_config(cls, path: str | Path, **kwargs) -> "RedfishFleetProvider":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "facility-discovery-v1":
            raise ValueError("discovery config must declare schema facility-discovery-v1")
        rows = payload.get("endpoints")
        if not isinstance(rows, list) or not rows:
            raise ValueError("discovery config needs a non-empty endpoints list")
        endpoints = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("each endpoint must be an object")
            protocol = str(row.get("protocol", ""))
            if protocol not in _KNOWN_DISCOVERY_PROTOCOLS:
                raise ValueError(f"unknown discovery protocol {protocol!r}")
            if protocol != "redfish":
                continue
            endpoints.append(RedfishEndpoint(
                host=str(row.get("host", "")),
                port=int(row.get("port", 443)),
                tls=bool(row.get("tls", True)),
                tls_fingerprint_sha256=row.get("tls_fingerprint_sha256"),
                credential_env=row.get("credential_env"),
            ))
        if not endpoints:
            raise ValueError("discovery config contains no redfish endpoints")
        return cls(endpoints, **kwargs)

    def detect(self) -> DetectionResult:
        devices: list[DetectedDevice] = []
        warnings: list[str] = []
        for endpoint in self.endpoints:
            try:
                devices.extend(self._walk_endpoint(endpoint, warnings))
            except RequestBudgetExceeded:
                warnings.append(
                    f"{endpoint.label}: request budget of {self._budget} exhausted; "
                    "inventory for this endpoint is partial")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(f"{endpoint.label}: discovery failed: {exc}")
        if not devices:
            warnings.append("No Redfish system produced inventory evidence")
        return DetectionResult("redfish", devices, warnings=warnings)

    # -- one endpoint ------------------------------------------------------

    def _walk_endpoint(self, endpoint: RedfishEndpoint,
                       warnings: list[str]) -> list[DetectedDevice]:
        used = 0

        def get(path: str, headers: dict[str, str]) -> dict:
            nonlocal used
            if used >= self._budget:
                raise RequestBudgetExceeded(path)
            used += 1
            status, body = self._fetcher(endpoint, path, headers)
            if status != 200:
                raise ValueError(f"GET {path} returned HTTP {status}")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError(f"GET {path} did not return an object")
            return payload

        # Tier 0: the service root is anonymous by DSP0266. No credentials
        # are ever sent to it.
        root = get("/redfish/v1/", {})
        auth = self._auth_header(endpoint, warnings)
        if auth is None:
            warnings.append(
                f"{endpoint.label}: no read-only credential; stopped at the "
                f"anonymous service root (Redfish {root.get('RedfishVersion', '?')})")
            return []

        # Tier 1: operator-credentialed read-only inventory.
        power_by_system = self._chassis_power(root, get, auth)
        found: list[DetectedDevice] = []
        systems_link = _odata_link(root.get("Systems"))
        if not systems_link:
            warnings.append(f"{endpoint.label}: service root exposes no Systems")
            return []
        for member in _members(get(systems_link, auth)):
            system = get(member, auth)
            found.extend(self._system_devices(endpoint, system,
                                              power_by_system, get, auth))
        return found

    def _auth_header(self, endpoint: RedfishEndpoint,
                     warnings: list[str]) -> dict[str, str] | None:
        if not endpoint.credential_env:
            return None
        value = os.environ.get(endpoint.credential_env)
        if value is None:
            warnings.append(
                f"{endpoint.label}: credential environment variable "
                f"{endpoint.credential_env} is not set")
            return None
        if ":" not in value:
            warnings.append(
                f"{endpoint.label}: credential must be user:password")
            return None
        token = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def _chassis_power(self, root: dict, get: Callable,
                       auth: dict[str, str]) -> dict[str, float]:
        """Map system @odata.id -> chassis input watts, joined the way the
        protocol states it: through PowerControl.RelatedItem, never guessed."""
        chassis_link = _odata_link(root.get("Chassis"))
        if not chassis_link:
            return {}
        by_system: dict[str, float] = {}
        for member in _members(get(chassis_link, auth)):
            chassis = get(member, auth)
            power_link = _odata_link(chassis.get("Power"))
            if not power_link:
                continue
            power = get(power_link, auth)
            for control in power.get("PowerControl", []):
                if not isinstance(control, dict):
                    continue
                watts = control.get("PowerConsumedWatts")
                if not isinstance(watts, (int, float)):
                    continue
                for related in control.get("RelatedItem", []):
                    target = _odata_link(related)
                    if target and "/Systems/" in target:
                        by_system.setdefault(target, float(watts))
        return by_system

    def _system_devices(self, endpoint: RedfishEndpoint, system: dict,
                        power_by_system: dict[str, float], get: Callable,
                        auth: dict[str, str]) -> list[DetectedDevice]:
        durable = str(system.get("UUID") or system.get("SerialNumber")
                      or system.get("SKU") or "")
        digest = self._digest("redfish-system", durable) if durable else None
        name = " ".join(part for part in (
            str(system.get("Manufacturer") or "").strip(),
            str(system.get("Model") or "").strip()) if part) or "Unknown system"
        memory = system.get("MemorySummary") or {}
        memory_gib = memory.get("TotalSystemMemoryGiB")
        watts = power_by_system.get(str(system.get("@odata.id") or ""))
        processors = system.get("ProcessorSummary") or {}
        notes = tuple(filter(None, (
            f"{processors.get('Count')}x {processors.get('Model')}"
            if processors.get("Count") and processors.get("Model") else "",
            "Memory reported by Redfish in GiB",
            "Chassis system input power; includes fans and PSU losses"
            if watts is not None else "",
            "Throughput and power curve are not discoverable; "
            "calibration is the only path to MEASURED performance",
        )))
        devices = [DetectedDevice(
            name=name,
            count=1,
            catalog_key=None,
            memory_gb=float(memory_gib) if memory_gib is not None else None,
            live_power_watts=float(watts) if watts is not None else None,
            memory_provenance=(Provenance.MEASURED.value
                               if memory_gib is not None else "UNAVAILABLE"),
            power_provenance=(Provenance.MEASURED.value
                              if watts is not None else "UNAVAILABLE"),
            power_scope="system" if watts is not None else None,
            performance_provenance="UNAVAILABLE",
            source=f"redfish:{endpoint.label}",
            device_digest=digest,
            notes=notes,
        )]
        devices.extend(self._accelerators(endpoint, system, durable, get, auth))
        return devices

    def _accelerators(self, endpoint: RedfishEndpoint, system: dict,
                      system_durable: str, get: Callable,
                      auth: dict[str, str]) -> list[DetectedDevice]:
        link = _odata_link(system.get("Processors"))
        if not link:
            return []
        found: list[DetectedDevice] = []
        for member in _members(get(link, auth)):
            processor = get(member, auth)
            kind = str(processor.get("ProcessorType") or "")
            if kind not in _ACCELERATOR_TYPES:
                continue  # CPUs are summarised on the system record
            model = str(processor.get("Model") or kind)
            maker = str(processor.get("Manufacturer") or "")
            key = (_nvidia_catalog_key(model)
                   if "nvidia" in f"{maker} {model}".lower() else None)
            found.append(DetectedDevice(
                name=model,
                count=1,
                catalog_key=key,
                memory_gb=None,
                memory_provenance="UNAVAILABLE",
                performance_provenance=(
                    catalogue.CATALOGUE[key].provenance.value
                    if key in catalogue.CATALOGUE else "UNAVAILABLE"),
                source=f"redfish:{endpoint.label}",
                device_digest=self._digest(
                    "redfish-processor",
                    f"{system_durable}|{processor.get('Id') or member}"),
                notes=(f"{kind} reported by Redfish",),
            ))
        return found

    def _digest(self, namespace: str, durable: str) -> str:
        message = f"{namespace}|{durable}".encode("utf-8")
        return hmac.new(self._site_key, message, hashlib.sha256).hexdigest()


def _odata_link(value) -> str | None:
    if isinstance(value, dict):
        link = value.get("@odata.id")
        if isinstance(link, str) and link.startswith("/redfish/v1"):
            return link
    return None


def _members(collection: dict) -> list[str]:
    members = collection.get("Members")
    if not isinstance(members, list):
        return []
    return [link for link in (_odata_link(member) for member in members) if link]


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
    matches = [key for key in catalogue.CATALOGUE if key.startswith("m")
               and _normal(key) == normal]
    return matches[0] if matches else None


def _nvidia_catalog_key(name: str) -> str | None:
    normal = _normal(name)
    candidates = [
        (key, _normal(device.name))
        for key, device in catalogue.CATALOGUE.items()
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
