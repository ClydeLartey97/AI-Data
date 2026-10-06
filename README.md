# AI-Energy

Work on the energy cost and carbon intensity of AI compute.

## Projects

### [`grid-aware-scheduler/`](grid-aware-scheduler/)

A scheduler that decides **which hardware runs which job, where, and when**, by combining:

1. **Hardware efficiency** — accelerators have different power-to-throughput curves, so work is matched to the unit that suits it rather than treated as interchangeable.
2. **Grid timing and location** — using electricity price and carbon intensity per half-hour, flexible work shifts into cheaper, cleaner windows and toward cleaner regions. Deadline-bound work runs regardless.
3. **On-site generation** — where a facility has its own supply, work is placed against forecast usable renewable surplus, with grid import as the residual.

It is **market-agnostic** by construction: the scheduling logic never touches market-specific code. Each electricity market sits behind an adapter translating its API into one common format, so switching markets is a config change, not a code change.

#### What is built

- **Five market adapters**, all against official public sources and none needing an API key: GB (national price, 18 regional carbon zones), CAISO (nodal LMP), NYISO (11 zonal LBMP), MISO (8 hub LMP), ERCOT (hub and load-zone day-ahead settlement point prices). US carbon comes from EIA-930 and is labelled balancing-area scope — never presented as nodal. GB reads through a sibling data project that must be checked out beside this one; the four US markets need nothing else.
- **An exact placement engine.** It enumerates every feasible hardware/location/start combination under hard memory, deadline, capacity, cost and carbon constraints, applies PUE, and marks the cost/carbon Pareto frontier. No solver, no opaque model — the decision is auditable and runs in microseconds.
- **A multi-job portfolio scheduler** with workflow stage dependencies, checkpoint splitting and facility power limits.
- **A local operator product** — six linked pages plus a versioned JSON API, served by a local process bound to loopback. Every planning decision can be persisted with its complete decision-time signal snapshot and later scored against realised outturn.
- **Read-only hardware discovery.** Single-host detection, plus facility-scale Redfish inventory of operator-declared endpoints. Provenance is per field: discovery can prove identity, installed memory and an instantaneous power reading; it never promotes throughput, which requires repeated calibration runs.

#### Measured results

All from live data, not simulation:

- **GB, 6.5 kW job, 4 hours, 24-hour deadline:** £3.21 → £0.21 and 2.10 → 0.97 kgCO₂ — **93.6% cost and 54.0% carbon saved for an 11.5-hour delay.**
- **Across 392 days:** a 4-hour job saves a median 21.9% on cost at a 24-hour deadline, and 75% at a week. Over a full year GB price ranged £0.09–£560.81/MWh.
- **Location beats timing for carbon.** North Scotland at 0 gCO₂/kWh against South West England at 358, the same instant.
- **Cheap is not clean.** Price/carbon correlation is r = 0.54, and the cheapest decile is also the cleanest only 59% of the time — so the objective has to be stated, not inferred.

From a published production trace:

- **Long jobs hold almost all the GPU time.** In Microsoft's Philly DNN training trace (CC BY 4.0; Jeon et al., USENIX ATC 2019), 83,152 of the 117,325 recorded jobs completed with usable timestamps and GPU counts. Of those, 2,896 (3.5%) ran longer than 24 hours and held **90.9% of the 2,484,862 GPU-hours** — measured as runtime × GPUs per job, by `gpu_hour_concentration` in `core/trace_replay.py`. A job longer than its deadline has no window to move into, which is why time-shifting saves single-digit percentages on real training work and placement and location matter more.

#### What this is not

- **Not a novel category, and the prior art is real.** This implements and extends a pattern that already exists in public: [Compute Gardener](https://github.com/elevated-systems/compute-gardener-scheduler) is an active open-source Kubernetes scheduler already combining carbon/price-aware temporal shifting with hardware-power-aware placement, and Eco-Orchestrator/CARL demonstrates the combination academically on a 64-GPU testbed. Zeus and Perseus cover GPU energy optimisation; Google's carbon-intelligent computing covers temporal and spatial shifting. An earlier version of this file claimed these mechanisms "had not been combined in one open, runnable system" — **that claim was wrong and is retracted.**
- **Not a carbon credit generator.** It reduces energy cost and can improve hourly carbon matching. It does not create tradeable instruments.
- **Not production-ready.** It runs locally with no authentication, tenancy or deployment hardening. Generation profiles in the dispatch scenarios are estimated standard shapes, not plant telemetry, and are labelled as such on screen. Hardware throughput is measured: an Apple M2 was benchmarked across three validated runs (2,583 GFLOP/s fp16 GEMM, 75.7 GB/s streaming read, 0.3% spread), and the raw runs are in `grid-aware-scheduler/benchmarks/data/m2_baseline_runs.json`. Workload profiles are a separate store, and that one is still empty — a profile requires three valid repeated runs of a real workload, and none has been recorded yet.

#### Running it

```bash
python -m app.serve      # → http://localhost:8765, loopback only
```

592 tests run offline with no network access. See [`grid-aware-scheduler/docs/`](grid-aware-scheduler/docs/) for the exact planning equations, data contracts, calibration rules and the discovery boundary.

## Working on this

Start with [`grid-aware-scheduler/HANDOFF.md`](grid-aware-scheduler/HANDOFF.md). It is the single source of truth for project state, decisions made, prior art and what to do next — kept current at the end of every working session.

---

## Licence

© 2026 Clyde Lartey. All rights reserved.

This repository is public so the work can be read and evaluated. It is not open source: no part of it may be used, copied, modified or distributed without written permission. See [LICENSE](LICENSE).

Third-party files keep their own licences: the vendored KLineCharts bundle (`grid-aware-scheduler/app/static/vendor/`, Apache-2.0) and the DMTF Redfish mockup fixtures (`grid-aware-scheduler/tests/fixtures/redfish/`, BSD-3-Clause). The Philly trace used by the replay is not included; it is published by Microsoft under CC BY 4.0.
