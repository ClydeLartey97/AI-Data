"""Third-party measured hardware performance, from MLPerf Inference results.

The catalogue's throughput figures are ESTIMATED for everything the project
has not personally benchmarked, which is nearly everything. MLPerf closes
that gap in the one way consistent with the rest of this codebase: it is the
same work on every device. A submission names a fixed model, a fixed
scenario and a required accuracy, and reports how fast that ran. Two
submissions on different silicon are therefore comparable in the way two
vendor spec sheets never are.

Three limits are enforced rather than noted in passing.

*Submitted results are tuned ceilings.* Vendors optimise hard for these, so a
figure here is what the hardware can do, not what a normal deployment sees.
Everything derived carries ``Provenance.PUBLISHED`` for that reason.

*Scenarios are not interchangeable.* Offline batches without latency limits;
Server and Interactive hold a latency target. Mixing them silently would
invent throughput, so results are only ever grouped within one scenario.

*Per-accelerator rates require a known accelerator count.* Many rows omit it,
and dividing by a guess would fabricate precision. Those rows are dropped.
"""
from __future__ import annotations

import json
import re
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from hardware.base import Provenance

RESULTS_URL = ("https://raw.githubusercontent.com/mlcommons/"
               "inference_results_{version}/main/summary_results.json")
DEFAULT_VERSIONS = ("v6.0", "v5.0")
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "mlperf"

#: Rates in these units are a throughput we can reason about. Others (latency
#: percentiles, for instance) describe something else and are ignored.
THROUGHPUT_UNITS = ("tokens/s", "samples/s", "queries/s")


@dataclass(frozen=True)
class Result:
    submitter: str
    system: str
    model: str
    scenario: str
    accelerator: str
    accelerator_count: int
    throughput: float
    throughput_units: str
    accuracy: str
    power_watts: float | None
    version: str

    @property
    def per_accelerator(self) -> float:
        return self.throughput / self.accelerator_count

    @property
    def watts_per_accelerator(self) -> float | None:
        if self.power_watts is None:
            return None
        return self.power_watts / self.accelerator_count


@dataclass
class ScalingPoint:
    accelerator_count: int
    per_accelerator_throughput: float
    samples: int


@dataclass
class DeviceProfile:
    """What a named accelerator achieved on one model in one scenario."""

    accelerator: str
    model: str
    scenario: str
    units: str
    per_accelerator_median: float
    per_accelerator_best: float
    submissions: int
    accelerator_counts: list[int] = field(default_factory=list)
    scaling: list[ScalingPoint] = field(default_factory=list)
    watts_per_accelerator: float | None = None
    provenance: str = Provenance.PUBLISHED.value

    def scaling_efficiency(self) -> dict | None:
        """Measured multi-device efficiency, against the smallest configuration.

        `core/workload.py` models this analytically and concedes it reports
        about 99% where reality is 70–85%. This is the same quantity observed
        rather than assumed, so the two can finally be compared.
        """
        if len(self.scaling) < 2:
            return None
        ordered = sorted(self.scaling, key=lambda p: p.accelerator_count)
        base = ordered[0]
        largest = ordered[-1]
        if not base.per_accelerator_throughput:
            return None
        return {
            "baseline_accelerators": base.accelerator_count,
            "largest_accelerators": largest.accelerator_count,
            "efficiency_at_largest": round(
                largest.per_accelerator_throughput
                / base.per_accelerator_throughput, 4),
            "points": [
                {"accelerators": point.accelerator_count,
                 "per_accelerator": round(point.per_accelerator_throughput, 3),
                 "efficiency": round(point.per_accelerator_throughput
                                     / base.per_accelerator_throughput, 4)}
                for point in ordered
            ],
        }


def _clean_accelerator(value: object) -> str:
    text = re.sub(r"\s*\(x\d+\)\s*$", "", str(value or "").strip())
    return re.sub(r"\s+", " ", text)


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


def _accelerator_count(row: dict) -> int | None:
    total = row.get("Total Accelerators")
    if isinstance(total, (int, float)) and total >= 1:
        return int(total)
    # Fall back to the per-node count multiplied by nodes, but only when both
    # are present; guessing either would fabricate a per-device rate.
    per_node, nodes = row.get("a#"), row.get("Nodes")
    if isinstance(per_node, (int, float)) and isinstance(nodes, (int, float)):
        if per_node >= 1 and nodes >= 1:
            return int(per_node * nodes)
    return None


def download(version: str, *, cache_dir: Path | None = None,
             refresh: bool = False, opener=urllib.request.urlopen) -> list[dict]:
    """Fetch one results round, caching it so re-runs need no network."""
    target = (cache_dir or CACHE_DIR) / f"summary_{version}.json"
    if target.exists() and not refresh:
        return json.loads(target.read_text(encoding="utf-8"))
    try:
        with opener(RESULTS_URL.format(version=version), timeout=90) as response:
            payload = response.read().decode("utf-8")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise RuntimeError(f"could not fetch MLPerf {version}: {exc}") from exc
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError(f"MLPerf {version} summary is not a list of results")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def parse(rows: list[dict], version: str = "") -> list[Result]:
    """Keep only rows that can support an honest per-accelerator rate."""
    results: list[Result] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("errors"):
            continue
        units = str(row.get("Performance_Units") or "")
        if not any(unit.lower() in units.lower() for unit in THROUGHPUT_UNITS):
            continue
        throughput = _number(row.get("Performance_Result"))
        accelerator = _clean_accelerator(row.get("Accelerator"))
        count = _accelerator_count(row)
        if throughput is None or not accelerator or not count:
            continue
        results.append(Result(
            submitter=str(row.get("Submitter") or ""),
            system=str(row.get("System") or ""),
            model=str(row.get("Model") or ""),
            scenario=str(row.get("Scenario") or ""),
            accelerator=accelerator,
            accelerator_count=count,
            throughput=throughput,
            throughput_units=units,
            accuracy=str(row.get("Accuracy") or ""),
            power_watts=_number(row.get("Power_Result")),
            version=str(row.get("version") or version),
        ))
    return results


def build_profiles(results: list[Result], *,
                   min_submissions: int = 1) -> list[DeviceProfile]:
    """Group by accelerator, model and scenario — never across scenarios."""
    grouped: dict[tuple[str, str, str], list[Result]] = {}
    for result in results:
        grouped.setdefault(
            (result.accelerator, result.model, result.scenario), []).append(result)

    profiles: list[DeviceProfile] = []
    for (accelerator, model, scenario), items in grouped.items():
        if len(items) < min_submissions:
            continue
        rates = [item.per_accelerator for item in items]
        by_count: dict[int, list[float]] = {}
        for item in items:
            by_count.setdefault(item.accelerator_count, []).append(
                item.per_accelerator)
        watts = [item.watts_per_accelerator for item in items
                 if item.watts_per_accelerator is not None]
        profiles.append(DeviceProfile(
            accelerator=accelerator,
            model=model,
            scenario=scenario,
            units=items[0].throughput_units,
            per_accelerator_median=round(statistics.median(rates), 4),
            per_accelerator_best=round(max(rates), 4),
            submissions=len(items),
            accelerator_counts=sorted(by_count),
            scaling=[ScalingPoint(count, round(statistics.median(values), 4),
                                  len(values))
                     for count, values in sorted(by_count.items())],
            watts_per_accelerator=round(statistics.median(watts), 1) if watts else None,
        ))
    profiles.sort(key=lambda p: (-p.submissions, p.accelerator))
    return profiles


def load(versions=DEFAULT_VERSIONS, *, cache_dir: Path | None = None,
         refresh: bool = False) -> tuple[list[Result], list[str]]:
    """Load every requested round, reporting rounds that could not be read."""
    results: list[Result] = []
    warnings: list[str] = []
    for version in versions:
        try:
            results.extend(parse(download(version, cache_dir=cache_dir,
                                          refresh=refresh), version))
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(str(exc))
    return results, warnings


#: Parsing 17k rows costs about a second, and the published record only
#: changes when MLPerf publishes a round. Cached for the process lifetime.
_PROFILE_CACHE: tuple[list, list[str]] | None = None


def cached_profiles() -> tuple[list[DeviceProfile], list[str]]:
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        results, warnings = load()
        _PROFILE_CACHE = (build_profiles(results), warnings)
    return _PROFILE_CACHE
