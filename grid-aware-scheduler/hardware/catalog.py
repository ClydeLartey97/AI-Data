"""
Published hardware specifications.

**Nothing here is MEASURED**, and the rows are not even equally trustworthy.
NVIDIA, AMD and Intel entries are SPEC — vendor datasheet numbers, idealised,
gathered under favourable conditions and never reached by a real workload.
The Apple entries are weaker still: ESTIMATED, because Apple does not publish
GPU TFLOPS or per-component power at all, so those figures are community
benchmarks rather than anything official.

Both are useful because they let the simulator reason about hardware nobody
here owns. Both are dangerous if presented as what you would actually get.

Two rules this file follows, both of which exist to stop the simulator
flattering itself:

1. **Dense figures only.** Vendors headline sparse tensor throughput because
   it is double the dense number. Almost no real training run achieves it.
   Where a datasheet quotes "with sparsity", the dense half is recorded.
2. **Every device carries an ``mfu``** — the fraction of peak FLOPS a
   well-optimised run actually reaches. Datacentre parts on mature stacks sit
   around 0.40; consumer and unified-memory parts land lower because they are
   bandwidth-starved or less well served by kernels.

> **Verification status:** these were compiled from published vendor
> specifications and should be checked against current datasheets before any
> figure is quoted externally. Treat the shape of the comparison as sound and
> individual digits as provisional. Where a number was uncertain the
> conservative value was taken.
"""
from __future__ import annotations

from hardware.base import Device, Interconnect, Provenance

_NV = "NVIDIA"
_AMD = "AMD"
_INTEL = "Intel"
_APPLE = "Apple"


def _d(**kw) -> Device:
    kw.setdefault("provenance", Provenance.SPEC)
    return Device(**kw)


CATALOG: dict[str, Device] = {d.key: d for d in [
    # ---- NVIDIA datacentre -------------------------------------------------
    _d(key="h100-sxm", name="H100 SXM", vendor=_NV, kind="GPU",
       peak_tflops_bf16=989.0, memory_gb=80, memory_bandwidth_gbs=3350,
       tdp_watts=700, idle_watts=75, interconnect=Interconnect.NVLINK, mfu=0.42,
       source="NVIDIA H100 datasheet (BF16 tensor, dense)",
       notes="HBM3. NVLink 900 GB/s bidirectional."),
    _d(key="h100-pcie", name="H100 PCIe", vendor=_NV, kind="GPU",
       peak_tflops_bf16=756.0, memory_gb=80, memory_bandwidth_gbs=2000,
       tdp_watts=350, idle_watts=50, interconnect=Interconnect.PCIE, mfu=0.40,
       source="NVIDIA H100 datasheet (BF16 tensor, dense)",
       notes="Lower TDP than SXM; scales worse past a few devices."),
    _d(key="a100-80", name="A100 80GB SXM", vendor=_NV, kind="GPU",
       peak_tflops_bf16=312.0, memory_gb=80, memory_bandwidth_gbs=2039,
       tdp_watts=400, idle_watts=50, interconnect=Interconnect.NVLINK, mfu=0.45,
       source="NVIDIA A100 datasheet (BF16 tensor, dense)",
       notes="The best-characterised part in public literature — Zeus and "
             "Eco-Orchestrator both benchmark on A100s."),
    _d(key="l40s", name="L40S", vendor=_NV, kind="GPU",
       peak_tflops_bf16=181.0, memory_gb=48, memory_bandwidth_gbs=864,
       tdp_watts=350, idle_watts=40, interconnect=Interconnect.PCIE, mfu=0.35,
       source="NVIDIA L40S datasheet (BF16 dense; 362 is the sparse figure)"),
    _d(key="rtx4090", name="RTX 4090", vendor=_NV, kind="GPU",
       peak_tflops_bf16=165.0, memory_gb=24, memory_bandwidth_gbs=1008,
       tdp_watts=450, idle_watts=25, interconnect=Interconnect.PCIE, mfu=0.30,
       source="NVIDIA Ada datasheet (FP16/BF16 dense)",
       notes="Consumer. No NVLink, so multi-device scaling is poor."),

    # ---- AMD ---------------------------------------------------------------
    _d(key="mi300x", name="Instinct MI300X", vendor=_AMD, kind="GPU",
       peak_tflops_bf16=1307.0, memory_gb=192, memory_bandwidth_gbs=5300,
       tdp_watts=750, idle_watts=90, interconnect=Interconnect.PCIE, mfu=0.32,
       source="AMD MI300X datasheet (BF16 dense)",
       notes="Enormous memory — fits models that need multiple H100s. Lower "
             "MFU reflects a less mature software stack, not the silicon."),
    _d(key="mi250x", name="Instinct MI250X", vendor=_AMD, kind="GPU",
       peak_tflops_bf16=383.0, memory_gb=128, memory_bandwidth_gbs=3277,
       tdp_watts=560, idle_watts=80, interconnect=Interconnect.PCIE, mfu=0.30,
       source="AMD MI250X datasheet (BF16 dense)"),

    # ---- Intel -------------------------------------------------------------
    _d(key="gaudi3", name="Gaudi 3", vendor=_INTEL, kind="GPU",
       peak_tflops_bf16=1835.0, memory_gb=128, memory_bandwidth_gbs=3700,
       tdp_watts=900, idle_watts=100, interconnect=Interconnect.ETHERNET, mfu=0.28,
       source="Intel Gaudi 3 specifications (BF16)",
       notes="Scales over standard Ethernet rather than a proprietary fabric."),
    _d(key="arc-a770", name="Arc A770", vendor=_INTEL, kind="GPU",
       peak_tflops_bf16=39.0, memory_gb=16, memory_bandwidth_gbs=560,
       tdp_watts=225, idle_watts=20, interconnect=Interconnect.PCIE, mfu=0.22,
       source="Intel Arc A770 specifications (FP16)",
       notes="Consumer. Included because mixed consumer fleets are real."),

    # ---- Apple Silicon -----------------------------------------------------
    # HONESTY WARNING, different in kind from the entries above. Apple does
    # not publish GPU TFLOPS or per-component TDP at all. It publishes core
    # counts and (sometimes) memory bandwidth, and nothing else these figures
    # need. So the FLOPS and wattage below are COMMUNITY-DERIVED estimates
    # from third-party benchmarking, not vendor datasheet values, and they
    # deserve less confidence than the NVIDIA/AMD/Intel rows.
    #
    # This is precisely why auto-profiling matters most here, and why the
    # validation story has to differ for Apple — see HANDOFF.md, "Auto
    # hardware analysis". There is no datasheet to check against; the test is
    # whether per-core constants derived on one chip predict another.
    #
    # Unified memory: no inter-device transfer within one SoC, and the whole
    # package budget is far below a discrete GPU. Bandwidth, not FLOPS, is
    # usually the binding constraint here.
    _d(key="m2", name="M2 (8-core GPU)", vendor=_APPLE, kind="SoC",
       peak_tflops_bf16=3.6, memory_gb=8, memory_bandwidth_gbs=100,
       tdp_watts=20, idle_watts=2, interconnect=Interconnect.UNIFIED, mfu=0.20,
       provenance=Provenance.ESTIMATED,
       source="core counts and memory bandwidth from Apple; FLOPS and package "
              "power are community-benchmarked estimates, not published",
       notes="Memory is the hard limit long before compute is. 8 GB caps this "
             "to small models — which is exactly the machine available to "
             "benchmark on, so it is the first real calibration point."),
    _d(key="m3-max", name="M3 Max", vendor=_APPLE, kind="SoC",
       peak_tflops_bf16=14.0, memory_gb=128, memory_bandwidth_gbs=400,
       tdp_watts=78, idle_watts=5, interconnect=Interconnect.UNIFIED, mfu=0.22,
       provenance=Provenance.ESTIMATED,
       source="bandwidth published by Apple; FLOPS and power community-estimated"),
    _d(key="m2-ultra", name="M2 Ultra", vendor=_APPLE, kind="SoC",
       peak_tflops_bf16=27.0, memory_gb=192, memory_bandwidth_gbs=800,
       tdp_watts=100, idle_watts=8, interconnect=Interconnect.UNIFIED, mfu=0.22,
       provenance=Provenance.ESTIMATED,
       source="bandwidth published by Apple; FLOPS and power community-estimated",
       notes="192 GB unified memory holds models that need several discrete "
             "GPUs, at a fraction of the power — the interesting Apple case."),
]}


def get(key: str) -> Device:
    if key not in CATALOG:
        raise KeyError(f"unknown device {key!r}. Known: {', '.join(sorted(CATALOG))}")
    return CATALOG[key]


def by_vendor(vendor: str) -> list[Device]:
    return [d for d in CATALOG.values() if d.vendor.lower() == vendor.lower()]


def vendors() -> list[str]:
    return sorted({d.vendor for d in CATALOG.values()})
