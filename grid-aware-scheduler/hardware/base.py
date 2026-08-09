"""
Hardware model — what a compute device is, and where its numbers came from.

The provenance field is the point of this module. A simulator that shows an
H100 you do not own, running a model you have not run, at a throughput nobody
measured, is only honest if every number says which of those it is. So each
figure carries a ``Provenance``, and the UI is expected to show it.

    MEASURED   this machine, benchmarked, real watts off powermetrics/NVML
    SPEC       a published vendor or benchmark figure, with a source
    ESTIMATED  derived by a model in this codebase from the two above
    SIMULATED  invented for a scenario — a fleet that does not exist

The distinction that matters most: vendor peak FLOPS is a number no real
workload reaches. Treating it as achievable throughput is the single easiest
way to produce a simulator that flatters itself. Everything here goes through
an efficiency factor for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Provenance(str, Enum):
    MEASURED = "MEASURED"
    SPEC = "SPEC"
    ESTIMATED = "ESTIMATED"
    SIMULATED = "SIMULATED"


class Interconnect(str, Enum):
    """How devices in a group talk to each other.

    This is the difference between 8 GPUs behaving like 8 and behaving like 5.
    """

    NVLINK = "NVLink"          # ~450-900 GB/s per device, in-node
    PCIE = "PCIe"              # ~32-64 GB/s, in-node
    ETHERNET = "Ethernet"      # ~12.5-50 GB/s, across nodes
    UNIFIED = "Unified memory"  # single SoC — no inter-device transfer at all
    NONE = "None"              # single device


#: Effective bidirectional bandwidth per device, GB/s. Deliberately
#: conservative: these are achievable figures, not marketing peaks.
INTERCONNECT_GBS = {
    Interconnect.NVLINK: 450.0,
    Interconnect.PCIE: 50.0,
    Interconnect.ETHERNET: 25.0,
    Interconnect.UNIFIED: 0.0,
    Interconnect.NONE: 0.0,
}


@dataclass
class Device:
    """One accelerator, as the simulator understands it.

    FLOPS figures are dense BF16/FP16 tensor throughput unless stated. Sparse
    figures — which vendors like to quote because they are double — are never
    used here: almost no real training run achieves them.
    """

    key: str
    name: str
    vendor: str
    kind: str                        # "GPU" | "SoC" | "CPU"
    peak_tflops_bf16: float          # dense, not sparse
    memory_gb: float
    memory_bandwidth_gbs: float
    tdp_watts: float                 # sustained board/package power under load
    idle_watts: float
    interconnect: Interconnect
    provenance: Provenance = Provenance.SPEC
    source: str = ""
    notes: str = ""
    #: Fraction of peak FLOPS a well-optimised training run actually reaches
    #: (model FLOPs utilisation). 0.35-0.5 is the published range for large
    #: transformer training on datacentre GPUs; consumer and unified-memory
    #: parts land lower because they are bandwidth-starved.
    mfu: float = 0.40

    @property
    def achievable_tflops(self) -> float:
        return self.peak_tflops_bf16 * self.mfu

    @property
    def tflops_per_watt(self) -> float:
        return self.achievable_tflops / self.tdp_watts if self.tdp_watts else 0.0

    @property
    def gb_per_watt(self) -> float:
        return self.memory_bandwidth_gbs / self.tdp_watts if self.tdp_watts else 0.0


@dataclass
class Group:
    """N identical devices — one homogeneous block within a fleet."""

    device: Device
    count: int = 1

    @property
    def peak_tflops(self) -> float:
        return self.device.peak_tflops_bf16 * self.count

    @property
    def achievable_tflops(self) -> float:
        return self.device.achievable_tflops * self.count

    @property
    def memory_gb(self) -> float:
        return self.device.memory_gb * self.count

    def power_watts(self, utilisation: float = 1.0) -> float:
        d = self.device
        per_device = d.idle_watts + (d.tdp_watts - d.idle_watts) * _clamp(utilisation)
        return per_device * self.count


@dataclass
class Fleet:
    """A heterogeneous fleet — the shape real estates actually have.

    "3 NVIDIA, 2 Intel Arc, 1 AMD" is a legitimate fleet and is modelled
    directly, because mixing vendors is exactly where naive schedulers fail:
    split work evenly across unequal devices and the slowest one sets the
    finish time while the fast ones idle at part load, burning power for
    nothing. Splitting *by throughput* is the whole point of hardware-aware
    allocation, and it cannot be demonstrated on a uniform fleet.
    """

    groups: list[Group]
    interconnect: Interconnect = Interconnect.PCIE
    provenance: Provenance = Provenance.SIMULATED
    label: str = ""

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("a fleet needs at least one group of devices")
        if self.count == 1:
            self.interconnect = Interconnect.NONE
        if not self.label:
            self.label = " + ".join(f"{g.count}x {g.device.name}" for g in self.groups)

    @classmethod
    def of(cls, device: Device, count: int = 1, **kw) -> "Fleet":
        """Convenience for the common homogeneous case."""
        interconnect = kw.pop("interconnect", None) or (
            Interconnect.NONE if count == 1 else device.interconnect)
        return cls([Group(device, count)], interconnect=interconnect, **kw)

    @property
    def count(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def devices(self) -> list[Device]:
        return [g.device for g in self.groups]

    @property
    def is_heterogeneous(self) -> bool:
        return len({g.device.key for g in self.groups}) > 1

    @property
    def total_memory_gb(self) -> float:
        return sum(g.memory_gb for g in self.groups)

    @property
    def peak_tflops(self) -> float:
        return sum(g.peak_tflops for g in self.groups)

    @property
    def achievable_tflops(self) -> float:
        return sum(g.achievable_tflops for g in self.groups)

    @property
    def link_gbs(self) -> float:
        return INTERCONNECT_GBS.get(self.interconnect, 0.0)

    @property
    def max_power_watts(self) -> float:
        return self.power_watts(1.0)

    def power_watts(self, utilisation: float = 1.0) -> float:
        """Fleet draw at a uniform utilisation.

        Linear between idle and TDP. Real device curves are convex, so this
        over-states power at low utilisation — stated rather than silently
        assumed, and least wrong in the regime that matters (a training run
        pinned near 100%).
        """
        return sum(g.power_watts(utilisation) for g in self.groups)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass
class Figure:
    """A number that knows where it came from."""

    value: float
    provenance: Provenance
    unit: str = ""
    source: str = ""
    assumptions: list[str] = field(default_factory=list)
