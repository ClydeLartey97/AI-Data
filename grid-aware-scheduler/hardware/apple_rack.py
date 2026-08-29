"""Model a rack of Apple-silicon compute boards from one measured chip.

**Why this module exists.** Apple now builds AI servers out of its own silicon,
and the reported layout is a rack-mount chassis holding many small,
individually removable compute boards — each carrying one SoC and its own
memory — rather than a few large accelerators. That architecture is the same
one this project already has a measurement of. A base M-series SoC on a
laptop logic board and a base M-series SoC on a server carrier board run the
same cores against the same memory system.

The consequence is the point of the whole module: **a fleet built from a part
you can hold is a fleet you can characterise without physical access to it.**
Measure one chip properly, apply the published core and bus ratios, apply an
explicit and stated scaling law for replicating boards, and you have a
defensible estimate of what a rack does per kilowatt. No datacentre visit, no
vendor briefing, no NDA. That is a genuinely unusual position, and it exists
only because the retail part and the server part are the same silicon — which
is not true for anyone shipping H100s.

**Three rules keep this honest, and each one is enforced rather than noted.**

*Boards do not share memory.* This is the single most important property of
the architecture and the one most likely to be got wrong by analogy with GPU
racks. Eight H200s on NVLink present as one 1.1 TB pool; sixty-four Apple
boards on a backplane do not. A model that does not fit in **one board's**
memory does not run, however many boards the chassis holds. `plan` refuses
rather than quietly aggregating memory, because aggregating it would turn a
physically impossible deployment into an attractive-looking number.

*Throughput replicates only for independent work.* This project already
measured the justification: across 83 multi-accelerator MLPerf curves,
inference scaling is near-linear because serving independent requests needs no
gradient synchronisation. So N boards serve about N times the requests. That
licence does **not** extend to splitting one model across boards, where the
backplane becomes the bottleneck and nobody here has measured it. `plan`
serves the first case and refuses the second.

*Throughput here is GPU-scope.* Every board figure traces back to an MLX dense
GEMM, which runs on the GPU. Apple silicon also carries a Neural Engine, which
is central to how Apple actually serves inference and which nothing here
measures. A rack's real serving throughput could therefore differ from these
numbers in either direction, and `plan` says so on every result rather than
letting a GPU ceiling read as whole-device capability.

*Chassis geometry is declared, never assumed.* Board count, per-board memory
and power come from the operator. `REPORTED_CHASSIS` records a published
figure as one worked example with its source attached, and it is a starting
point for a conversation, not a datasheet. Nothing in this module treats it as
fact.

What comes out is deliberately shaped for an energy question rather than a
procurement one: sustained tokens per second, kilowatts drawn, and **tokens
per kilowatt-hour** — the quantity that matters when the binding constraint on
an AI facility is its grid connection rather than its capital budget. That
figure is what connects this module to the rest of the project: a rack's power
draw is a facility load, and `core/portfolio.py` already schedules facility
load against price, carbon and on-site generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hardware import derive
from hardware.base import Provenance
from hardware.roofline import DeviceCapability, Prediction, Workload, predict

#: Scaling efficiency for replicating independent inference across boards.
#:
#: Not a guess. This project parsed 83 multi-accelerator MLPerf Inference
#: curves and measured a median efficiency of 100.6% at largest scale, ranging
#: 70–139%, because inference serving is embarrassingly parallel across
#: independent requests. Figures above 100% reflect larger submissions being
#: better tuned rather than any hardware effect, so linear is the honest
#: ceiling and this sits just below it to avoid claiming the superlinear tail.
INDEPENDENT_SCALING_EFFICIENCY = 0.98

#: Everything in the chassis that is not a compute board: fans, power
#: conversion, the backplane and the controller. Expressed as a multiplier on
#: aggregate board power. Declared by the operator; the default is an
#: explicitly ESTIMATED placeholder, and `plan` labels any result that used it.
DEFAULT_CHASSIS_OVERHEAD = 1.15


class RackRefused(ValueError):
    """The deployment as described cannot physically run the work."""


@dataclass(frozen=True)
class BoardSpec:
    """One compute board: an SoC, its own memory, and its own power budget.

    ``memory_gb`` is the hard ceiling for any single model on this
    architecture. It is not pooled with any other board and must not be
    presented as though it were.
    """

    name: str
    memory_gb: float
    memory_bandwidth_gbs: float
    achievable_tflops: float
    board_watts: float
    compute_provenance: str = Provenance.ESTIMATED.value
    bandwidth_provenance: str = Provenance.ESTIMATED.value
    power_provenance: str = Provenance.ESTIMATED.value

    def capability(self) -> DeviceCapability:
        """The roofline view of this board, as a single-accelerator device."""
        return DeviceCapability(
            name=self.name,
            memory_gb=self.memory_gb,
            memory_bandwidth_gbs=self.memory_bandwidth_gbs,
            achievable_tflops=self.achievable_tflops,
            bandwidth_provenance=self.bandwidth_provenance,
            compute_provenance=self.compute_provenance,
            accelerators=1,
        )


@dataclass(frozen=True)
class ChassisSpec:
    """How many boards sit in one chassis, and what the chassis costs to run."""

    name: str
    boards: int
    overhead_multiplier: float = DEFAULT_CHASSIS_OVERHEAD
    #: Where the geometry came from, in plain words. Carried into every result
    #: so a reported figure never reads as a measured one.
    source: str = "operator declared"

    def __post_init__(self) -> None:
        if self.boards < 1:
            raise ValueError("a chassis holds at least one board")
        if self.overhead_multiplier < 1.0:
            raise ValueError(
                "chassis overhead cannot be below 1.0: fans, power conversion "
                "and the backplane add draw, they never subtract it")


#: One published example, recorded with its provenance attached.
#:
#: A press report on Apple's AI server hardware described a rack-mount chassis
#: holding up to 32 individually removable compute boards, cooled by heatsinks
#: down the centre with fans at both ends. That board count is the only figure
#: taken from it. Per-board memory, per-board power and the overhead
#: multiplier were **not** reported and are not guessed here — a caller
#: supplies them or gets no answer.
REPORTED_CHASSIS = ChassisSpec(
    name="32-board rack chassis",
    boards=32,
    source="press report of Apple AI server hardware, August 2026; board "
           "count only. Memory, power and overhead were not reported.",
)


@dataclass
class RackPlan:
    """What a described rack does with a described workload."""

    workload: str
    board: str
    chassis: str
    boards: int
    fits_per_board: bool
    memory_required_gb: float
    memory_per_board_gb: float
    #: Aggregate sustained decode throughput across every board.
    tokens_per_second: float | None = None
    board_tokens_per_second: float | None = None
    compute_kw: float | None = None
    total_kw: float | None = None
    tokens_per_kwh: float | None = None
    provenance: str = Provenance.ESTIMATED.value
    board_prediction: Prediction | None = None
    notes: list = field(default_factory=list)

    def public_dict(self) -> dict:
        return {
            "workload": self.workload,
            "board": self.board,
            "chassis": self.chassis,
            "boards": self.boards,
            "fits_per_board": self.fits_per_board,
            "memory_required_gb": round(self.memory_required_gb, 2),
            "memory_per_board_gb": self.memory_per_board_gb,
            "tokens_per_second": (None if self.tokens_per_second is None
                                  else round(self.tokens_per_second, 1)),
            "total_kw": (None if self.total_kw is None
                         else round(self.total_kw, 3)),
            "tokens_per_kwh": (None if self.tokens_per_kwh is None
                               else round(self.tokens_per_kwh, 1)),
            "provenance": self.provenance,
            "notes": list(self.notes),
        }


def plan(workload: Workload, board: BoardSpec, chassis: ChassisSpec,
         *, scaling_efficiency: float = INDEPENDENT_SCALING_EFFICIENCY,
         independent_requests: bool = True) -> RackPlan:
    """Estimate what a chassis of these boards does with this workload.

    ``independent_requests`` is the honesty switch. Left true, this models many
    separate requests served concurrently, which is what a Private Cloud
    Compute-style fleet actually does and what the MLPerf scaling evidence
    supports. Set it false — meaning one model split across boards — and the
    call is refused, because the backplane cost is unmeasured and a number
    produced here would be indistinguishable from a guess.
    """
    if not independent_requests:
        raise RackRefused(
            "splitting one model across boards is not modelled. These boards "
            "do not share memory, so the work would cross the backplane every "
            "layer, and no measurement of that path exists here. Model "
            "independent requests, or measure the interconnect first.")
    if not 0 < scaling_efficiency <= 1.0:
        raise ValueError("scaling efficiency must be above 0 and at most 1")

    board_prediction = predict(workload, board.capability())
    notes: list[str] = []

    if not board_prediction.fits:
        # The defining constraint of the architecture. Refusing here is the
        # whole point: aggregating 32 boards' memory would turn a deployment
        # that cannot physically run into an attractive-looking number.
        notes.append(
            f"{workload.name} needs "
            f"{board_prediction.memory_required_gb:.1f} GB but one board holds "
            f"{board.memory_gb:.0f} GB. Boards do not share memory, so the "
            f"chassis total of "
            f"{board.memory_gb * chassis.boards:.0f} GB is not available to a "
            f"single model and this workload cannot run on this board at all. "
            f"Use a smaller model, a narrower weight width, or a board with "
            f"more memory.")
        return RackPlan(
            workload=workload.name,
            board=board.name,
            chassis=chassis.name,
            boards=chassis.boards,
            fits_per_board=False,
            memory_required_gb=board_prediction.memory_required_gb,
            memory_per_board_gb=board.memory_gb,
            provenance=board_prediction.provenance,
            board_prediction=board_prediction,
            notes=notes,
        )

    per_board = board_prediction.decode_tokens_per_second
    aggregate = None
    if per_board is not None:
        aggregate = per_board * chassis.boards * scaling_efficiency
        notes.append(
            f"{chassis.boards} boards at {scaling_efficiency:.0%} scaling. "
            f"Independent requests only — inference needs no gradient "
            f"synchronisation, which is why this replicates near-linearly "
            f"where distributed training would not.")

    compute_kw = board.board_watts * chassis.boards / 1000
    total_kw = compute_kw * chassis.overhead_multiplier
    notes.append(
        f"{compute_kw:.2f} kW of boards, {total_kw:.2f} kW at the chassis "
        f"after a {chassis.overhead_multiplier:.2f}x overhead for fans, power "
        f"conversion and the backplane.")

    tokens_per_kwh = None
    if aggregate is not None and total_kw > 0:
        # Tokens per kWh, which is the figure an energy team can act on: it
        # converts directly into cost and emissions once a price and a carbon
        # intensity are attached, which the rest of this project supplies.
        tokens_per_kwh = aggregate * 3600 / total_kw

    provenance = _weakest(board_prediction.provenance, board.power_provenance)
    if chassis.overhead_multiplier == DEFAULT_CHASSIS_OVERHEAD:
        notes.append(
            "chassis overhead is the module default, not a measured or "
            "declared figure; the power result is no better than ESTIMATED "
            "until an operator supplies the real one.")
    notes.append(f"chassis geometry: {chassis.source}")
    notes.append(
        "throughput is GPU-scope: it descends from a dense GEMM on the GPU. "
        "The Neural Engine is not measured or included, and Apple serves real "
        "inference on it, so treat this as the GPU's ceiling rather than the "
        "board's total serving capacity.")

    return RackPlan(
        workload=workload.name,
        board=board.name,
        chassis=chassis.name,
        boards=chassis.boards,
        fits_per_board=True,
        memory_required_gb=board_prediction.memory_required_gb,
        memory_per_board_gb=board.memory_gb,
        tokens_per_second=aggregate,
        board_tokens_per_second=per_board,
        compute_kw=compute_kw,
        total_kw=total_kw,
        tokens_per_kwh=tokens_per_kwh,
        provenance=provenance,
        board_prediction=board_prediction,
        notes=notes,
    )


def board_from_projection(projected: derive.DerivedDevice, *,
                          board_watts: float,
                          power_provenance: str = Provenance.ESTIMATED.value,
                          memory_gb: float | None = None) -> BoardSpec:
    """Turn a derived or projected chip into a board this module can plan with.

    ``memory_gb`` overrides the part's retail maximum, and usually should. A
    server board is populated for its role rather than for a configurator's
    top option, and reported Apple AI server boards carry noticeably less
    memory than the equivalent desktop part. Since per-board memory is the
    binding constraint of the whole architecture, taking the retail maximum by
    default would be the single most flattering wrong assumption available.
    """
    return BoardSpec(
        name=projected.key,
        memory_gb=memory_gb if memory_gb is not None else projected.max_memory_gb,
        memory_bandwidth_gbs=projected.memory_bandwidth_gbs,
        # GEMM GFLOP/s is a dense arithmetic ceiling; roofline expects an
        # achievable TFLOPS figure and applies its own efficiency on top.
        achievable_tflops=projected.gemm_fp16_gflops / 1000,
        board_watts=board_watts,
        compute_provenance=projected.provenance,
        bandwidth_provenance=projected.provenance,
        power_provenance=power_provenance,
    )


def _weakest(*provenances: str) -> str:
    """A rack figure is only as good as the worst input behind it."""
    return max(provenances,
               key=lambda p: derive.PROVENANCE_RANK.get(p, 99))
