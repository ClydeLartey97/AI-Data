"""Scale one measured chip to its siblings, where the silicon permits it.

We measured an Apple M2: 2,583 GFLOP/s fp16 dense GEMM and 75.7 GB/s streaming
read, three preflight-validated runs, 0.3% spread. Every other Apple chip in
the catalogue carries a vendor peak and an assumed utilisation, which is a
worse class of number entirely — and that is silly, because within one Apple
Silicon generation the larger parts are *the same GPU core replicated*. An M2
Pro is an M2 with more of the identical cores; an M2 Ultra is two M2 Max dies
fused. So a per-core rate measured on one member is a real measurement of the
core the others are built from.

**This is a narrow licence and the module enforces its edges.** It works
because Apple ships one core design per generation across the whole family. It
does not work across generations (M2 to M3 changes the core), and it certainly
does not work across vendors. `derive` refuses both rather than producing a
plausible number, because the entire value of this project's provenance
discipline is that a figure never looks better sourced than it is.

**Compute scales with cores. Bandwidth does not.**

That distinction is the one thing here that is easy to get wrong and expensive
to get wrong. GPU cores set arithmetic throughput, so GFLOP/s tracks the core
count. Memory bandwidth is set by the *memory bus*, which Apple widens on a
different schedule: 100 GB/s on M2, 200 on Pro, 400 on Max, 800 on Ultra —
doublings that do not match the core ratios at all. Scaling bandwidth by cores
would overstate an M2 Pro's memory throughput by roughly a fifth and understate
an Ultra's badly. Since decode is bandwidth-bound and prefill is
compute-bound, getting this backwards would corrupt exactly the prefill/decode
split `hardware/roofline.py` exists to model.

What travels from the measurement to a sibling is therefore not the bandwidth
figure but the **achieved fraction of the bus**: we measured 75.7 GB/s against
a 100 GB/s spec bus, so 76% of theoretical is what this memory system actually
delivers, and that ratio is a property of the design rather than of the part.

**Two derates that are not linear, both stated rather than hidden.**

*UltraFusion.* An Ultra is two dies over an interconnect, not one die twice the
size. Work that spans both halves pays for the crossing. A linear claim would
be too generous, so Ultra carries an explicit derate and says so.

*Cooling.* The anchor was measured on a **fanless** M2. A Max or Ultra lives in
an actively cooled chassis and holds its rate far longer, so the derived
sustained figure for those parts is, if anything, conservative — which is the
safe direction, and is recorded so nobody later reads a low number as an error.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Provenance for a figure scaled from a real measurement on the same
#: architecture. Ranked between PUBLISHED and SPEC: nobody ran this exact part,
#: so it is not MEASURED, but it is anchored to silicon we did run rather than
#: to a vendor's marketing peak.
DERIVED = "DERIVED"

#: Priority order for any figure about a device. The rule the whole project
#: turns on: **a real measurement always wins.** Anything derived is a fallback
#: for parts nobody has measured or published, never an override.
PROVENANCE_RANK = {
    "MEASURED": 0,
    "PUBLISHED": 1,
    DERIVED: 2,
    "CONTRACTED": 3,
    "SPEC": 4,
    "ESTIMATED": 5,
    "UNAVAILABLE": 9,
}

#: GPU core count each catalogue peak figure corresponds to.
#:
#: This exists because of a real error it prevents. The catalogue lists the M2
#: at 3.6 TFLOPS, which is the **10-core** configuration — its Pro/Max/Ultra
#: rows are that figure scaled by core count, and the ratios confirm it
#: (6.8/3.6 = 1.89 against 19/10 = 1.9). The M2 measured here is the **8-core**
#: part. Dividing 2,583 GFLOP/s by a 10-core peak produced the "72% of
#: estimated peak" recorded earlier in this project; against the 8-core peak of
#: 2.88 TFLOPS the achieved fraction is about 90%, which is what a
#: well-optimised dense matmul should reach. Compare like with like or the
#: hardware looks worse than it is.
CATALOGUE_REFERENCE_CORES = {
    "m2": 10, "m2-pro": 19, "m2-max": 38, "m2-ultra": 76,
}

#: Interconnect derate for the two-die Ultra parts. Deliberately a single
#: blunt figure with no false precision: the honest statement is "meaningfully
#: less than linear, by an amount nobody here has measured".
ULTRAFUSION_EFFICIENCY = 0.90


@dataclass(frozen=True)
class ChipSpec:
    """Published Apple configuration for one part.

    Core counts and bus widths are Apple's own published figures, which is
    what makes this scaling possible at all — the ratios are facts, and only
    the per-core rate comes from our measurement.
    """

    key: str
    family: str
    gpu_cores: int
    performance_cores: int
    efficiency_cores: int
    memory_bandwidth_gbs: float
    max_memory_gb: float
    dies: int = 1
    actively_cooled: bool = True


#: The M2 generation. One GPU core design, replicated.
#:
#: The base M2 ships in 8- and 10-GPU-core variants. This entry carries 8
#: because that is what the machine behind `MEASURED_M2` actually reports, and
#: the anchor's core count is the divisor for every derived figure — taking 10
#: for an 8-core part would understate the per-core rate by a fifth and drag
#: the whole family down with it.
M2_FAMILY: dict[str, ChipSpec] = {
    "m2": ChipSpec("m2", "M2", 8, 4, 4, 100.0, 24.0, actively_cooled=False),
    "m2-pro": ChipSpec("m2-pro", "M2", 19, 8, 4, 200.0, 32.0),
    "m2-max": ChipSpec("m2-max", "M2", 38, 8, 4, 400.0, 96.0),
    "m2-ultra": ChipSpec("m2-ultra", "M2", 76, 16, 8, 800.0, 192.0, dies=2),
}


@dataclass(frozen=True)
class Measurement:
    """What was actually measured on the anchor part."""

    chip_key: str
    gpu_cores: int
    #: Dense GEMM, the arithmetic ceiling. Not a transformer's throughput.
    gemm_fp16_gflops: float
    #: Streaming read, achieved.
    memory_bandwidth_gbs: float
    #: The part's published bus, so achieved-fraction can be computed.
    spec_bandwidth_gbs: float
    runs: int = 3

    @property
    def gflops_per_gpu_core(self) -> float:
        return self.gemm_fp16_gflops / self.gpu_cores

    @property
    def bandwidth_efficiency(self) -> float:
        """Fraction of the theoretical bus this memory system delivers."""
        if self.spec_bandwidth_gbs <= 0:
            return 1.0
        return self.memory_bandwidth_gbs / self.spec_bandwidth_gbs


@dataclass(frozen=True)
class DerivedDevice:
    """A sibling part's figures, scaled from the anchor, with its reasoning."""

    key: str
    gemm_fp16_gflops: float
    memory_bandwidth_gbs: float
    max_memory_gb: float
    gpu_cores: int
    provenance: str
    anchor_key: str
    notes: tuple[str, ...] = ()

    def public_dict(self) -> dict:
        return {
            "key": self.key,
            "gemm_fp16_gflops": round(self.gemm_fp16_gflops, 1),
            "memory_bandwidth_gbs": round(self.memory_bandwidth_gbs, 1),
            "max_memory_gb": self.max_memory_gb,
            "gpu_cores": self.gpu_cores,
            "provenance": self.provenance,
            "derived_from": self.anchor_key,
            "notes": list(self.notes),
        }


class DerivationRefused(ValueError):
    """The two parts are not the same silicon, so no scaling is defensible."""


def derive(anchor: Measurement, target: ChipSpec,
           anchor_spec: ChipSpec) -> DerivedDevice:
    """Scale the anchor's measured rates to a sibling part.

    Refuses anything but a same-family scale. That refusal is the point: the
    licence to do this at all comes from Apple shipping one core design per
    generation, and it evaporates the moment the core changes.
    """
    if anchor_spec.family != target.family:
        raise DerivationRefused(
            f"cannot scale a {anchor_spec.family} measurement to "
            f"{target.family}: a different generation is a different GPU core, "
            f"so a per-core rate from one says nothing about the other. "
            f"Measure {target.family} directly, or leave it at catalogue "
            f"provenance.")
    if anchor.gpu_cores <= 0 or target.gpu_cores <= 0:
        raise DerivationRefused("a core count of zero cannot be scaled")

    notes: list[str] = []

    # Compute follows the cores, because they are the same cores.
    compute = anchor.gflops_per_gpu_core * target.gpu_cores
    if target.dies > 1:
        compute *= ULTRAFUSION_EFFICIENCY
        notes.append(
            f"{target.dies} dies over an interconnect, not one large die; "
            f"scaled at {ULTRAFUSION_EFFICIENCY:.0%} of linear because work "
            f"spanning both halves pays to cross. The real figure is "
            f"unmeasured.")

    # Bandwidth follows the bus, not the cores. What carries across is the
    # achieved fraction of theoretical, which is a property of the design.
    bandwidth = target.memory_bandwidth_gbs * anchor.bandwidth_efficiency
    notes.append(
        f"bandwidth scaled from the {target.memory_bandwidth_gbs:.0f} GB/s bus "
        f"at the {anchor.bandwidth_efficiency:.0%} achieved fraction measured "
        f"on {anchor.chip_key}, not from the core count — the bus and the "
        f"cores widen on different schedules.")

    if target.actively_cooled and not anchor_spec.actively_cooled:
        notes.append(
            f"the anchor was measured fanless while {target.key} is actively "
            f"cooled, so its sustained rate is likely higher than this. "
            f"Conservative rather than optimistic.")

    return DerivedDevice(
        key=target.key,
        gemm_fp16_gflops=compute,
        memory_bandwidth_gbs=bandwidth,
        max_memory_gb=target.max_memory_gb,
        gpu_cores=target.gpu_cores,
        provenance=DERIVED,
        anchor_key=anchor.chip_key,
        notes=tuple(notes),
    )


def derive_family(anchor: Measurement,
                  family: dict[str, ChipSpec] | None = None
                  ) -> dict[str, DerivedDevice]:
    """Every sibling of the anchor, scaled from it.

    The anchor itself is excluded: it was measured, and a derived figure must
    never sit on top of a measurement.
    """
    family = family or M2_FAMILY
    anchor_spec = family.get(anchor.chip_key)
    if anchor_spec is None:
        raise DerivationRefused(
            f"{anchor.chip_key} is not in the family table, so its siblings "
            f"cannot be identified")
    return {
        key: derive(anchor, spec, anchor_spec)
        for key, spec in family.items()
        if key != anchor.chip_key
    }


def best(*candidates: tuple[str, float | None]) -> tuple[str, float | None]:
    """Pick the best-sourced of several figures for the same quantity.

    ``candidates`` are ``(provenance, value)``. The measured one wins, then
    published, then derived. A value of ``None`` never wins whatever its label,
    because a well-sourced absence is still an absence.
    """
    usable = [(p, v) for p, v in candidates if v is not None]
    if not usable:
        return ("UNAVAILABLE", None)
    return min(usable, key=lambda pair: PROVENANCE_RANK.get(pair[0], 99))


#: The anchor this project actually has: three preflight-validated runs on the
#: local fanless M2, 0.3% spread. Recorded here so the derivation has a real
#: starting point rather than a placeholder, and so it is obvious what has to
#: be re-run if the measurement is ever superseded.
MEASURED_M2 = Measurement(
    chip_key="m2",
    # 8, not 10: local detection reports 8 CPU and 8 GPU cores on this part.
    # This is the divisor for the whole family, so it must be the count of the
    # machine that produced the number above, never the top configuration.
    gpu_cores=8,
    gemm_fp16_gflops=2583.2,
    memory_bandwidth_gbs=75.7,
    spec_bandwidth_gbs=100.0,
    runs=3,
)
