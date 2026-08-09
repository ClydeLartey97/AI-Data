"""
Model workloads — how long a model takes on a fleet, and what it costs in energy.

This is a **first-order analytical model**, not a profiler. It uses the
standard published approximations, which are accurate enough to compare
configurations and nowhere near accurate enough to promise a wall-clock time.
Every estimate that leaves this module is ESTIMATED provenance for that reason.

The two approximations, both widely used and both citable:

**Training** — FLOPs ~= 6 x parameters x tokens. Six because a training step is
one forward pass (2 FLOPs per parameter per token, a multiply and an add) plus
a backward pass costing roughly twice the forward.

**Inference decode** — bound by memory bandwidth, not arithmetic. Generating
each token requires reading every weight once, so tokens/sec ~= memory
bandwidth / model bytes. This is why an Apple SoC with 800 GB/s of unified
memory can serve a large model respectably despite modest FLOPS, and why
adding compute to a decode-bound job buys nothing.

Getting that distinction right is the difference between a simulator that
tells you something and one that just multiplies TFLOPS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from hardware.base import Fleet, Group, Interconnect, Provenance

BYTES_PER_PARAM = {"fp32": 4, "fp16": 2, "bf16": 2, "fp8": 1, "int8": 1, "int4": 0.5}

#: Training memory multiplier over raw weights: gradients, optimiser moments
#: and activations. ~4x weights is the usual figure for mixed-precision Adam
#: without offload; it is the reason a model that "fits" cannot be trained.
TRAINING_MEMORY_MULTIPLIER = 4.0

#: Inference needs weights plus a KV cache and activation headroom.
INFERENCE_MEMORY_MULTIPLIER = 1.25


class Task(str, Enum):
    TRAINING = "training"
    INFERENCE = "inference"


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    params_b: float                 # billions
    precision: str = "bf16"
    source: str = ""

    @property
    def weight_bytes(self) -> float:
        return self.params_b * 1e9 * BYTES_PER_PARAM.get(self.precision, 2)

    @property
    def weight_gb(self) -> float:
        return self.weight_bytes / 1e9


#: A spread of sizes, chosen so the memory wall shows up: the 8B fits almost
#: anywhere, the 70B needs real memory, the 405B needs a fleet.
MODELS: dict[str, Model] = {m.key: m for m in [
    Model("llama-8b", "Llama-class 8B", 8.0, source="8B dense transformer"),
    Model("llama-70b", "Llama-class 70B", 70.0, source="70B dense transformer"),
    Model("llama-405b", "Llama-class 405B", 405.0, source="405B dense transformer"),
    Model("mistral-7b", "Mistral-class 7B", 7.3, source="7B dense transformer"),
    Model("phi-3-mini", "Phi-class 3.8B", 3.8, source="3.8B dense transformer"),
]}


@dataclass
class Job:
    """A unit of work to simulate."""

    name: str
    model: Model
    task: Task
    #: Training: total tokens to train on. Inference: total tokens to generate.
    tokens: float
    prompt_tokens: float = 0.0        # inference only, drives prefill
    deadline_hours: float | None = None
    flexible: bool = True

    @property
    def training_flops(self) -> float:
        return 6.0 * self.model.params_b * 1e9 * self.tokens

    @property
    def prefill_flops(self) -> float:
        return 2.0 * self.model.params_b * 1e9 * self.prompt_tokens


@dataclass
class GroupShare:
    """How much of a job one hardware group was given, and what it did."""

    group: Group
    fraction: float
    throughput: float          # effective TFLOPS or tokens/s, per the task
    active: bool = True


@dataclass
class Estimate:
    """The result of simulating a job on a fleet. Always ESTIMATED."""

    job: Job
    fleet: Fleet
    runtime_hours: float
    energy_kwh: float
    average_power_kw: float
    scaling_efficiency: float
    shares: list[GroupShare] = field(default_factory=list)
    fits: bool = True
    limit: str = ""
    assumptions: list[str] = field(default_factory=list)
    provenance: Provenance = Provenance.ESTIMATED

    @property
    def energy_per_token_j(self) -> float:
        return (self.energy_kwh * 3.6e6 / self.job.tokens) if self.job.tokens else 0.0


# --------------------------------------------------------------------------
# memory feasibility
# --------------------------------------------------------------------------

def memory_required_gb(job: Job) -> float:
    mult = (TRAINING_MEMORY_MULTIPLIER if job.task is Task.TRAINING
            else INFERENCE_MEMORY_MULTIPLIER)
    return job.model.weight_gb * mult


# --------------------------------------------------------------------------
# scaling
# --------------------------------------------------------------------------

def scaling_efficiency(fleet: Fleet, job: Job) -> float:
    """Fraction of linear scaling this fleet achieves on this job.

    Modelled from the actual communication cost rather than a fudge factor.
    Data-parallel training all-reduces the gradients every step, moving
    ``2(n-1)/n x parameter bytes`` over the interconnect. Set that against the
    compute time per step and you get the efficiency directly.

    Unified memory (a single Apple SoC) has no inter-device transfer at all,
    so it scales perfectly — there is nothing to scale.

    **Known limitation, do not oversell this.** Communication is only one of
    the reasons large runs scale badly. Pipeline bubbles, stragglers, load
    imbalance and failure recovery all cost real efficiency and none are
    modelled here, so at large device counts this function is optimistic — it
    reports ~99% where a real 32-GPU run might see 70-85%. It is sound for
    comparing configurations and should not be quoted as a scaling prediction.
    Replacing it with measured scaling curves is the natural follow-on to the
    auto-profiling work (see HANDOFF.md).
    """
    n = fleet.count
    if n <= 1 or fleet.interconnect is Interconnect.UNIFIED:
        return 1.0

    link_gbs = fleet.link_gbs
    if link_gbs <= 0:
        return 1.0

    # One step's worth of work, spread over the fleet.
    step_tokens = 2e6  # a representative global batch in tokens
    step_flops = 6.0 * job.model.params_b * 1e9 * step_tokens
    compute_s = step_flops / (fleet.achievable_tflops * 1e12)

    allreduce_bytes = 2.0 * (n - 1) / n * job.model.weight_bytes
    comm_s = allreduce_bytes / (link_gbs * 1e9)

    return compute_s / (compute_s + comm_s) if (compute_s + comm_s) else 1.0


# --------------------------------------------------------------------------
# estimation
# --------------------------------------------------------------------------

def estimate(job: Job, fleet: Fleet) -> Estimate:
    """Simulate ``job`` on ``fleet``."""
    assumptions: list[str] = []

    need = memory_required_gb(job)
    have = fleet.total_memory_gb
    fits = have >= need
    limit = "" if fits else (
        f"needs ~{need:,.0f} GB, fleet has {have:,.0f} GB")
    if not fits:
        assumptions.append(
            f"Does not fit: {job.task.value} of a {job.model.params_b:g}B model at "
            f"{job.model.precision} needs about {need:,.0f} GB "
            f"({'weights x4 for gradients, optimiser and activations' if job.task is Task.TRAINING else 'weights plus KV cache'})."
        )

    if job.task is Task.TRAINING:
        est = _estimate_training(job, fleet, assumptions)
    else:
        est = _estimate_inference(job, fleet, assumptions)

    est.fits, est.limit = fits, limit
    return est


def _split_by_throughput(fleet: Fleet, capability) -> list[GroupShare]:
    """Give each group work in proportion to what it can actually do.

    This is the core of hardware-aware allocation. Split a job evenly across
    unequal devices and the slowest sets the finish time while the fastest sit
    part-idle burning power. Split by throughput and every group finishes at
    the same moment.
    """
    caps = [capability(g) for g in fleet.groups]
    total = sum(caps)
    if total <= 0:
        return [GroupShare(g, 0.0, 0.0, active=False) for g in fleet.groups]
    return [GroupShare(g, c / total, c, active=c > 0)
            for g, c in zip(fleet.groups, caps)]


def _estimate_training(job: Job, fleet: Fleet, assumptions: list[str]) -> Estimate:
    shares = _split_by_throughput(fleet, lambda g: g.achievable_tflops)
    eff = scaling_efficiency(fleet, job)

    effective_tflops = fleet.achievable_tflops * eff
    runtime_h = (job.training_flops / (effective_tflops * 1e12)) / 3600 if effective_tflops else float("inf")

    power_kw = fleet.power_watts(1.0) / 1000.0
    assumptions += [
        "Training FLOPs approximated as 6 x parameters x tokens.",
        f"Devices assumed to reach their model-FLOPs-utilisation figure "
        f"({', '.join(f'{g.device.name} {g.device.mfu:.0%}' for g in fleet.groups)}), not peak.",
        f"Multi-device scaling {eff:.0%} of linear, from gradient all-reduce over "
        f"{fleet.interconnect.value}." if fleet.count > 1 else "Single device — no scaling loss.",
        "Sustained near-peak power for the whole run, which training does.",
    ]
    if fleet.is_heterogeneous:
        assumptions.append("Work split in proportion to each group's achievable throughput, "
                           "so all groups finish together.")

    return Estimate(job=job, fleet=fleet, runtime_hours=runtime_h,
                    energy_kwh=power_kw * runtime_h, average_power_kw=power_kw,
                    scaling_efficiency=eff, shares=shares, assumptions=assumptions)


def _estimate_inference(job: Job, fleet: Fleet, assumptions: list[str]) -> Estimate:
    # Decode is bandwidth-bound: every generated token reads all weights once.
    def tokens_per_s(g: Group) -> float:
        bytes_per_token = job.model.weight_bytes
        return (g.device.memory_bandwidth_gbs * 1e9 * g.count) / bytes_per_token

    shares = _split_by_throughput(fleet, tokens_per_s)
    total_tps = sum(s.throughput for s in shares)

    decode_s = job.tokens / total_tps if total_tps else float("inf")
    prefill_s = (job.prefill_flops / (fleet.achievable_tflops * 1e12)
                 if fleet.achievable_tflops else 0.0)
    runtime_h = (decode_s + prefill_s) / 3600

    # Decode leaves arithmetic units idle, so the fleet does not draw its
    # training-time power. 60% is a conservative published mid-point.
    utilisation = 0.6
    power_kw = fleet.power_watts(utilisation) / 1000.0

    assumptions += [
        "Decode modelled as memory-bandwidth-bound: tokens/sec = bandwidth / model bytes. "
        "Adding compute to a decode-bound job buys nothing.",
        f"Prefill modelled as 2 x parameters x prompt tokens ({prefill_s:.1f}s of the run).",
        f"Power at {utilisation:.0%} of TDP — decode leaves arithmetic units idle, "
        "unlike training.",
    ]

    return Estimate(job=job, fleet=fleet, runtime_hours=runtime_h,
                    energy_kwh=power_kw * runtime_h, average_power_kw=power_kw,
                    scaling_efficiency=1.0, shares=shares, assumptions=assumptions)
