"""
Hardware catalogue — accelerators, memory and interconnect.

Dense BF16/FP16 tensor throughput only. Vendors headline sparse figures
because they are double; almost nothing achieves them. Where a datasheet
quotes "with sparsity", the dense half is recorded.

Every device carries an ``mfu`` — the fraction of peak a well-optimised run
reaches. Datacentre parts on mature stacks sit near 0.40-0.45; consumer parts,
newer silicon with immature kernels, and bandwidth-starved unified memory land
lower. Peak FLOPS is not a throughput anyone gets, and treating it as one is
the fastest way to build a simulator that flatters itself.

**Memory type is recorded because it decides inference.** Decode reads every
weight per generated token, so tokens/sec tracks bandwidth, not arithmetic.
That is why an HBM3e part with modest FLOPS out-serves a GDDR6 part with more,
and why LPDDR5X unified memory competes at all. The ladder, roughly:
HBM3e > HBM3 > HBM2e > GDDR7 > GDDR6X > GDDR6 > LPDDR5X > DDR5.

> **Verification status:** compiled from published vendor specifications.
> Check against current datasheets before quoting any figure externally.
> Treat the shape of the comparison as sound and individual digits as
> provisional; where uncertain the conservative value was taken. Apple rows
> are ESTIMATED rather than SPEC because Apple publishes neither GPU
> throughput nor per-component power.
"""
from __future__ import annotations

from hardware.base import Device, Interconnect, Provenance

_NV, _AMD, _INTEL, _APPLE, _GOOGLE, _AWS = (
    "NVIDIA", "AMD", "Intel", "Apple", "Google", "AWS")

NVL, PCIE, ETH, UNI = (Interconnect.NVLINK, Interconnect.PCIE,
                       Interconnect.ETHERNET, Interconnect.UNIFIED)


def _d(key, name, vendor, kind, tflops, mem, bw, tdp, idle, link, mfu,
       mem_type, tier, source="", notes="", prov=Provenance.SPEC):
    d = Device(key=key, name=name, vendor=vendor, kind=kind,
               peak_tflops_bf16=tflops, memory_gb=mem, memory_bandwidth_gbs=bw,
               tdp_watts=tdp, idle_watts=idle, interconnect=link, mfu=mfu,
               provenance=prov, source=source, notes=notes)
    d.memory_type = mem_type   # type: ignore[attr-defined]
    d.tier = tier              # type: ignore[attr-defined]
    return d


_DEVICES = [
    # ---- NVIDIA Blackwell -------------------------------------------------
    _d("b200", "B200", _NV, "GPU", 2250, 192, 8000, 1000, 120, NVL, 0.40,
       "HBM3e", "Datacentre", "NVIDIA Blackwell (BF16 dense)"),
    _d("b100", "B100", _NV, "GPU", 1800, 192, 8000, 700, 100, NVL, 0.40,
       "HBM3e", "Datacentre", "NVIDIA Blackwell (BF16 dense)"),
    # ---- NVIDIA Hopper ----------------------------------------------------
    _d("h200-sxm", "H200 SXM", _NV, "GPU", 989, 141, 4800, 700, 80, NVL, 0.42,
       "HBM3e", "Datacentre", "NVIDIA H200 datasheet",
       "Same compute as H100 SXM with far more memory and bandwidth — an "
       "inference part more than a training one."),
    _d("h100-sxm", "H100 SXM", _NV, "GPU", 989, 80, 3350, 700, 75, NVL, 0.42,
       "HBM3", "Datacentre", "NVIDIA H100 datasheet"),
    _d("h100-nvl", "H100 NVL", _NV, "GPU", 835, 94, 3900, 400, 60, NVL, 0.41,
       "HBM3", "Datacentre", "NVIDIA H100 NVL datasheet"),
    _d("h100-pcie", "H100 PCIe", _NV, "GPU", 756, 80, 2000, 350, 50, PCIE, 0.40,
       "HBM3", "Datacentre", "NVIDIA H100 datasheet"),
    # ---- NVIDIA Ampere and earlier ---------------------------------------
    _d("a100-80-sxm", "A100 80GB SXM", _NV, "GPU", 312, 80, 2039, 400, 50, NVL, 0.45,
       "HBM2e", "Datacentre", "NVIDIA A100 datasheet",
       "Best-characterised part in public literature — Zeus and "
       "Eco-Orchestrator both benchmark on A100s."),
    _d("a100-80-pcie", "A100 80GB PCIe", _NV, "GPU", 312, 80, 1935, 300, 45, PCIE, 0.43,
       "HBM2e", "Datacentre", "NVIDIA A100 datasheet"),
    _d("a100-40-sxm", "A100 40GB SXM", _NV, "GPU", 312, 40, 1555, 400, 50, NVL, 0.45,
       "HBM2", "Datacentre", "NVIDIA A100 datasheet"),
    _d("v100-sxm2", "V100 SXM2", _NV, "GPU", 125, 32, 900, 300, 40, NVL, 0.38,
       "HBM2", "Datacentre", "NVIDIA V100 datasheet (FP16 tensor)"),
    _d("a30", "A30", _NV, "GPU", 165, 24, 933, 165, 30, PCIE, 0.36,
       "HBM2", "Datacentre", "NVIDIA A30 datasheet"),
    _d("l4", "L4", _NV, "GPU", 121, 24, 300, 72, 12, PCIE, 0.32,
       "GDDR6", "Datacentre", "NVIDIA L4 datasheet (BF16 dense)",
       "Bandwidth-starved: strong FLOPS per watt, weak decode throughput."),
    _d("t4", "T4", _NV, "GPU", 65, 16, 320, 70, 12, PCIE, 0.30,
       "GDDR6", "Datacentre", "NVIDIA T4 datasheet (FP16)",
       "Very low power, still ubiquitous for small-model inference."),
    # ---- NVIDIA professional ---------------------------------------------
    _d("l40s", "L40S", _NV, "GPU", 181, 48, 864, 350, 40, PCIE, 0.35,
       "GDDR6", "Professional", "NVIDIA L40S datasheet (BF16 dense)"),
    _d("l40", "L40", _NV, "GPU", 181, 48, 864, 300, 38, PCIE, 0.34,
       "GDDR6", "Professional", "NVIDIA L40 datasheet"),
    _d("rtx6000-ada", "RTX 6000 Ada", _NV, "GPU", 91, 48, 960, 300, 30, PCIE, 0.32,
       "GDDR6", "Professional", "NVIDIA RTX 6000 Ada datasheet"),
    _d("a6000", "RTX A6000", _NV, "GPU", 77, 48, 768, 300, 30, PCIE, 0.30,
       "GDDR6", "Professional", "NVIDIA A6000 datasheet"),
    _d("a40", "A40", _NV, "GPU", 150, 48, 696, 300, 32, PCIE, 0.31,
       "GDDR6", "Professional", "NVIDIA A40 datasheet"),
    # ---- NVIDIA consumer --------------------------------------------------
    _d("rtx5090", "RTX 5090", _NV, "GPU", 209, 32, 1792, 575, 30, PCIE, 0.30,
       "GDDR7", "Consumer", "NVIDIA Blackwell consumer (FP16 dense)",
       "GDDR7 gives it near-datacentre bandwidth on a consumer board."),
    _d("rtx4090", "RTX 4090", _NV, "GPU", 165, 24, 1008, 450, 25, PCIE, 0.30,
       "GDDR6X", "Consumer", "NVIDIA Ada datasheet (FP16 dense)",
       "No NVLink — multi-device scaling is poor."),
    _d("rtx4080s", "RTX 4080 Super", _NV, "GPU", 104, 16, 736, 320, 22, PCIE, 0.28,
       "GDDR6X", "Consumer", "NVIDIA Ada datasheet"),
    _d("rtx3090", "RTX 3090", _NV, "GPU", 71, 24, 936, 350, 25, PCIE, 0.26,
       "GDDR6X", "Consumer", "NVIDIA Ampere datasheet"),
    _d("rtx3080", "RTX 3080", _NV, "GPU", 60, 10, 760, 320, 22, PCIE, 0.25,
       "GDDR6X", "Consumer", "NVIDIA Ampere datasheet",
       "10 GB caps it to small models regardless of speed."),

    # ---- AMD --------------------------------------------------------------
    _d("mi325x", "Instinct MI325X", _AMD, "GPU", 1307, 256, 6000, 1000, 110, PCIE, 0.32,
       "HBM3e", "Datacentre", "AMD MI325X specifications",
       "256 GB — the largest single-device memory here."),
    _d("mi300x", "Instinct MI300X", _AMD, "GPU", 1307, 192, 5300, 750, 90, PCIE, 0.32,
       "HBM3", "Datacentre", "AMD MI300X datasheet",
       "Lower MFU reflects a less mature software stack, not the silicon."),
    _d("mi300a", "Instinct MI300A", _AMD, "APU", 980, 128, 5300, 760, 90, PCIE, 0.30,
       "HBM3", "Datacentre", "AMD MI300A datasheet", "CPU and GPU on one package."),
    _d("mi250x", "Instinct MI250X", _AMD, "GPU", 383, 128, 3277, 560, 80, PCIE, 0.30,
       "HBM2e", "Datacentre", "AMD MI250X datasheet"),
    _d("mi210", "Instinct MI210", _AMD, "GPU", 181, 64, 1638, 300, 45, PCIE, 0.28,
       "HBM2e", "Datacentre", "AMD MI210 datasheet"),
    _d("w7900", "Radeon Pro W7900", _AMD, "GPU", 123, 48, 864, 295, 28, PCIE, 0.24,
       "GDDR6", "Professional", "AMD W7900 specifications"),
    _d("rx7900xtx", "Radeon RX 7900 XTX", _AMD, "GPU", 123, 24, 960, 355, 25, PCIE, 0.22,
       "GDDR6", "Consumer", "AMD RDNA3 specifications"),

    # ---- Intel ------------------------------------------------------------
    _d("gaudi3", "Gaudi 3", _INTEL, "GPU", 1835, 128, 3700, 900, 100, ETH, 0.28,
       "HBM2e", "Datacentre", "Intel Gaudi 3 specifications",
       "Scales over standard Ethernet rather than a proprietary fabric."),
    _d("gaudi2", "Gaudi 2", _INTEL, "GPU", 865, 96, 2450, 600, 80, ETH, 0.27,
       "HBM2e", "Datacentre", "Intel Gaudi 2 specifications"),
    _d("max1550", "Data Center GPU Max 1550", _INTEL, "GPU", 832, 128, 3276, 600, 80, PCIE, 0.25,
       "HBM2e", "Datacentre", "Intel Max series specifications"),
    _d("max1100", "Data Center GPU Max 1100", _INTEL, "GPU", 362, 48, 1229, 300, 45, PCIE, 0.24,
       "HBM2e", "Datacentre", "Intel Max series specifications"),
    _d("arc-a770", "Arc A770", _INTEL, "GPU", 39, 16, 560, 225, 20, PCIE, 0.22,
       "GDDR6", "Consumer", "Intel Arc A770 specifications"),
    _d("arc-b580", "Arc B580", _INTEL, "GPU", 46, 12, 456, 190, 18, PCIE, 0.22,
       "GDDR6", "Consumer", "Intel Battlemage specifications"),

    # ---- Google TPU -------------------------------------------------------
    _d("tpu-v6e", "TPU v6e (Trillium)", _GOOGLE, "TPU", 918, 32, 1640, 300, 50, ETH, 0.45,
       "HBM", "Datacentre", "Google Cloud TPU specifications",
       "TPUs reach high MFU — the stack is co-designed with the silicon."),
    _d("tpu-v5p", "TPU v5p", _GOOGLE, "TPU", 459, 95, 2765, 350, 55, ETH, 0.45,
       "HBM2e", "Datacentre", "Google Cloud TPU specifications"),
    _d("tpu-v5e", "TPU v5e", _GOOGLE, "TPU", 197, 16, 819, 170, 30, ETH, 0.43,
       "HBM2e", "Datacentre", "Google Cloud TPU specifications"),
    _d("tpu-v4", "TPU v4", _GOOGLE, "TPU", 275, 32, 1200, 200, 40, ETH, 0.44,
       "HBM2", "Datacentre", "Google Cloud TPU specifications"),

    # ---- AWS silicon ------------------------------------------------------
    _d("trainium2", "Trainium2", _AWS, "ASIC", 667, 96, 2900, 500, 70, ETH, 0.38,
       "HBM3", "Datacentre", "AWS Trainium2 specifications"),
    _d("inferentia2", "Inferentia2", _AWS, "ASIC", 190, 32, 820, 175, 30, ETH, 0.35,
       "HBM2e", "Datacentre", "AWS Inferentia2 specifications"),

    # ---- Apple Silicon ----------------------------------------------------
    # Apple publishes core counts and sometimes memory bandwidth. It publishes
    # neither GPU throughput nor per-component power, so these are
    # community-benchmarked estimates, marked ESTIMATED. This is precisely why
    # auto-profiling matters most here — see HANDOFF.md.
    _d("m1", "M1", _APPLE, "SoC", 2.6, 16, 68, 20, 2, UNI, 0.18,
       "LPDDR4X", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m1-pro", "M1 Pro", _APPLE, "SoC", 5.2, 32, 200, 30, 3, UNI, 0.19,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m1-max", "M1 Max", _APPLE, "SoC", 10.4, 64, 400, 60, 4, UNI, 0.20,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m1-ultra", "M1 Ultra", _APPLE, "SoC", 21, 128, 800, 90, 7, UNI, 0.20,
       "LPDDR5", "Workstation", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m2", "M2", _APPLE, "SoC", 3.6, 24, 100, 20, 2, UNI, 0.20,
       "LPDDR5", "Consumer", "community benchmarks",
       "Memory is the hard limit long before compute is.", Provenance.ESTIMATED),
    _d("m2-pro", "M2 Pro", _APPLE, "SoC", 6.8, 32, 200, 32, 3, UNI, 0.21,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m2-max", "M2 Max", _APPLE, "SoC", 13.6, 96, 400, 62, 4, UNI, 0.21,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m2-ultra", "M2 Ultra", _APPLE, "SoC", 27.2, 192, 800, 100, 8, UNI, 0.22,
       "LPDDR5", "Workstation", "community benchmarks",
       "192 GB unified memory holds models needing several discrete GPUs, at a "
       "fraction of the power — the interesting Apple case.", Provenance.ESTIMATED),
    _d("m3", "M3", _APPLE, "SoC", 4.1, 24, 100, 22, 2, UNI, 0.21,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m3-max", "M3 Max", _APPLE, "SoC", 14.2, 128, 400, 78, 5, UNI, 0.22,
       "LPDDR5", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m4", "M4", _APPLE, "SoC", 4.6, 32, 120, 22, 2, UNI, 0.22,
       "LPDDR5X", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m4-pro", "M4 Pro", _APPLE, "SoC", 9.2, 64, 273, 45, 3, UNI, 0.23,
       "LPDDR5X", "Consumer", "community benchmarks", prov=Provenance.ESTIMATED),
    _d("m4-max", "M4 Max", _APPLE, "SoC", 18.4, 128, 546, 90, 5, UNI, 0.23,
       "LPDDR5X", "Workstation", "community benchmarks", prov=Provenance.ESTIMATED),

    # ---- CPUs -------------------------------------------------------------
    # Orders of magnitude slower for this work, included so that is visible
    # rather than assumed. Note the memory: they fit anything and crawl.
    _d("epyc-9754", "EPYC 9754 (128c)", _AMD, "CPU", 10, 768, 460, 360, 90, PCIE, 0.15,
       "DDR5", "Server", "AMD EPYC Bergamo specifications",
       "Huge memory, little compute — fits any model, runs it slowly."),
    _d("xeon-8592", "Xeon Platinum 8592+", _INTEL, "CPU", 14, 512, 358, 350, 85, PCIE, 0.15,
       "DDR5", "Server", "Intel Emerald Rapids specifications",
       "AMX gives usable BF16 throughput, still far below any GPU."),
]

CATALOGUE: dict[str, Device] = {d.key: d for d in _DEVICES}

MEMORY_TYPES = ["HBM3e", "HBM3", "HBM2e", "HBM2", "HBM", "GDDR7", "GDDR6X",
                "GDDR6", "LPDDR5X", "LPDDR5", "LPDDR4X", "DDR5"]

TIERS = ["Datacentre", "Professional", "Workstation", "Consumer", "Server"]


def get(key: str) -> Device:
    if key not in CATALOGUE:
        raise KeyError(f"unknown device {key!r}. Known: {', '.join(sorted(CATALOGUE))}")
    return CATALOGUE[key]


def by_vendor(vendor: str) -> list[Device]:
    return [d for d in CATALOGUE.values() if d.vendor.lower() == vendor.lower()]


def vendors() -> list[str]:
    seen, out = set(), []
    for d in CATALOGUE.values():
        if d.vendor not in seen:
            seen.add(d.vendor)
            out.append(d.vendor)
    return out
