"""One pass over everything reachable, and one honest picture of the result.

Six sources already exist in this package and none of them talked to each
other: local detection, live occupancy, facility discovery over Redfish, the
measured ceilings in the baseline history, the device catalogue, and the
third-party MLPerf profiles. A scan consults all six, matches what they say
about the same piece of silicon, and reports it as one record per device.

The scan is deliberately *quick*: it reads, it does not benchmark. Nothing
here runs a workload, so nothing here can promote a figure to MEASURED —
that still requires the calibration path. What it can do is tell you, per
field, which of the six sources a number came from, and therefore how much
weight it carries.

Sources that are absent are reported as absent. A facility with no discovery
configuration is not an error, and a device nobody has benchmarked is not
given a plausible-looking number.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hardware import baseline_store, catalog, mlperf
from hardware.base import Provenance
from hardware.providers import LocalDetector
from hardware.telemetry import TelemetryCollector

#: Tokens that appear in device names without distinguishing anything.
_NOISE = re.compile(
    r"\b(nvidia|amd|apple|intel|instinct|geforce|radeon|server|edition|"
    r"gpu|accelerator|pcie|sxm5?|nvl|hbm3e?|hbm3|gddr6x?|gb|tb)\b",
    re.IGNORECASE)


def _tokens(name: str) -> set[str]:
    """Reduce a device name to the parts that identify the silicon."""
    cleaned = _NOISE.sub(" ", str(name or ""))
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower())
    return {token for token in cleaned.split() if token}


def match_published(name: str, profiles: list) -> list:
    """MLPerf profiles plausibly describing this device.

    Matching is on identifying tokens rather than exact strings, because the
    same part is written "NVIDIA H200-SXM-141GB" in a submission and
    "H200 SXM" in a BMC inventory. A device with no confident match gets an
    empty list rather than the nearest-looking one.
    """
    wanted = _tokens(name)
    if not wanted:
        return []
    scored = []
    for profile in profiles:
        have = _tokens(profile.accelerator)
        overlap = wanted & have
        if not overlap:
            continue
        # Require the distinguishing token (a model number such as "h200"
        # or "mi355x") rather than an incidental word.
        if not any(any(char.isdigit() for char in token) for token in overlap):
            continue
        scored.append((len(overlap) / max(len(wanted | have), 1), profile))
    scored.sort(key=lambda item: -item[0])
    return [profile for _, profile in scored]


def match_catalogue(name: str) -> tuple[str, object] | None:
    wanted = _tokens(name)
    if not wanted:
        return None
    best, best_score = None, 0.0
    for key, device in catalog.CATALOG.items():
        have = _tokens(device.name) | _tokens(key)
        overlap = wanted & have
        if not overlap or not any(
                any(char.isdigit() for char in token) for token in overlap):
            continue
        score = len(overlap) / max(len(wanted | have), 1)
        if score > best_score:
            best, best_score = (key, device), score
    return best


@dataclass
class ScannedDevice:
    name: str
    kind: str
    source: str
    count: int = 1
    identity: dict = field(default_factory=dict)
    live: dict = field(default_factory=dict)
    measured: dict | None = None
    published: list = field(default_factory=list)
    catalogue: dict | None = None
    provenance: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


@dataclass
class ScanReport:
    scanned_at: str
    devices: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["devices"] = [asdict(d) if not isinstance(d, dict) else d
                              for d in self.devices]
        return payload

    @property
    def accelerator_count(self) -> int:
        return sum(d.count for d in self.devices if d.kind in ("GPU", "SoC"))


def _published_summary(profiles: list, limit: int = 3) -> list:
    best: dict[str, dict] = {}
    for profile in profiles:
        key = f"{profile.model}|{profile.scenario}"
        if key in best:
            continue
        best[key] = {
            "model": profile.model,
            "scenario": profile.scenario,
            "per_accelerator": profile.per_accelerator_median,
            "units": profile.units,
            "submissions": profile.submissions,
            "watts_per_accelerator": profile.watts_per_accelerator,
            "provenance": Provenance.PUBLISHED.value,
        }
        if len(best) >= limit:
            break
    return list(best.values())


def scan(*, include_published: bool = True,
         discovery_config: Path | None = None) -> ScanReport:
    report = ScanReport(scanned_at=datetime.now(timezone.utc).isoformat())
    collector = TelemetryCollector()

    # 1. Local host identity, and 2. what it is doing right now.
    try:
        detection = LocalDetector().detect()
        report.sources["local_detection"] = detection.provider
        report.warnings.extend(detection.warnings)
    except Exception as exc:
        detection = None
        report.warnings.append(f"local detection failed: {exc}")

    try:
        telemetry = collector.snapshot()
        report.sources["telemetry"] = "occupancy"
    except Exception as exc:
        telemetry = {"devices": []}
        report.warnings.append(f"telemetry unavailable: {exc}")

    # 3. Published third-party performance for whatever is found.
    profiles: list = []
    if include_published:
        try:
            profiles, warnings = mlperf.cached_profiles()
            report.sources["published"] = f"MLPerf, {len(profiles)} profiles"
            report.warnings.extend(warnings)
        except Exception as exc:
            report.warnings.append(f"published profiles unavailable: {exc}")

    # 4. Measured ceilings this project established itself.
    try:
        baselines = {device: baseline_store.baseline(device)
                     for device in baseline_store.summary()["devices"]}
        report.sources["measured_baselines"] = f"{len(baselines)} device(s)"
    except Exception as exc:
        baselines = {}
        report.warnings.append(f"baseline history unavailable: {exc}")

    for entry in telemetry.get("devices", []):
        name = entry.get("name", "unknown")
        device = ScannedDevice(
            name=name, kind=entry.get("kind", "host"), source="local",
            identity=entry.get("static", {}), live=entry.get("live", {}),
            notes=list(entry.get("notes", [])),
        )
        device.provenance = {
            "identity": Provenance.MEASURED.value,
            "occupancy": Provenance.MEASURED.value,
            "throughput": "UNAVAILABLE",
        }
        state = baselines.get(name)
        if state and state.get("established"):
            device.measured = state["metrics"]
            device.provenance["ceiling"] = Provenance.MEASURED.value
            device.notes.append(
                f"ceiling reproduced across {state['run_count']} validated runs")
        elif state:
            device.notes.append(f"no ceiling yet: {state['reason']}")

        matched = match_catalogue(name)
        if matched:
            key, spec = matched
            device.catalogue = {
                "key": key,
                "peak_tflops_bf16": getattr(spec, "peak_tflops_bf16", None),
                "memory_bandwidth_gbs": getattr(spec, "memory_bandwidth_gbs", None),
                "provenance": spec.provenance.value,
            }
        found = match_published(name, profiles)
        if found:
            device.published = _published_summary(found)
            device.provenance["throughput"] = Provenance.PUBLISHED.value
        report.devices.append(device)

    # 5. Facility fleet, if any endpoints are declared.
    config = discovery_config or (
        Path(__file__).resolve().parents[1] / "data" / "discovery.json")
    if config.exists():
        try:
            from hardware.providers import RedfishFleetProvider
            result = RedfishFleetProvider.from_config(config).detect()
            report.sources["facility_discovery"] = f"redfish, {len(result.devices)} device(s)"
            report.warnings.extend(result.warnings)
            for found_device in result.devices:
                device = ScannedDevice(
                    name=found_device.name, kind="fleet", source=found_device.source,
                    count=found_device.count,
                    identity={"memory_gb": found_device.memory_gb},
                    live={"power_watts": found_device.live_power_watts},
                    notes=list(found_device.notes),
                    provenance={
                        "identity": found_device.identity_provenance,
                        "memory": found_device.memory_provenance,
                        "power": found_device.power_provenance,
                        "throughput": found_device.performance_provenance,
                    },
                )
                matches = match_published(found_device.name, profiles)
                if matches:
                    device.published = _published_summary(matches)
                    device.provenance["throughput"] = Provenance.PUBLISHED.value
                report.devices.append(device)
        except Exception as exc:
            report.warnings.append(f"facility discovery failed: {exc}")
    else:
        report.sources["facility_discovery"] = "not configured"

    if not report.devices:
        report.warnings.append("no device reported anything")
    return report


def render(report: ScanReport) -> str:
    """A readable summary — the point of the scan, not a JSON dump."""
    lines: list[str] = []
    add = lines.append
    add("═" * 74)
    add(f" HARDWARE SCAN   {report.scanned_at[:19].replace('T', ' ')} UTC")
    add("═" * 74)
    add("")
    add(" Sources consulted")
    for name, detail in report.sources.items():
        add(f"   {name.replace('_', ' '):<22} {detail}")
    add("")

    for device in report.devices:
        add("─" * 74)
        add(f" {device.name}   ({device.kind}"
            + (f" x{device.count}" if device.count > 1 else "") + ")")
        add("─" * 74)
        identity = {k: v for k, v in (device.identity or {}).items() if v is not None}
        if identity:
            add("   installed   " + " · ".join(
                f"{key.replace('_', ' ')}: {value}" for key, value in identity.items()))
        live = {k: v for k, v in (device.live or {}).items() if v is not None}
        if live:
            interesting = ("memory_available_gb", "storage_free_gb", "gpu_percent",
                           "cpu_percent", "power_watts")
            shown = [f"{key.replace('_', ' ')}: {live[key]}"
                     for key in interesting if key in live]
            if shown:
                add("   right now   " + " · ".join(shown))
        if device.measured:
            add("   MEASURED    " + " · ".join(
                f"{label.replace('_', ' ')}: {values['median']}"
                for label, values in device.measured.items()))
        if device.catalogue:
            cat = device.catalogue
            add(f"   catalogue   {cat['key']} · peak {cat['peak_tflops_bf16']} TFLOPS"
                f" · {cat['memory_bandwidth_gbs']} GB/s  [{cat['provenance']}]")
        for item in device.published:
            watts = (f" · {item['watts_per_accelerator']} W/acc"
                     if item.get("watts_per_accelerator") else "")
            add(f"   PUBLISHED   {item['model']} {item['scenario']}: "
                f"{item['per_accelerator']:,.1f} {item['units']}/accelerator"
                f" (n={item['submissions']}){watts}")
        if not device.published and not device.measured:
            add("   throughput  UNAVAILABLE — not benchmarked here, and no"
                " published result matches this part")
        for note in device.notes[:3]:
            add(f"   note        {note}")
        add("")

    add("═" * 74)
    add(f" {len(report.devices)} device record(s). Throughput is only ever MEASURED")
    add(" after repeated local runs; PUBLISHED figures are tuned ceilings from")
    add(" third-party submissions, not typical deployment throughput.")
    for warning in report.warnings:
        add(f"   warning: {warning}")
    add("═" * 74)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan all reachable hardware and report what is known about it.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead")
    parser.add_argument("--no-published", action="store_true",
                        help="skip third-party MLPerf lookup")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = scan(include_published=not args.no_published)
    text = json.dumps(report.as_dict(), indent=2, default=str) if args.json \
        else render(report)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
