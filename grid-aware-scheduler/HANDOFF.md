# HANDOFF.md — Read this first

**Purpose of this file:** if you are an AI assistant opening this folder for the first time, this document tells you everything you need to know to continue the work with no other context. Read this fully before touching any code.

**Protocol:** whichever tool is used in a working session, at the end of that session, update the "Current State" and "Next Steps" sections below, AND append a dated entry to "Session Log" (never edit or delete past log entries). Do not let this file go stale. It is the single source of truth for the project, not the conversation history in any one chat.

**"Catch up" protocol:** when Clyde says "catch up" (in any AI tool), that means: read this entire file, then read the most recent entries in "Session Log" until you understand what has changed since the last session, then summarise back in 2-4 sentences what state the project is in and what you'd do next — before taking any other action or asking what to do.

**Writing rules for this repo — this repository is PUBLIC. Apply these to every file, including this one:**
- **Never name an employer, client, or any internal/commercial system.** This project stands on its own and has no relation to any employer's work.
- **Never name a specific AI tool or vendor**, in docs or in commit metadata. No AI assistant is credited as an author or co-author of any commit. Session Log entries record *what changed*, dated — not which product made the change.
- Keep prior-art framing honest (see "What this is explicitly NOT").

---

## Project summary

A scheduler that decides which GPU runs which job, and when, based on two combined factors:

1. **Hardware efficiency profile** — different GPU types have different power-to-throughput curves. The scheduler allocates jobs to the GPU best suited to the job's power/performance profile rather than treating all GPUs as interchangeable.
2. **Grid signal timing** — using live or forecast electricity price and/or carbon intensity data, the scheduler shifts flexible (non-urgent) workloads to run when the grid is cheaper or cleaner. Urgent jobs run immediately regardless of grid state.

The system must be **market-agnostic**: the scheduling logic never touches market-specific code directly. Each electricity market (GB, CAISO, ERCOT, etc.) has its own adapter that translates that market's API into one common data format. Switching markets should be a one-line config change, not a code change.

## What this is explicitly NOT

- **Not a carbon credit generator.** Carbon credits/RECs are certified instruments issued by accredited bodies against verified, additional emissions reductions. This software does not create tradeable credits. It reduces actual energy cost and can improve hourly carbon-matching metrics. Never describe it as generating credits in any write-up or pitch.
- **Not a novel category.** It is an applied synthesis of existing published mechanisms (see Prior Art below). Present it honestly as a combined implementation, not an invention.

## Prior art (read before building — do not reinvent unknowingly)

| System | What it does | Source |
|---|---|---|
| Zeus (NSDI '23) | Optimises GPU power limit + batch size to minimise training energy | https://www.usenix.org/system/files/nsdi23-you.pdf |
| Perseus (PyTorch/SymbioticLab) | Extends Zeus to large-scale/pipeline training | https://arxiv.org/pdf/2312.06902 |
| Google Carbon-Intelligent Computing | Shifts compute timing/location to match grid carbon intensity | https://arxiv.org/pdf/2106.11750 |
| GridCensus / LandGate / REST | Data centre *siting* tools (different problem — where to build, not how to run) | gridcensus.com |
| **Compute Gardener** (open source, active, 2025-2026) | A real Kubernetes scheduler plugin doing almost exactly this project's two pillars combined: carbon + time-of-use-price-aware temporal shifting (pre-filter stage) AND hardware power-profile-aware node placement, incl. GPU workload classification (filter stage). Uses Electricity Maps API for grid data, NVIDIA DCGM + Kepler for hardware power estimation. Founder (Dave Masselink) is actively seeking production validation partners as of Aug 2025. | https://github.com/elevated-systems/compute-gardener-scheduler , https://compute-gardener.com |
| **Eco-Orchestrator / CARL** (academic, 2025-2026) | Combines carbon-aware job placement (grid forecasts) with hardware optimisation (automated DVFS on NVIDIA A100s) via a reinforcement-learning scheduler. Tested on a 64-A100 Kubernetes testbed; reported 34.7% carbon reduction. | found via arXiv/search, exact paper not yet pinned down |
| Green Software Foundation Carbon-Aware SDK, Azure `carbon-aware-keda-operator`, Kepler (K8s power exporter) | Mature, widely-adopted open-source building blocks for carbon-aware infra — not a full scheduler on their own but the standard plumbing everyone in this space uses | greensoftware.foundation, github.com/Azure/carbon-aware-keda-operator, sustainable-computing.io |

**Correction to the gap claim (2026-08-09):** the original claim — "nobody has done both together openly" — is **not accurate** and must not be repeated in any write-up or pitch. Compute Gardener already ships both pillars (grid-signal timing + hardware-aware placement) as an open-source, actively-developed project, and Eco-Orchestrator/CARL demonstrates the same combination academically at real GPU-cluster scale (64 A100s). This space is more mature and more validated than initially scoped. See "Where the real gap might still be" below for what's left.

## Where the real gap might still be (revised 2026-08-09)

Given the above, straight prior art re-implementation is not a credible pitch. What still looks open, and should be validated (not assumed) before claiming it:

- **On-device / Apple Silicon**: everything found so far (Compute Gardener, Eco-Orchestrator, Zeus/Perseus) targets Kubernetes clusters or discrete NVIDIA GPU fleets. Nobody found yet targets heterogeneous on-device compute (CPU P/E-core, GPU, ANE) paired with consumer time-of-use tariffs. See "Possible platform target: Apple Silicon companion demo" above.
- **Live hardware auto-detection + empirical benchmarking** rather than relying on DCGM/Kepler power estimates or hand-curated specs — worth checking directly against what Compute Gardener's "hardware power profiling" actually does before claiming this as new; it may already do something similar.
- Honest framing going forward: this project should be positioned as *implementing and extending the Compute Gardener / Eco-Orchestrator pattern*, ideally with a genuinely uncovered angle (Apple Silicon), not as filling a gap nobody has touched. Worth actually reading Compute Gardener's source/docs before writing any more scheduler code, to avoid rebuilding it blind.

## Architecture decisions already made

- **Adapter pattern**: one `MarketAdapter` interface. Each market (GB, CAISO, ERCOT) gets its own adapter file translating that market's API into a common format: a timestamped series of `{timestamp, carbon_intensity, price}`.
- **Scheduler logic is fully decoupled** from any market-specific code. It only ever sees the common data format.
- **GPU hardware profiles** are a separate input entirely — real published power/throughput specs, not invented numbers.
- **Build order**: get ONE market (GB) working end to end first. Do not build all three adapters before the core pipeline works. Add CAISO/ERCOT only once the interface is proven.

## Current state

*(Update this section at the end of every session — replace this line with what was actually done)*

- **Now a git repository** (initialised 2026-08-09). The National Grid Tool is deliberately NOT part of it — see "External dependency" below.
- **Environment set up and working on Clyde's Mac.** Python 3.12.9 venv at `~/venvs/national-grid` (outside the project folder, per the National Grid Tool README's own convention), installed from that project's `requirements.txt`. Run anything in this project with `~/venvs/national-grid/bin/python`.
- **`gb_adapter.py` is now VERIFIED LIVE**, not just against fixtures. Real run for 2026-08-01 → 2026-08-07 returned 289 `GridDataPoint`s with 0 missing prices; carbon 31–186 gCO2/kWh, and (see bug below) real day-ahead price £2.84–£158.94/MWh. The carbon/price join holds against live data.
- **Speed measured:** ~0.3s per day of data fetched (7 days = 2.14s). 92% of that is the Carbon Intensity endpoint, which is one HTTP call *per day*, vs prices which chunk 5 days per call. A full year is therefore ~2 minutes of live fetching. Fine once, wasteful on every scheduler iteration — see next steps.
- **KNOWN BUG, deliberately NOT fixed yet** (will be handled in the real implementation rather than patched into the current adapter): `gb_adapter.get_data()` averages Market Index price across data providers, but only two exist and **`N2EXMIDP` publishes structural zeros** — 100% zero in Aug/Jun/Feb 2026, 99.6% zero in Nov 2025. So every price the scheduler currently sees is **exactly half the real price** (measured mean £57.44 vs true APX mean £115.00). The level error alone understates savings 2×; worse, the Nov 2025 case means the scale factor is 0.5 for most periods and something else for a few, which distorts the *shape* of the price curve — and shape is the entire input to a "which half-hour is cheapest" decision. **Fix when rebuilding: prefer `APXMIDP`, or drop non-positive prices before averaging. Do not average blindly.** This resolves open question #2 below.
- `adapters/base_adapter.py` done — `MarketAdapter` interface + `GridDataPoint` dataclass.
- `adapters/gb_adapter.py` is now a REAL implementation, not a stub. It imports a sibling project, `AI Energy/National-Grid-Tool` (Clyde's own earlier GB power-market data project — 28 fetchers, SQLite+Parquet warehouse), and calls two of its clients directly: `sources/carbon_intensity/client.py` (Carbon Intensity API, public/keyless) and `sources/elexon/prices.py` (Elexon Insights Market Index Data, public/keyless).
- Signal choice made (not yet validated on live data): uses `forecast_gco2_kwh` (not settled `actual`) and day-ahead Market Index price (not settlement system price), because a real scheduler only ever has forward-looking data when it decides — see the docstring at the top of `gb_adapter.py` for full reasoning. This is a judgment call, open to revisiting.
- Join logic (settlement_date + settlement_period, averaging price across multiple Market Index providers) was verified offline against the National Grid Tool's own real test fixtures (`tests/fixtures/carbon_intensity_date.json`, `tests/fixtures/mid_2026-07-13.json`) — mechanically correct.
- NOT yet verified: an actual live network call. That session's sandbox proxy blocked both `api.carbonintensity.org.uk` and `data.elexon.co.uk` (403 on CONNECT), so a real end-to-end run has to happen on Clyde's own machine, where the National Grid Tool already runs fine against these same hosts.
- No GPU profile data collected yet. No scheduler logic written yet.

## Next steps (in order)

1. [x] Build `adapters/base_adapter.py` — the common interface every market adapter implements
2. [x] Build `adapters/gb_adapter.py` — pull GB carbon intensity (National Grid ESO / Carbon Intensity API) and day-ahead price data — **DONE and now VERIFIED LIVE on Clyde's Mac** (2026-08-09). Works end to end.
3. [x] Confirm the common data format works by printing one week of GB data — done, 289 points, join holds. Plot still outstanding, but the format is proven. Note the price bug in Current State before trusting any £ figure.
3b. [ ] Route the adapter through the National Grid Tool's warehouse (`warehouse/store.py`) with fetch-on-miss, instead of hitting live APIs on every call. Reuses the layer that already solves this, makes repeat backtests instant, and unblocks pulling settlement actuals (`disebsp`, already in that project's `DATASETS` registry) for scoring realised performance.
4. [ ] Build a hardware detection + profiling module (replaces the static `gpu_profiles.csv` idea — see "Hardware detection" below for reasoning)
5. [ ] Build `scheduler/scheduler.py` — the naive baseline first (run everything immediately, max power)
6. [ ] Build the actual grid-aware + hardware-aware allocation logic
7. [ ] Produce one comparison chart: naive baseline vs. scheduler, on real GB data
8. [ ] Only then: build `adapters/caiso_adapter.py` and `adapters/ercot_adapter.py`, reusing the same interface
9. [ ] Write the half-page explainer in `docs/` citing prior art honestly

## Folder structure

```
grid-aware-scheduler/
├── HANDOFF.md          ← this file, always read first
├── adapters/            ← one file per market, all implementing the same interface
├── scheduler/           ← core scheduling logic, market-agnostic
├── data/                ← cached pulled data, GPU spec sheets
├── docs/                ← prior art notes, the final write-up
└── notebooks/           ← exploratory analysis, charts
```

**External dependency: the National Grid Tool.** `adapters/gb_adapter.py` imports from a sibling folder, `AI Energy/National-Grid-Tool` — Clyde's own earlier GB power-market data project. It serves **two** purposes here, and they are different things:

1. **A runtime dependency.** The adapter imports its `carbon_intensity` and `elexon.prices` clients via `sys.path` at runtime (see top of `gb_adapter.py`; override with the `NATIONAL_GRID_TOOL_PATH` env var). If that folder moves or is renamed, `gb_adapter.py` breaks until the path is updated.
2. **The architectural reference this project is modelled on.** See "Architecture we're borrowing" below.

**It is deliberately NOT part of this git repo** — not vendored, not copied, not a submodule. It is a separate production tool with its own lifecycle. Do not commit it here, and do not rename or strip it down: this project imports it at runtime by that exact folder name, and removing parts of it buys this repo nothing since it was never going to be committed anyway.

**Setup on a fresh machine:** create the venv from *that* project's requirements, at the location its README specifies —
```bash
python3 -m venv ~/venvs/national-grid
~/venvs/national-grid/bin/python -m pip install -r "../National-Grid-Tool/requirements.txt"
```
then run everything in this project with `~/venvs/national-grid/bin/python`. (Note: pip currently resolves pandas 3.0.x, which breaks 3 date-parsing tests in that project's news/weather feeds. Harmless here — nothing in the carbon or price path is affected, and all of those tests pass.)

## Architecture we're borrowing from the National Grid Tool

Worth understanding before writing more adapters, because it's why `gb_adapter.py` is only ~130 lines:

- **The settlement-period grid is the universal join key.** 48 half-hourly periods per day. All ~28 fetchers in that project are forced to land on the same `settlement_date` + `settlement_period` + `start_time` index, which is why two totally unrelated APIs (Carbon Intensity and Elexon) merge on a two-column join with no dtype fight. `sources/elexon/_frames.py::to_frame()` is the enforcer — it snake_cases keys, coerces timestamps to UTC-aware, and pins `settlement_date` to a plain `date`.
- **Layering:** HTTP client (retry on 429/5xx/timeouts only, typed errors) → fetchers returning tidy frames against a declared column constant → `warehouse/store.py` → UI. Each layer only knows the one below it.
- **The warehouse is one API over two backends**, routed by table name: SQLite for settlement-keyed feeds, Parquet+DuckDB for the high-volume per-BM-unit feeds (~10GB vs ~400GB). Callers never know which answers. Writes are idempotent by delete-then-append on `settlement_date`.
- **Why this matters for us:** `GridDataPoint` is just a narrowed projection of a contract that already existed, and the settlement period is a natural scheduling decision slot. The calculation falls straight out — for a job drawing `P` kW over one period: `energy_kWh = P × 0.5`, `cost = energy_kWh × price_£MWh / 1000`, `carbon_g = energy_kWh × intensity_gCO2kWh`. A deadline-constrained flexible job is then a sliding-window argmin over a few hundred floats. No solver, no ML — which is exactly the transparent classical approach argued for in Open Questions.

## Hardware detection (replaces static GPU profile CSV)

Decided 2026-08-09: instead of hand-curating a CSV of published GPU specs, the profiling module should auto-detect whatever compute units are actually present on the machine it's running on, then either match against known specs or empirically benchmark on the fly. This is more credible than trusting published TDP numbers (which are idealized) and works on hardware nobody's pre-catalogued.

Two separate steps, don't conflate them:
- **Detection** (easy): enumerate what's present. NVIDIA/cloud → `nvidia-smi` / NVML (`pynvml`) lists every GPU + live stats. Apple Silicon → `system_profiler`, `sysctl` (`hw.perflevel0/1` for P-core/E-core counts), Metal's `MTLDevice` API for GPU.
- **Profiling** (harder, more valuable): measure real efficiency under load rather than trust a spec sheet. NVML gives live power draw + utilization per NVIDIA GPU. macOS `powermetrics` (needs sudo) gives real per-component power draw — CPU, GPU, ANE — while a task runs. Fallback to known-spec lookup only when live measurement isn't available.

## Possible platform target: Apple Silicon companion demo

Not yet committed — logged here so it isn't lost. Clyde's dream outcome is a warm-intro conversation with someone senior at Apple, same "build something credible, start a conversation" strategy as the core project (see [[project-grid-aware-scheduler]] memory).

The core architecture (fleet of interchangeable GPUs + wholesale grid market price) doesn't port literally to a single Mac — one SoC, no GPU fleet to route between, no wholesale electricity market relevant to one device's draw. But there's a legitimate structural reframe, not a stretch: Apple Silicon has heterogeneous compute units on one chip (CPU P-cores/E-cores, GPU, Neural Engine) with different power/throughput curves per task — same shape as "different GPU types" at the chip scale instead of the rack scale. Pair that with a real consumer time-of-use tariff feed (e.g. UK Octopus Agile API, the standard one people build these demos against) instead of wholesale price, and the personal-device version becomes: delay non-urgent on-device ML jobs to cheap tariff windows, and route each task to whichever compute unit (CPU/GPU/ANE) is most power-efficient for it.

Framing discipline (same rule as the rest of this project): Core ML already does automatic compute-unit dispatch. Any write-up must be honest that this adds cost/time-of-use awareness on top of existing dispatch, not that it invents compute-unit selection from scratch.

Plan if pursued: keep the cloud/DC version as the primary credibility piece (real prior art, real stakes, matches the current AI-datacenter-energy story). Build the Apple Silicon variant as a second, smaller companion demo aimed specifically at an Apple conversation, since it speaks their language (performance-per-watt, on-device ML) more directly.

## Open questions / decisions not yet made

- Whether forecast-carbon + day-ahead-price is the right pairing long-term, vs. also pulling settlement/actuals for backtesting scored performance — leaning yes on the latter, not built yet.
- ~~Whether averaging Market Index price across data providers (APXMIDP, N2EXMIDP, ...) is good enough, or whether one provider should be preferred~~ **RESOLVED 2026-08-09 by looking at real numbers: do not average.** There are only two providers and `N2EXMIDP` reports structural zeros (100% zero across Aug/Jun/Feb 2026, 99.6% in Nov 2025), so averaging halves every price and, in the 99.6% case, distorts the curve's shape rather than just its level. Prefer `APXMIDP`, or filter non-positive prices first. Not yet patched into `gb_adapter.py` — to be built correctly in the real implementation. See Current State.
- Exact GPU types to profile — not yet chosen.
- Whether "urgent vs flexible" job classification is user-specified per job, or inferred somehow — not yet decided.
- Scheduling decision logic: leaning towards a plain rule-based/greedy or linear-programming allocator rather than an ML model — this is a constrained optimisation problem with known structure, and a transparent classical algorithm is more credible to a technical audience than an opaque model deciding GPU allocation, especially for a project whose whole framing is honest applied synthesis rather than novelty. ML (if used at all) would sit narrowly in forecasting future carbon/price, not in the decision itself. Not yet built either way.

## Session Log

*(Append-only — never edit or delete a past entry, even if it turns out to be wrong. If something changes, add a new entry that says so. This is what makes "catch up" reliable across tools.)*

### 2026-08-06 — session 1
- Created initial folder scaffold: `adapters/`, `scheduler/`, `data/`, `docs/`, `notebooks/`.
- Wrote `HANDOFF.md`, `README.md`, `adapters/base_adapter.py` (interface), `adapters/gb_adapter.py` (stub, raises `NotImplementedError`).
- Added this "Catch up" protocol and Session Log to make handoff between sessions and tools reliable.
- No real data pulled yet, no scheduler logic written yet.

### 2026-08-06 — session 2, same day
- Brought in Clyde's own earlier "National Grid Tool" project (a Streamlit app: 28 fetchers, SQLite+Parquet warehouse) and saved a full copy to `AI Energy/National-Grid-Tool`, sibling to this project, so any later session can read it for reference.
- Rewrote `adapters/gb_adapter.py` from a stub to a real implementation that imports that project's `carbon_intensity` and `elexon.prices` clients directly (both public/keyless APIs) and merges them into the common `GridDataPoint` format on `settlement_date` + `settlement_period`.
- Verified the join/merge logic offline against the National Grid Tool's own real test fixtures — mechanically correct.
- Could NOT verify a live network call from that sandbox: its proxy returned 403 on both `api.carbonintensity.org.uk` and `data.elexon.co.uk`. This needs to be run for real on Clyde's own machine before trusting it fully — flagged as the first thing to do next session.
- Also discussed and recorded a judgment call: scheduling decision logic should be a plain rule-based/LP algorithm, not ML — see "Open questions" below for the reasoning.

### 2026-08-09 — session 3
- Re-verified folder state: no changes since the 2026-08-06 sessions — same 4 files (`HANDOFF.md`, `README.md`, `adapters/base_adapter.py`, `adapters/gb_adapter.py`), `data/`, `docs/`, `notebooks/`, `scheduler/` all still empty. Nothing was manually edited outside these sessions.
- Discussed and logged a real architecture improvement: replace the planned static `gpu_profiles.csv` (hand-curated published specs) with a hardware detection + profiling module that auto-detects whatever compute units are actually present and empirically measures their efficiency (NVML for NVIDIA, `powermetrics`/`system_profiler` for Apple Silicon) rather than trusting spec sheets. See "Hardware detection" section above. This replaces step 4 in Next Steps.
- Discussed, and logged as NOT YET COMMITTED, a possible Apple Silicon companion demo aimed at a warm-intro conversation with someone at Apple — see "Possible platform target: Apple Silicon companion demo" section above for the honest technical mapping and the framing discipline required.
- Clyde asked directly: "has anyone done the whole project already." Researched via web search. Found this has substantially been done: **Compute Gardener** (github.com/elevated-systems/compute-gardener-scheduler) is an active open-source Kubernetes scheduler combining carbon/price-aware temporal shifting with hardware power-profile-aware placement, including GPU workload classification — essentially both of this project's pillars, already shipped. **Eco-Orchestrator/CARL** is an academic RL-based system doing the same combination, tested on 64 A100s, 34.7% carbon reduction reported. Corrected the Prior Art table and retracted the "nobody has done both together" gap claim — it was wrong and must not be repeated in any pitch. Added a "Where the real gap might still be" section: Apple Silicon/on-device is the one angle nothing found so far covers. Recommended reading Compute Gardener's actual source before writing more scheduler code, to avoid unknowingly rebuilding it.

### 2026-08-09 — session 4, later the same day
- Clyde moved `National-Grid-Tool` into `AI Energy/` (it had been sitting at `Desktop/National-Grid-Tool`), which is exactly where `gb_adapter.py`'s default path expects it. The import path issue that would have blocked step 2 is therefore gone.
- **Set up the environment on Clyde's Mac**: venv at `~/venvs/national-grid` (Python 3.12.9), installed from the National Grid Tool's `requirements.txt`, at the location that project's own README specifies (outside the project folder). Ran its test suite: 512 passed, 3 failed — all three are RSS/date-parsing in the news and Met Office feeds, caused by pip resolving pandas 3.0.x. Nothing in the carbon or price path is affected; every Elexon and Carbon Intensity test passes.
- **Ran `GBAdapter().get_data()` live for real** — the thing flagged as "first thing to do next session". It works: 2026-08-01 → 2026-08-07 returned 289 `GridDataPoint`s, 0 missing prices, carbon 31–186 gCO2/kWh. Steps 2 and 3 in Next Steps are now done. The offline fixture verification held up against live data.
- **Benchmarked it**: ~0.3s per day fetched, 92% of which is the Carbon Intensity endpoint (one call per day, vs prices chunked 5 days per call). ~2 min for a full year. Added step 3b — route through the National Grid Tool's warehouse instead of re-fetching live every time.
- **Found a real bug in the price path** by inspecting live provider-level data: `N2EXMIDP` publishes structural zeros, so averaging across MID providers halves every price (measured £57.44 vs true £115.00), and in Nov 2025 does so non-uniformly, distorting curve shape not just level. Clyde's call: **do not patch the current adapter** — carry it forward and build it correctly in the real implementation. Logged in Current State and resolves Open Question #2.
- **Initialised this folder as a git repository** with a `.gitignore`, and recorded in "External dependency" that the National Grid Tool stays out of it — separate production tool, imported at runtime, explicitly not to be renamed or stripped.
- Added an "Architecture we're borrowing" section documenting how the National Grid Tool actually works (settlement-period grid as universal join key, the layering, the two-backend warehouse) and how the cost/carbon calculation falls out of it.
