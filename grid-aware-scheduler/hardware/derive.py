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

#: Weaker than DERIVED, and separated from it because the licence is different.
#: DERIVED scales a measured *per-core rate* to a sibling built from the same
#: core. PROJECTED crosses a generation boundary, where that licence does not
#: exist: an M5 core is not an M2 core, so no per-core rate travels. What
#: travels instead is the *achieved fraction* of theoretical peak — how much of
#: the silicon a mature software stack actually extracts — which is a property
#: of the toolchain rather than of the core. It is a real, useful thing to
#: carry forward, and it is still weaker than measuring the part.
PROJECTED = "PROJECTED"

#: Priority order for any figure about a device. The rule the whole project
#: turns on: **a real measurement always wins.** Anything derived is a fallback
#: for parts nobody has measured or published, never an override.
PROVENANCE_RANK = {
    "MEASURED": 0,
    "PUBLISHED": 1,
    DERIVED: 2,
    PROJECTED: 3,
    "CONTRACTED": 4,
    "SPEC": 5,
    "ESTIMATED": 6,
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

#: Interconnect derate **per crossing**. Deliberately a single blunt figure
#: with no false precision: the honest statement is "meaningfully less than
#: linear, by an amount nobody here has measured".
ULTRAFUSION_EFFICIENCY = 0.90


def interconnect_hops(dies: int) -> int:
    """Worst-case UltraFusion crossings for a package of ``dies`` dies.

    This exists because Apple went past two dies. Up to and including the M4
    generation an "Ultra" was two dies joined by one UltraFusion bridge, and a
    single flat derate described it. The M5 Ultra is announced as a **quad-die**
    part — two dual-die Max chips joined together — so it has a hierarchy: one
    crossing inside each Max, and a second between them.

    Treating a four-die part as though it paid the two-die penalty is the bug
    this function removes. It was not a latent style problem: a flat 0.90 on a
    quad-die package overstates its dense throughput by about 11%, and does so
    silently, on exactly the part someone would reach for to model a large
    Apple-silicon deployment.

    The hop count is ``log2(dies)`` because the packaging is hierarchical
    rather than a daisy chain: doubling the dies adds one level, not one link.
    """
    if dies < 1:
        raise ValueError("a package has at least one die")
    hops = 0
    remaining = dies
    while remaining > 1:
        remaining = (remaining + 1) // 2
        hops += 1
    return hops


def multi_die_efficiency(dies: int) -> float:
    """Compounded interconnect derate for a package of ``dies`` dies.

    One unmeasured constant applied twice is a weaker claim than the same
    constant applied once, and `derive` says so in its notes rather than
    presenting a quad-die figure with the confidence of a two-die one.
    """
    return ULTRAFUSION_EFFICIENCY ** interconnect_hops(dies)


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
    #: The part's published dense fp16 peak, for the same reason. Needed to
    #: state what fraction of the silicon the toolchain actually reaches,
    #: which is the only quantity that survives a generation change.
    spec_peak_gflops: float | None = None
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

    @property
    def compute_efficiency(self) -> float | None:
        """Fraction of the published dense peak the toolchain actually reaches.

        Not the same thing as MFU. This is dense GEMM against the vendor's
        arithmetic peak — the ceiling — while MFU measures a whole transformer
        against that peak and is far lower. Conflating them is the error
        `hardware/scan.py` already warns about.
        """
        if not self.spec_peak_gflops or self.spec_peak_gflops <= 0:
            return None
        return self.gemm_fp16_gflops / self.spec_peak_gflops


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
        hops = interconnect_hops(target.dies)
        efficiency = multi_die_efficiency(target.dies)
        compute *= efficiency
        notes.append(
            f"{target.dies} dies over an interconnect, not one large die; "
            f"{hops} worst-case crossing{'s' if hops > 1 else ''} at "
            f"{ULTRAFUSION_EFFICIENCY:.0%} each gives {efficiency:.0%} of "
            f"linear, because work spanning the package pays to cross. The "
            f"per-crossing figure is unmeasured.")
        if hops > 1:
            notes.append(
                f"a {target.dies}-die package applies that unmeasured constant "
                f"{hops} times over, so this figure carries materially wider "
                f"uncertainty than a two-die one. Treat it as a magnitude "
                f"check, not a procurement number.")

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


@dataclass(frozen=True)
class PublishedPart:
    """A part described only by the vendor's own published figures.

    Used for silicon nobody here has run and nobody has submitted to a
    benchmark — a new generation, or a server part that is not sold retail.
    Every field is the vendor's claim, which is why what comes out the other
    side of `project` is labelled PROJECTED rather than DERIVED.
    """

    key: str
    family: str
    #: Published dense fp16 peak. Leave ``None`` and the projection refuses
    #: rather than inventing one.
    spec_peak_gflops: float | None
    memory_bandwidth_gbs: float | None
    max_memory_gb: float | None
    gpu_cores: int | None = None
    dies: int = 1
    actively_cooled: bool = True
    #: Where these figures came from, in the operator's own words. Recorded so
    #: a press announcement never reads like a datasheet.
    source: str = "vendor published"


def project(anchor: Measurement, target: PublishedPart) -> DerivedDevice:
    """Carry the anchor's *achieved fractions* onto a part from another generation.

    This is the bridge `derive` refuses to build, and the distinction matters.
    `derive` scales a measured per-core rate to a sibling built from the same
    core, which is legitimate because the core is literally the same design.
    Across a generation that is false — an M5 core is not an M2 core — so the
    rate cannot travel.

    What can travel is the **fraction of the published peak the software stack
    actually reaches**. That is a property of the compiler, the kernels and the
    memory system's real behaviour rather than of any one core, and it is the
    thing a vendor's peak figure never tells you. We measured roughly 90% of
    dense fp16 peak and 76% of the published bus. Applying those fractions to
    a newer part's published numbers gives a far better estimate than taking
    its peak at face value, and a far worse one than measuring it.

    **The honest limit, stated because it is the obvious objection.** This
    assumes the toolchain extracts the new silicon about as well as it extracts
    the measured silicon. For a mature architecture continuing along the same
    line that is reasonable. For a genuinely new unit — a redesigned matrix
    engine, a new Neural Engine generation — it is not, and the result is a
    magnitude check rather than a ranking. A projection is never allowed to
    outrank a measurement or a published benchmark result; `PROVENANCE_RANK`
    enforces that.
    """
    if target.spec_peak_gflops is None and target.memory_bandwidth_gbs is None:
        raise DerivationRefused(
            f"{target.key} publishes neither a peak nor a bus width, so there "
            f"is nothing to apply an achieved fraction to. Leave it "
            f"UNAVAILABLE rather than inventing a figure.")

    efficiency = anchor.compute_efficiency
    if efficiency is None:
        raise DerivationRefused(
            f"the anchor {anchor.chip_key} has no published peak recorded, so "
            f"its achieved fraction is unknown and nothing can be projected "
            f"from it")

    notes: list[str] = [
        f"projected across a generation boundary: {anchor.chip_key} "
        f"({anchor.gemm_fp16_gflops:.0f} GFLOP/s measured over {anchor.runs} "
        f"runs) reaches {efficiency:.0%} of its published peak and "
        f"{anchor.bandwidth_efficiency:.0%} of its published bus. Those "
        f"fractions are applied to {target.key}'s published figures; no "
        f"per-core rate crosses the boundary.",
        f"{target.key} figures sourced as: {target.source}.",
    ]

    compute = None
    if target.spec_peak_gflops is not None:
        compute = target.spec_peak_gflops * efficiency
        if target.dies > 1:
            hops = interconnect_hops(target.dies)
            compute *= multi_die_efficiency(target.dies)
            notes.append(
                f"{target.dies}-die package: {hops} worst-case crossing"
                f"{'s' if hops > 1 else ''} applied on top of the projection.")

    bandwidth = None
    if target.memory_bandwidth_gbs is not None:
        bandwidth = target.memory_bandwidth_gbs * anchor.bandwidth_efficiency

    if target.family != anchor_family(anchor):
        notes.append(
            f"a new generation may extract its silicon better or worse than "
            f"the {anchor_family(anchor)} stack did. Treat this as a magnitude "
            f"check until {target.key} is measured or published.")

    return DerivedDevice(
        key=target.key,
        gemm_fp16_gflops=compute if compute is not None else 0.0,
        memory_bandwidth_gbs=bandwidth if bandwidth is not None else 0.0,
        max_memory_gb=target.max_memory_gb or 0.0,
        gpu_cores=target.gpu_cores or 0,
        provenance=PROJECTED,
        anchor_key=anchor.chip_key,
        notes=tuple(notes),
    )


def anchor_family(anchor: Measurement) -> str:
    """The generation the anchor belongs to, from the family table."""
    spec = M2_FAMILY.get(anchor.chip_key)
    return spec.family if spec else anchor.chip_key.upper()


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
    # The 8-core part's published dense fp16 peak. The catalogue's 3.6 TFLOPS
    # is the 10-core configuration, so 0.36 TFLOPS per core times 8. Comparing
    # the measurement against the 10-core figure is what produced the "72% of
    # peak" recorded earlier in this project; against the part actually
    # measured the achieved fraction is about 90%.
    spec_peak_gflops=2880.0,
    runs=3,
)
