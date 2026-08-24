"""One answer per device, best-sourced first.

Six places in this project know something about a piece of hardware: a local
measurement we reproduced, an audited third-party submission, a figure derived
from a sibling part, the catalogue's vendor spec, live occupancy, and facility
discovery. Until now the pages each picked a source by hand, which is how the
Fleet Lab came to display `performance ESTIMATED` for an M2 whose real ceiling
this project had already measured three times and stored.

**The rule, and it is the whole module: if a measurement exists, it is used.**
A catalogue estimate is what you fall back to when nobody has measured the
part, never something that quietly outranks a number we produced ourselves.

## Achieved is not peak, and the distinction survives here

What was measured on the M2 is **achieved dense GEMM throughput** — 2,583
GFLOP/s. The catalogue's 3.6 TFLOPS is a **theoretical peak**. These are
different quantities, and overwriting one with the other is a category error
that would silently change what every downstream calculation means. So they
are kept in separate fields: the measurement replaces the *achieved* figure,
the catalogue keeps the *peak*, and the ratio between them is the real
utilisation that `mfu` currently guesses at.

That is also why resolving performance does not resolve `mfu`. Dense matmul is
the arithmetic ceiling; a full transformer with attention and memory movement
does not reach it. Until the prefill/decode benchmark runs, utilisation stays
ESTIMATED even on a device whose ceiling is MEASURED — and a card that says
"peak measured, utilisation estimated" is telling the truth, where a blanket
label in either direction would not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hardware.derive import (CATALOGUE_REFERENCE_CORES, DERIVED, M2_FAMILY,
                             MEASURED_M2, PROVENANCE_RANK, DerivationRefused,
                             derive_family)


@dataclass(frozen=True)
class Figure:
    """A number and where it came from."""

    value: float | None
    provenance: str
    source: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    def public_dict(self) -> dict:
        return {"value": self.value, "provenance": self.provenance,
                "source": self.source}


@dataclass(frozen=True)
class ResolvedDevice:
    """Everything known about one device, each field with its own provenance."""

    key: str
    #: Achieved dense GEMM throughput. Measured where we ran it.
    achieved_gflops: Figure
    #: Achieved streaming memory bandwidth.
    bandwidth_gbs: Figure
    #: Vendor theoretical peak. Never overwritten by a measurement.
    peak_tflops: Figure
    #: Fraction of peak a real workload reaches. Still a guess, and says so.
    utilisation: Figure
    notes: tuple[str, ...] = field(default_factory=tuple)

    #: Cores the measured part has, and cores the catalogue peak describes.
    #: Kept so the fraction below compares like with like.
    measured_gpu_cores: int | None = None
    catalogue_gpu_cores: int | None = None

    @property
    def comparable_peak_gflops(self) -> float | None:
        """The catalogue peak, scaled to the configuration actually measured.

        A catalogue row quotes the part's top configuration. Measuring a
        smaller variant and dividing by that row understates the hardware —
        which is exactly how an 8-core M2 came to look like it reached 72% of
        peak when the real figure is nearer 90%.
        """
        if not self.peak_tflops.known:
            return None
        peak_gflops = self.peak_tflops.value * 1000
        if peak_gflops <= 0:
            return None
        if self.measured_gpu_cores and self.catalogue_gpu_cores:
            peak_gflops *= self.measured_gpu_cores / self.catalogue_gpu_cores
        return peak_gflops

    @property
    def measured_utilisation(self) -> float | None:
        """Achieved over comparable peak — what `mfu` is standing in for.

        This is the dense-matmul ceiling as a fraction of theoretical peak,
        not the transformer utilisation `mfu` models, so it is offered as
        evidence rather than substituted for it.
        """
        peak_gflops = self.comparable_peak_gflops
        if not self.achieved_gflops.known or not peak_gflops:
            return None
        return self.achieved_gflops.value / peak_gflops

    def public_dict(self) -> dict:
        return {
            "key": self.key,
            "achieved_gflops": self.achieved_gflops.public_dict(),
            "bandwidth_gbs": self.bandwidth_gbs.public_dict(),
            "peak_tflops": self.peak_tflops.public_dict(),
            "utilisation": self.utilisation.public_dict(),
            "measured_utilisation": self.measured_utilisation,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        """One line an operator surface can show instead of a bare label."""
        parts = []
        if self.achieved_gflops.known:
            parts.append(f"{self.achieved_gflops.value / 1000:.2f} TFLOPS "
                         f"achieved {self.achieved_gflops.provenance}")
        if self.bandwidth_gbs.known:
            parts.append(f"{self.bandwidth_gbs.value:.0f} GB/s "
                         f"{self.bandwidth_gbs.provenance}")
        parts.append(f"utilisation {self.utilisation.provenance}")
        return " · ".join(parts)


def _pick(*candidates: Figure) -> Figure:
    """The best-sourced known figure. A labelled absence never wins."""
    usable = [f for f in candidates if f.known]
    if not usable:
        return Figure(None, "UNAVAILABLE")
    return min(usable, key=lambda f: PROVENANCE_RANK.get(f.provenance, 99))


def resolve(device_key: str, *, measured: dict | None = None,
            published: dict | None = None,
            catalogue_device=None) -> ResolvedDevice:
    """Resolve one device from every source, measurement first.

    Sources are injected rather than fetched so this stays cheap to test and
    so a caller that already loaded the baseline store does not load it twice.

    ``measured`` and ``published`` are ``{key: {"achieved_gflops": float,
    "bandwidth_gbs": float}}``. ``catalogue_device`` is a catalogue ``Device``.
    """
    measured = measured or {}
    published = published or {}
    notes: list[str] = []

    local = measured.get(device_key) or {}
    third_party = published.get(device_key) or {}

    derived: dict = {}
    if device_key not in measured:
        # Only derive for parts nobody measured. A derived figure must never
        # be computed on top of a real one.
        try:
            siblings = derive_family(MEASURED_M2, M2_FAMILY)
        except DerivationRefused:
            siblings = {}
        sibling = siblings.get(device_key)
        if sibling is not None:
            derived = {"achieved_gflops": sibling.gemm_fp16_gflops,
                       "bandwidth_gbs": sibling.memory_bandwidth_gbs}
            notes.extend(sibling.notes)

    achieved = _pick(
        Figure(local.get("achieved_gflops"), "MEASURED",
               "reproduced locally"),
        Figure(third_party.get("achieved_gflops"), "PUBLISHED",
               "third-party submission"),
        Figure(derived.get("achieved_gflops"), DERIVED,
               f"scaled from {MEASURED_M2.chip_key}"),
    )
    bandwidth = _pick(
        Figure(local.get("bandwidth_gbs"), "MEASURED", "reproduced locally"),
        Figure(third_party.get("bandwidth_gbs"), "PUBLISHED",
               "third-party submission"),
        Figure(derived.get("bandwidth_gbs"), DERIVED,
               f"scaled from {MEASURED_M2.chip_key}'s achieved bus fraction"),
    )

    peak = Figure(None, "UNAVAILABLE")
    utilisation = Figure(None, "UNAVAILABLE")
    if catalogue_device is not None:
        prov = getattr(catalogue_device, "provenance", None)
        label = getattr(prov, "value", prov) or "SPEC"
        peak = Figure(getattr(catalogue_device, "peak_tflops_bf16", None),
                      label, "catalogue")
        # Deliberately not resolved from the measurement: dense GEMM is the
        # ceiling, not what a transformer achieves.
        utilisation = Figure(getattr(catalogue_device, "mfu", None),
                             "ESTIMATED", "catalogue assumption")

    spec = M2_FAMILY.get(device_key)
    cores = dict(
        measured_gpu_cores=(MEASURED_M2.gpu_cores if device_key == MEASURED_M2.chip_key
                            else (spec.gpu_cores if spec else None)),
        catalogue_gpu_cores=CATALOGUE_REFERENCE_CORES.get(device_key),
    )
    resolved = ResolvedDevice(
        key=device_key, achieved_gflops=achieved, bandwidth_gbs=bandwidth,
        peak_tflops=peak, utilisation=utilisation, notes=tuple(notes), **cores)

    real = resolved.measured_utilisation
    if real is not None and achieved.provenance == "MEASURED":
        notes.append(
            f"dense GEMM reaches {real:.0%} of the theoretical peak for this "
            f"part's own core count. The catalogue assumes a real workload reaches "
            f"{utilisation.value:.0%}; both can be true, because dense matmul "
            f"is the ceiling and a transformer is not. The prefill/decode "
            f"benchmark replaces the assumption, not this measurement.")
        resolved = ResolvedDevice(
            key=device_key, achieved_gflops=achieved, bandwidth_gbs=bandwidth,
            peak_tflops=peak, utilisation=utilisation, notes=tuple(notes),
            **cores)
    return resolved


def measured_from_baselines(rows) -> dict:
    """Turn stored baseline runs into the ``measured`` map ``resolve`` wants.

    Accepts whatever `hardware.baseline_store` returns — dicts or objects —
    because the store's shape is its own business and this should not break if
    a column is added.
    """
    out: dict[str, dict] = {}
    for row in rows or []:
        get = (row.get if isinstance(row, dict)
               else lambda k, d=None, r=row: getattr(r, k, d))
        key = get("device_key") or get("device")
        if not key:
            continue
        entry = out.setdefault(str(key), {})
        metric = str(get("metric") or "").lower()
        value = get("value")
        if value is None:
            continue
        if "gemm" in metric and "fp16" in metric:
            entry["achieved_gflops"] = float(value)
        elif "bandwidth" in metric or "read" in metric:
            entry["bandwidth_gbs"] = float(value)
    return {k: v for k, v in out.items() if v}
