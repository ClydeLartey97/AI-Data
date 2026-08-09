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
- ~~NOT yet verified: an actual live network call.~~ **Superseded — the live call was made and passed on 2026-08-09.** (This line previously contradicted the "VERIFIED LIVE" bullet above and misled a later session into re-running work that was already done. Left visible rather than deleted, so the contradiction is a matter of record.)

**Built 2026-08-09 (session 5) — the first working vertical slice:**
- `adapters/gb.py` — **the corrected GB adapter, and the one to use.** Same signal choices, but price comes from a single preferred provider (`APXMIDP`) with non-positive prices dropped, instead of a mean across providers. Returns `GBPoint`, which carries `price_provider` and `settled_carbon` so every figure's provenance travels with it. `gb_adapter.py` is untouched and superseded — keep it for the record, don't build on it.
- `core/grid.py` — **market-agnostic** scheduling algorithms and energy accounting. Knows nothing about GB. `Job`, `run_immediately` (naive baseline), `cheapest_window`, `cleanest_window`, `compare`. The allocator is a sliding-window minimum: no solver, no ML, microseconds to run.
- `app/dashboard.py` — generates a self-contained HTML dashboard from live data. **Not Streamlit** (see "UI and deployment" below).
- `tests/test_grid.py` — 15 offline tests, all passing, no network. Covers the accounting, deadline enforcement, inflexible jobs, and the rule that unpriced windows are never chosen (a window with missing data is not a cheap window).
- **Real measured result** (GB, 2026-08-05→08, 6.5 kW job for 4 h, 24 h deadline): baseline £3.21 / 2.10 kgCO₂ → scheduled £0.21 / 0.97 kgCO₂. **93.6% cost and 54.0% carbon saved for an 11.5 h delay.** Verified against live data, with the corrected price path. Steps 5, 6 and 7 are substantially done.
- Confirmed on real data that **cost-optimal and carbon-optimal windows can disagree**, sometimes with the cleanest window costing *more* than running immediately. The scheduler has to be told which objective it is serving; it cannot infer it. The dashboard surfaces this only when the trade-off is material (≥2% cost penalty), because a bare index comparison fires on windows a single period apart that cost the same to a penny.
- No GPU profile data collected yet — hardware detection (step 4) is the remaining gap, and now the only thing standing between this and the full pitch.

## Next steps (in order)

1. [x] Build `adapters/base_adapter.py` — the common interface every market adapter implements
2. [x] Build `adapters/gb_adapter.py` — pull GB carbon intensity (National Grid ESO / Carbon Intensity API) and day-ahead price data — **DONE and now VERIFIED LIVE on Clyde's Mac** (2026-08-09). Works end to end.
3. [x] Confirm the common data format works by printing one week of GB data — done, 289 points, join holds. Plot still outstanding, but the format is proven. Note the price bug in Current State before trusting any £ figure.
3b. [ ] Route the adapter through the National Grid Tool's warehouse (`warehouse/store.py`) with fetch-on-miss, instead of hitting live APIs on every call. Reuses the layer that already solves this, makes repeat backtests instant, and unblocks pulling settlement actuals (`disebsp`, already in that project's `DATASETS` registry) for scoring realised performance.
4. [ ] **← YOU ARE HERE.** Build a hardware detection + profiling module (replaces the static `gpu_profiles.csv` idea — see "Hardware detection" below). Build it behind a `HardwareProvider` interface with **two** implementations, exactly mirroring the market-adapter split: `LocalDetector` (real — NVML / `powermetrics` / `system_profiler`) and `SimulatedFleet` (reads a fleet YAML, so a multi-GPU cluster can be developed and demoed on a laptop). Every device figure carries a `source` field — `MEASURED` / `SPEC` / `SIMULATED` — so the UI can never present a config file as a measurement.
5. [x] Build the naive baseline — `core/grid.run_immediately`. Done, tested.
6. [x] Build the grid-aware allocation logic — `core/grid.cheapest_window` / `cleanest_window`. Done, tested. **Hardware-aware allocation still outstanding** and depends on step 4.
7. [x] Produce the comparison, naive baseline vs. scheduler, on real GB data — done, and live in the dashboard rather than as a static chart. 93.6% cost / 54.0% carbon saved on a real 3-day window.
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

## Location awareness (built 2026-08-09)

**Location turns out to be a bigger lever than time.** Time-shifting a job on the national signal saved 54% carbon. Moving it between GB regions, measured live: **North Scotland 0 gCO2/kWh (96% wind) against East Midlands 131, at the same instant.** Google's carbon-intelligent computing work shifts on both axes; this project now has the data to do the same.

- `adapters/gb_regional.py` — all 18 GB grid regions, postcode -> region lookup (accepts full postcodes and normalises to the outward code), and a 48-hour forward half-hourly carbon forecast per region.
- `adapters/weather.py` — Open-Meteo forecast for any lat/lon: temperature, solar radiation, 100 m wind speed, cloud cover. Public, keyless, **worldwide** — so unlike the GB carbon endpoints this already works for markets that have no adapter yet, and it is the feed the bespoke CAISO/ERCOT forecast work would build on.

Weather explains carbon rather than merely sitting beside it — measured across the presets in one pass:

| location | region | gCO2/kWh | wind 100m | usable wind |
|---|---|---|---|---|
| Thurso | North Scotland | 0 | 10.8 m/s | 83% |
| Edinburgh | South Scotland | 1 | 8.5 m/s | 59% |
| Manchester | North West England | 40 | 4.2 m/s | 19% |
| London | London | 90 | 3.4 m/s | 14% |

**The constraint that shapes the UI — in GB, location changes CARBON but not PRICE.** GB settles one national wholesale price, so moving a job from London to Scotland changes its emissions and nothing about its bill. Locational price variation is real but lives in nodal markets (CAISO, ERCOT) where thousands of nodes settle separately. A GB location control therefore drives carbon and weather only. **Never present a GB map as though it varied price** — that becomes true when the nodal-market adapters land, and not before.

## UI and deployment (decided 2026-08-09)

**The real system cannot be a web portal, and this is forced, not preference.** A browser cannot read hardware power draw — NVML, `powermetrics`, `system_profiler`, Metal device enumeration are all unreachable from a web page. And a scheduler that defers a job to a window six hours out has to still be running six hours later. So the real system needs a local, long-lived process with OS access.

**But "web portal vs desktop app" is a false binary.** The answer is a local process serving a browser UI — how Jupyter, Ollama and the National Grid Tool itself all work. Browser-based UI with instant iteration and no packaging, on top of a native process with unrestricted hardware access. No code signing, no notarisation, no per-platform builds.

**Not Streamlit, though.** It imposes a strong visual identity that can't be reshaped into a native-feeling interface without CSS overrides that break on version bumps. `app/dashboard.py` instead generates plain HTML/CSS/SVG we own outright — no framework, no CDN, no build step, one file that opens in any browser. The Apple-style design language is deliberate.

The layering, each knowing only the one below it:

1. **Core engine** (`core/`, `adapters/`) — importable, testable, no UI, no server. *This is the actual product.*
2. **Agent** — the long-lived local process: detects hardware, holds the job queue, wakes to launch deferred jobs. Later a `launchd`/`systemd` service. Not built yet.
3. **UI** (`app/`) — thin and replaceable.

**Two artefacts, two deployment stories, and they don't conflict:** the `demo/` simulator is a pitch/communication piece and is a static page that can ship on GitHub Pages for a shareable link; the scheduler is the real system and installs locally. Keep them separate.

**Running it — two ways, and the difference matters:**

```bash
# 1. Serve it (normal way). Rebuilds from live data on reload.
~/venvs/national-grid/bin/python -m app.serve          # → http://localhost:8765

# 2. Render one file (for sharing a snapshot, or offline).
~/venvs/national-grid/bin/python -m app.dashboard --days 3 --open   # → file://…
```

**"In the browser" is not "on the web", and the distinction is the whole architecture.** `app.dashboard` writes an HTML file that the browser opens off disk over `file://` — no server, nothing listening. `app.serve` runs a local process bound to `127.0.0.1` (loopback only — other machines on the network cannot reach it, and nothing outside can). Both are entirely local. Neither is hosted anywhere.

That is exactly what makes the rest of the plan possible: a hosted web page could never read `powermetrics` or stay alive to launch a job deferred six hours out, but a local process serving a local page does both while still giving a browser-quality UI. Only fleet mode across multiple machines ever needs real hosting.

Generated output lands in `app/build/` (gitignored — it rebuilds in seconds). The server caches for 5 minutes, since the underlying signals only move on a half-hour settlement boundary.

**Charting — custom, not a library. Decided 2026-08-09 after trying KLineCharts.**

The target is a good *consumer finance* chart (Trading212, Yahoo), which is not the same thing as a trading terminal — and the difference decided the build. A terminal gives free pan/zoom and technical indicators; an RSI of carbon intensity is meaningless. A consumer chart gives a **range selector** and a **crosshair that reads out in a fixed header slot**, and that is roughly two hundred lines of SVG.

`app/chart.py` + `core/resample.py`. No library, no CDN, ~125 KB page, full design control.

**The domain reason this could not be delegated to a charting library:** bucketing must keep the extremes. Naive downsampling takes each bucket's mean — and for a scheduler that is actively destructive, because the whole job is finding the *cheapest half-hour*. A day whose 30-minute prices ran £2.84 to £158.94 has a daily mean near £80, and both numbers that matter are gone. So every bucket keeps mean, min and max; the chart draws the mean as a line with the min-max range as a band behind it. Zooming out costs resolution and never costs the extremes. `tests/test_resample.py::test_extremes_survive_aggregation` guards this, because a mean-only chart still *looks* fine — the failure is invisible by inspection.

**Bug worth remembering: the scheduling decision must run over the horizon a job actually faces, not over whatever history the charts show.** Feeding the full 21-day series to `compare()` produced a "run immediately" baseline dated three weeks earlier — not a decision anyone could act on — and placed the chosen window off the edge of every visible chart range. The dashboard now decides over `series[-deadline_periods:]` and states the window it decided over. Charts still show full history; the decision does not.

**Chart completeness — what a grid chart actually needs.** A first version shipped with no y-axis at all: gridlines and value labels were missing entirely, so the reader could see that carbon rose but not to what. Also missing was any link back to the decision. Both now fixed:
- Y-axis with round-number ticks (snapped via a nice-number step, because "190" is a number a reader holds and "187.4" is not), in a left gutter so labels never sit on the plot.
- Highlighted spans mapped from timestamps, so the window the scheduler chose is visible on the curve and stays correct across range changes.
- Range summary: high, low, average and **spread**, the last being the headline for a scheduler — it is the size of the prize for shifting a job at all.
- A settled/forecast divider, because one unbroken line implies both halves are the same kind of claim and they are not.

**Still missing from Page 1, and it is not finished:** generation mix by fuel, region selection and the 18-region comparison, weather alongside carbon, forward forecast, percentage change over the range, and weekend/overnight shading.

**Bug worth remembering: axis format comes from the SPAN, not the BUCKET.** They are different questions. A 1-month range buckets into 2-hour bars, so the bucket is "intraday" — but labelling a month-wide axis `22:00, 20:00, 04:00` tells the reader nothing about where they are. Span decides the axis; bucket decides the hover readout. Caught only by rendering it and looking.

Ranges: 1D (30 min) · 1W (30 min) · 1M (2 h) · 3M (6 h) · 1Y (1 day) · Max (1 week). All bucketed server-side and embedded, so switching range is a repaint, not a fetch.

**KLineCharts is kept but unused** (`app/kline.py`, vendored v9.8.12, Apache-2.0) in case a power-user terminal view with indicators is ever wanted. Its lessons are recorded below.
- **Hand-written SVG** for everything KLineCharts is not for: generation-mix breakdowns, stat tiles, sparklines, fleet and routing diagrams. It is a *financial candlestick* library — a fuel-mix chart is not an OHLC series and forcing it through would be worse than drawing it directly.

**Jank already hit and fixed, so nobody hits it twice:**
- The formatter API is `chart.setCustomApi({formatDate})`, **not** `setFormatter`. Calling the wrong name throws, and since it sits before `applyNewData` the chart draws styled axes and *no data at all* — it looks like a data problem and is actually an exception. Check the vendored bundle's exported names before guessing an API.
- The default tooltip lists open/high/low/close. For a line series that is the same number four times. Replaced with a custom tooltip showing time and value only.
- Axis label weight cannot be set for dates independently of times — the library styles the whole bottom axis as one group.
- Library defaults print a bare `HH:mm` with no date, and an ambiguous `MM-DD`. Custom `Intl.DateTimeFormat` formatters spell the month and keep the year, and drop clock times once bars are daily or coarser.
- Timezone: `chart.setTimezone()` rebuilds the internal formatter and repaints, so formatters read `dtf.resolvedOptions().timeZone` rather than tracking toggle state — one source of truth.

**Not yet verified:** the click-to-read tooltip renders correctly. It could not be confirmed in a headless screenshot because KLineCharts handles pointer events on its own canvas overlay and ignores synthetic ones. Needs checking with a real mouse on the served page.

**Charts:** the palette is Apple's system colours snapped to steps that pass a colour-accessibility validator in *both* light and dark — lightness band, chroma floor, colour-blind separation, normal-vision separation, and contrast against the surface. The dark series colours are deliberately **darker** than Apple's own dark system colours, which sit above the band a chart needs. Don't hand-edit `CARBON_*` / `PRICE_*` in `app/dashboard.py` without re-validating. Carbon and price get separate charts on purpose — never a dual y-axis.

## A note on the `demo/` simulator

`demo/task_difficulty_routing_simulator.html` is a **throwaway communication prototype**, not a spec. A slider for operation count, a dropdown for task type, four fixed hardware tiers. It exists to make the task-difficulty-routing idea visible before the real thing exists.

Do not let its simplicity anchor scope. The real system replaces every part of it: real task classification instead of a dropdown, auto-detected and benchmarked hardware instead of four labelled boxes, real multi-GPU topology and interconnect awareness, and grid-signal timing feeding the routing decision — all at once. Keep the prototype as a reference for the interaction idea; build nothing from its structure.

## Hardware detection (replaces static GPU profile CSV)

Decided 2026-08-09: instead of hand-curating a CSV of published GPU specs, the profiling module should auto-detect whatever compute units are actually present on the machine it's running on, then either match against known specs or empirically benchmark on the fly. This is more credible than trusting published TDP numbers (which are idealized) and works on hardware nobody's pre-catalogued.

Two separate steps, don't conflate them:
- **Detection** (easy): enumerate what's present. NVIDIA/cloud → `nvidia-smi` / NVML (`pynvml`) lists every GPU + live stats. Apple Silicon → `system_profiler`, `sysctl` (`hw.perflevel0/1` for P-core/E-core counts), Metal's `MTLDevice` API for GPU.
- **Profiling** (harder, more valuable): measure real efficiency under load rather than trust a spec sheet. NVML gives live power draw + utilization per NVIDIA GPU. macOS `powermetrics` (needs sudo) gives real per-component power draw — CPU, GPU, ANE — while a task runs. Fallback to known-spec lookup only when live measurement isn't available.

## The portal — two pages (built 2026-08-09)

Served together by `app/serve.py` at `http://localhost:8765`:

- **`/` — Grid.** Live carbon and price, the scheduling decision, range-selector charts. Not finished: generation mix, region selection, weather, forward forecast.
- **`/simulator` — Model simulator** (`app/simulator.py`). Pick a model, hardware, count and task; get runtime, power, energy, memory feasibility, and — via the live grid signal — cost and carbon. 840 configurations precomputed server-side and embedded, so every control is instant with no round trip.

The simulator carries three views: the selected configuration, **every device ranked by energy** for the same job, and **the same device at every fleet size**. The second and third are where the argument lives, because they make two results visible at a glance:

- Adding hardware does not save energy. 1x and 64x H100 both use ~22-23 kWh for the same job; only runtime moves (32.1 h to 31 min).
- Choosing hardware does. On an 8B training run the spread across the catalogue is 16 kWh (H100 PCIe) to 370 kWh (M2) — over 20x, for identical work.

Provenance is on screen as a badge, not buried: SPEC for datasheet-backed rows, ESTIMATED for Apple, where no vendor figures exist.

**On-site renewables (`core/renewables.py`, added 2026-08-09).** Given a location and installed solar/wind capacity, computes how much of a job's load on-site generation actually serves, period by period, and how much must still be imported.

**Built rather than called out to Renewables.ninja, deliberately.** Renewables.ninja is better modelled — MERRA-2/SARAH reanalysis, real turbine curves, bias correction validated against metered output — but it needs a token, rate-limits to tens of requests an hour, and answers in seconds. A simulator that recomputes on a control change cannot live on that. The physics is a few lines and the inputs (irradiance, 100 m wind) are already fetched from Open-Meteo for the weather panel, so this runs locally in microseconds. What is given up, and it must be labelled ESTIMATED because of it: no bias correction, a generic turbine curve, no shading/soiling/downtime, and irradiance treated as plane-of-array. **Expect the shape right and the level optimistic.** Validating against Renewables.ninja on a handful of sites is the obvious next step — same "derive, then check against a known-good source" method as the hardware profiling.

**The number that matters is hourly matching, not annual.** Measured live at Thurso with 500 kW of each: the site generates **191% of what it needs** across the period yet covers only **90% of it**, still importing 930 kWh — because the generation arrived when the load did not. Netting annual totals would call that fully renewable. This is what 24/7 carbon-free accounting means and why the page reports both figures side by side with the gap called out.

**Simulator rewritten to compute client-side (2026-08-09).** The first version precomputed every (model, task, device, count) combination server-side and embedded the lot. That capped the catalogue at whatever the payload could carry — five models — and made a custom model impossible, because you cannot precompute a number nobody has typed. Now the device and model specs ship and the arithmetic runs on each control change. The page went from 122 KB to **32 KB** while going from 840 fixed combinations to 35 models x 12 devices x 11 fleet sizes x 6 precisions, plus any custom model. Precomputing was the expensive way to do less.

`core/models.py` holds the catalogue — Llama, Mistral, Qwen, Gemma, Phi, DeepSeek, Command R, Falcon, Yi, GPT-OSS. **MoE models carry two parameter counts and conflating them is the classic error:** compute scales with *active* parameters, memory with *total*. DeepSeek-V3 is 671B total and ~37B active — the compute of a 37B model and the memory of a 671B one. Both true; a simulator tracking one will confidently mislead.

**Page 1 now shows regions.** All 18 GB grid regions with live carbon and generation mix. The adapters had existed for hours while the page still showed national-only data, which was the fair criticism. Measured live: North Scotland **0 gCO2/kWh on 100% wind** against South West England at **358 on 91% gas** — same country, same instant. Moving a job is a bigger lever than delaying one, and the page now says so.

**Still to build: page 3, the planner.** Given a job, a heterogeneous fleet ("3 NVIDIA, 2 Intel Arc, 1 AMD" or 25 H100s) and a deadline, decide how to split the work across devices and when to run each part, optimising cost and carbon together. `hardware/base.Fleet` already models heterogeneous groups and `core/workload` already splits work by achievable throughput; what is missing is placing those splits in *time* against the grid signal.

## Local cache — the thing that made date ranges honest (built 2026-08-09)

`core/store.py` (SQLite) + `core/backfill.py` + `core/feed.py`.

**The bug it fixed was a lie in the UI.** Live fetching costs ~0.3s per day of history, so the app fetched three weeks — then offered 1M, 3M, 1Y and Max range buttons that all rendered *the same three weeks*. Four controls, one view, no indication anything was wrong.

Now: **395 days cached, 18,864 rows, 3.2 MB, and a full year loads in 23 ms.** Backfill takes ~2 minutes once and is safe to re-run or interrupt (idempotent per settlement date). Only settled days are cached — today and tomorrow are forecasts that change, so those always go live.

```bash
python -m core.backfill --days 400     # one-time, ~2 min
python -m core.backfill --stats
```

**Everything above the data layer now calls `core.feed.load()`** and never touches an adapter directly. `feed.extent()` reports what data actually exists, so the range selector can stop offering ranges it cannot fill.

**A year of data changes the story.** Over 2025-07 → 2026-08: price ran **£0.09 to £560.81 — a 6,231× spread** — and carbon 17 to 319 gCO2/kWh. The three-week window had been hiding almost all of the opportunity this project exists to exploit.

## Allocation path map (built 2026-08-09)

`app/flow.py` (server-side renderer) and a client-side version inside `app/simulator.py`, plus a fleet builder for mixed-vendor estates.

Three stages — **JOB → DEVICES → WINDOW** — drawn as ribbons whose width is each group's share of the work, because the proportions *are* the decision. Verified with 8x H100 + 4x Arc A770 + 2x MI300X + 1x M2 Ultra: the H100s take 79.1% and the MI300X 19.9%, so every group finishes together. Split that fleet evenly instead and the slowest sets the finish time while the fastest idle at part load, burning power for nothing — which is precisely what a uniform-fleet demo cannot show.

## Chart: now a terminal, not a picture

Area / line / **candles** / bars, wheel zoom centred on cursor, drag-pan, shift-drag box select, keyboard control, MA/EMA, UTC↔Local, CSV, full OHLC crosshair. **Interval and range are separate controls** — conflating them is why candles rendered as flat dashes (each bucket held one reading, so O=H=L=C). Bucketing is client-side and zoom is stored as a *time window*, so changing interval keeps you on the same stretch of time.

Two deliberate departures from a financial terminal: a **spread sub-pane** instead of volume (the high-low range within an interval is the size of the prize for shifting a job; volume is meaningless here), and **percentile bands** instead of oscillators ("is now in the cheapest decile" is the real question; an RSI of carbon intensity is not).

## Three ways I have now blanked the charts — read before editing generated JS

Each was invisible to the check I had at the time:

1. **A raw newline inside a JS string literal.** Syntax error, kills the whole script block. Grepping found the code present and correct-looking. **Fix: `tests/test_pages.py` parses every generated script with `node --check`.**
2. **An unsubstituted `__HSUB__` placeholder.** A valid JS identifier, so `node --check` passed it; it only failed in a browser. **Fix: substitution raises at build time, and a test asserts no `__TOKEN__` survives.**
3. **A `str.replace()` whose anchor did not match**, so an entire JS block was silently dropped — the markup rendered, the behaviour did not. **Fix: assert the anchor exists before replacing.**

**And the rule underneath all three: render it and look at it.** Every one of these passed its automated check. Screenshot the page after any change to generated JS.

Note both page modules build HTML with **f-strings**, so braces in injected JS must be doubled (`{{`/`}}`). `app/chart.py` avoids this entirely by using a plain string with `__TOKEN__` placeholders — the better pattern, and worth porting `simulator.py` to.

## Analytics panels — built for the actual buyer (2026-08-09)

Target user is now explicit: **a data-centre operator with a carbon-neutrality target.** `core/analytics.py` + `app/panels.py` answer the questions that buyer has, measured over the whole cached year rather than one week.

**Findings that should shape the pitch, all measured on 392 days of real GB data:**

- **Flexibility is quantified.** A 4-hour job saves **21.9% median on cost at a 24-hour deadline**, and **75% at a week**. Carbon saves **28.5% at 24 hours**. Every start in the history, not one anecdote.
- **Cheap is not clean.** Price/carbon correlation is only **r = 0.54**, and the cheapest decile is also the cleanest decile just **59% of the time**. An operator optimising purely on price misses the carbon target 41% of the time — which is the single strongest argument that the objective has to be *stated*, not inferred.
- **Carbon is less volatile than price**, so carbon savings from time-shifting are smaller and steadier. Location, not timing, is the bigger carbon lever (North Scotland 0 vs South Wales 358 gCO2/kWh, same instant).

Panels: savings-vs-deadline (cost and carbon), time-of-day profile (both), duration curve (both), price-carbon scatter. Deliberately non-interactive — a duration curve is a conclusion over a year, and giving every panel a toolbar would bury the two charts where interaction matters.

## Design direction changed (2026-08-09)

The Apple design language was **dropped as the primary goal**. It is optimised for calm and touch; this is a data terminal for an operator, and the two pull in opposite directions — large type and generous padding mean fewer things on screen, which is the opposite of powerful. Density is now the goal. The analytics grid is the first piece built that way; the rest of both pages still needs converting from the vertical card stack.

## Still outstanding (2026-08-09)

- **KV cache is modelled in `core/models.py` but not wired into the simulator UI.** Inference memory there is still a flat 1.25x multiplier, which is wrong by orders of magnitude at long context: a 70B at 128k across 32 sequences needs ~1.3 TB of KV cache against 141 GB of weights, and grouped-query attention swings it 8x. **Needs context-length and batch-size controls.**
- **PUE / cooling overhead is absent.** Datacentre energy is not chip energy; every figure is optimistic by roughly 1.2-1.5x.
- **No sharding model** (FSDP/ZeRO), so multi-GPU training memory is wrong.
- **Scaling model covers communication only** — no pipeline bubbles or stragglers, so ~99% at 32 devices where reality is 70-85%.
- **No URL state**, so a configuration cannot be shared or returned to.
- **57 devices sit in a plain `<select>`** with no search or filter.
- **Density**: the pages still read as an iOS app rather than a data terminal — large type, generous padding, one column. Needs a denser multi-panel layout.
- **Page 3, the planner**, still does not exist.

## Auto hardware analysis, and how to prove it works (planned 2026-08-09)

**The goal: nobody types in specs.** A simulation runs on the device, and from how it behaves the system derives what that hardware actually is — throughput, memory bandwidth, power curve, and how it scales. Manual spec entry is a stopgap.

**The validation method is the valuable part, and it is genuinely falsifiable.** Run the profiler on hardware whose real specifications are already published, then compare what the algorithm derived against those known figures. If the derived numbers track the datasheet across several different devices, the algorithm can be trusted on hardware nobody has catalogued. If they diverge, that is a measurable error to fix, not a matter of opinion.

That matters more than it might sound. Every simulator in this space asks to be taken on trust. This one can state an error bar: *"derived within X% of published spec across N devices."* That is a claim with a number attached, and it is the kind of thing that survives a technical audience.

Sequence:
1. Micro-benchmarks -> derive achievable FLOPS, memory bandwidth, sustained power. Start with what is measurable here: an M2 with 8 GB, using `powermetrics` for real watts.
2. Compare derived against `hardware/catalog.py` published figures. Record the delta per device.
3. Widen to whatever hardware can be borrowed or rented — a single cloud GPU-hour is enough for one calibration point.
4. Only once the deltas are known and small: trust the profiler on uncatalogued hardware, and report the error bar alongside every derived figure.
5. Then replace the analytical scaling model with **measured** scaling curves, which is the weakest part of the current simulator (see below).

**Apple Silicon inverts the validation method, and is the stronger case for it.** Apple publishes core counts and sometimes memory bandwidth — and no GPU TFLOPS, no per-component TDP, nothing else the simulator needs. So `hardware/catalog.py`'s Apple rows are marked ESTIMATED, not SPEC: they are community benchmarks, not datasheet values. There is no ground truth to check a profiler against.

But the architecture is uniform. An M2, an M3 Max and an M2 Ultra are the same GPU core design in different quantities with different memory bandwidth. That makes a different and arguably better test available:

> Profile one Apple chip. Derive per-GPU-core throughput and per-core power. **Predict** a different Apple chip from its core count and bandwidth alone. Then measure that chip and see whether the prediction held.

Matching a datasheet only shows you can reproduce a published number. Predicting an unmeasured device from constants derived on another one is a real out-of-sample test — the thing actually claimed when the profiler is pointed at uncatalogued hardware. Apple is the best platform to run it on precisely because the architecture is consistent and the specs are absent.

The first calibration point is available now: the M2 in use here, 8 GB, `powermetrics` for real watts. The 8 GB limit caps it to small models, which is fine — deriving per-core constants does not need a large model.

**Known weakness this would fix.** `core/workload.scaling_efficiency` models communication cost only — gradient all-reduce over the interconnect. Real large-run inefficiency also comes from pipeline bubbles, stragglers, load imbalance and failure recovery, none of which are modelled. So it reports ~99% efficiency at 32 devices where a real run might see 70-85%. Sound for comparing configurations; not a scaling prediction, and it must not be quoted as one.

**Findings already worth keeping from the first simulator run** (70B training, 1B tokens):
- **More devices does not save energy — it saves time.** 1x H100 and 32x H100 both consume ~197 kWh; only the runtime changes (281 h vs 8.9 h). Under near-perfect scaling, total energy is invariant.
- **Hardware choice does save energy.** 8x A100 needs 333 kWh for the same job that 8x H100 does in 197 kWh — a 40% difference from device selection alone.
- Together these say something useful about the whole project: the savings come from *which* hardware, *where*, and *when* — not from *how much*.

## Possible platform target: Apple Silicon companion demo

Not yet committed — logged here so it isn't lost. Clyde's dream outcome is a warm-intro conversation with someone senior at Apple, same "build something credible, start a conversation" strategy as the core project (see [[project-grid-aware-scheduler]] memory).

The core architecture (fleet of interchangeable GPUs + wholesale grid market price) doesn't port literally to a single Mac — one SoC, no GPU fleet to route between, no wholesale electricity market relevant to one device's draw. But there's a legitimate structural reframe, not a stretch: Apple Silicon has heterogeneous compute units on one chip (CPU P-cores/E-cores, GPU, Neural Engine) with different power/throughput curves per task — same shape as "different GPU types" at the chip scale instead of the rack scale. Pair that with a real consumer time-of-use tariff feed (e.g. UK Octopus Agile API, the standard one people build these demos against) instead of wholesale price, and the personal-device version becomes: delay non-urgent on-device ML jobs to cheap tariff windows, and route each task to whichever compute unit (CPU/GPU/ANE) is most power-efficient for it.

Framing discipline (same rule as the rest of this project): Core ML already does automatic compute-unit dispatch. Any write-up must be honest that this adds cost/time-of-use awareness on top of existing dispatch, not that it invents compute-unit selection from scratch.

Plan if pursued: keep the cloud/DC version as the primary credibility piece (real prior art, real stakes, matches the current AI-datacenter-energy story). Build the Apple Silicon variant as a second, smaller companion demo aimed specifically at an Apple conversation, since it speaks their language (performance-per-watt, on-device ML) more directly.

## Scope decisions from 2026-08-09 planning session

No fixed deadline for showing this to anyone — pace is set by getting things genuinely right, not by a target date. Building on existing published research/reasoning (Compute Gardener, Eco-Orchestrator, Zeus/Perseus, academic carbon-forecast methodology) and improving on it with better execution is legitimate and should be cited, not treated as something to avoid referencing.

**Forecasting — researched, decision made:** checked whether GB's National Grid ESO Carbon Intensity forecast is already "genuinely direct from source" (Clyde's own bar for whether a bespoke model is worth building). Answer: yes, for GB specifically. It's built with University of Oxford Dept of Computer Science + Met Office weather data + WWF/EDF Europe, uses ML model ensembling into an optimised meta-model, a full reduced GB network power-flow model (active/reactive flows, system losses, impedance), regional granularity, nowcasting updates every 30 min, 96+ hours ahead. This is not a naive model — trying to out-forecast it specifically for GB is not a good use of effort.

However: CAISO and ERCOT do **not** have an equivalent free, transparent, operator-published forecast. Public access there runs through third parties (WattTime MOER, Electricity Maps — often paywalled/subscription, EIA raw generation-mix data) rather than a documented in-house model like GB's. That's real, verified room for a bespoke forecast to add value — not by beating GB, but by providing something methodologically transparent that doesn't otherwise exist for free in those markets.

**Decision:** build an own forecasting model, grounded in published research methodology (not invented from scratch), pulling from multiple sources (not just one grid operator's feed). Validate it first against GB's published forecast as the best available ground truth (since GB's is known-good), then apply the validated methodology to CAISO/ERCOT where no free equivalent exists. Do not claim it beats GB's forecast — frame it as consistent, transparent methodology extended to markets that lack GB's infrastructure.

**Hardware scope — decision:** include multi-GPU topology/interconnect-aware clustering (how GPUs work together in groups, not just individual device benchmarking) in scope now, not deferred to later. This is harder than single-device profiling but is core to the vision of auto-arranging clusters, not an add-on.

**Primary differentiator — decision:** task-difficulty-based routing (classifying a task's difficulty — e.g. complex math vs. verbal reasoning — and routing it to the right hardware/cluster configuration, combined with grid-aware timing) is the main focus. This remains the one piece not found anywhere in existing prior art (Compute Gardener, Eco-Orchestrator are task/content-agnostic — they schedule by resource request, not inferred task difficulty).

Companion demo planned: an interactive visual simulator (slider for number of operations + task type, animated branching-path visualization showing which hardware/route a task would take) to communicate the concept before the real backend and hardware detection exist. Lives separately from the core scheduler code (e.g. `notebooks/` or a new `demo/` folder) — a prototype/pitch tool, not production logic.

**Relation to prior art — decision:** study Compute Gardener and Eco-Orchestrator's source/papers for reasoning and design choices, but build an independent implementation. No direct code reuse, no plan to contribute upstream to Compute Gardener at this time.

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
- Went deeper on comparing mechanics, not just concepts: verified from the actual KubeFM transcript that Compute Gardener's "price-aware" scheduling is a fixed time-of-use schedule (residential/commercial rate structure), not real wholesale day-ahead market price — a real, meaningful difference from this project's Elexon day-ahead Market Index Price signal. Also confirmed Compute Gardener's GPU power profiling already uses live NVIDIA DCGM measurement, not static specs, which undercuts (not confirms) the novelty of this project's planned hardware auto-benchmarking — logged honestly rather than oversold.
- Clyde laid out an expanded, more ambitious vision: (1) a bespoke weather+multi-source-driven electricity generation/carbon forecast model grounded in research papers, (2) multi-GPU topology/interconnect-aware clustering (not just per-device benchmarking), (3) task-difficulty-based routing (classify task difficulty, e.g. complex math vs. verbal reasoning, route to best-suited hardware/cluster) combined with grid-timing, (4) deadline-guaranteed scheduling ("after 5 hours, x is done"). Researched and logged decisions — see "Scope decisions from 2026-08-09 planning session" above for full detail on each. Key finding: GB's National Grid ESO forecast is genuinely sophisticated (Oxford CS + Met Office + full network power-flow model) and not worth trying to beat directly; CAISO/ERCOT lack an equivalent free transparent operator forecast, which is where a bespoke model has real room to add value. Task-difficulty-based routing confirmed as the primary differentiator (not found in any prior art). Multi-GPU topology work pulled into current scope rather than deferred. Decided to study Compute Gardener/Eco-Orchestrator independently rather than reuse or contribute code.
- Building an interactive visual simulator/prototype for task-difficulty-based routing per Clyde's request (slider for number of operations + task type, animated path visualization) — see file in `demo/` if created this session.

### 2026-08-09 — session 4, later the same day
- Clyde moved `National-Grid-Tool` into `AI Energy/` (it had been sitting at `Desktop/National-Grid-Tool`), which is exactly where `gb_adapter.py`'s default path expects it. The import path issue that would have blocked step 2 is therefore gone.
- **Set up the environment on Clyde's Mac**: venv at `~/venvs/national-grid` (Python 3.12.9), installed from the National Grid Tool's `requirements.txt`, at the location that project's own README specifies (outside the project folder). Ran its test suite: 512 passed, 3 failed — all three are RSS/date-parsing in the news and Met Office feeds, caused by pip resolving pandas 3.0.x. Nothing in the carbon or price path is affected; every Elexon and Carbon Intensity test passes.
- **Ran `GBAdapter().get_data()` live for real** — the thing flagged as "first thing to do next session". It works: 2026-08-01 → 2026-08-07 returned 289 `GridDataPoint`s, 0 missing prices, carbon 31–186 gCO2/kWh. Steps 2 and 3 in Next Steps are now done. The offline fixture verification held up against live data.
- **Benchmarked it**: ~0.3s per day fetched, 92% of which is the Carbon Intensity endpoint (one call per day, vs prices chunked 5 days per call). ~2 min for a full year. Added step 3b — route through the National Grid Tool's warehouse instead of re-fetching live every time.
- **Found a real bug in the price path** by inspecting live provider-level data: `N2EXMIDP` publishes structural zeros, so averaging across MID providers halves every price (measured £57.44 vs true £115.00), and in Nov 2025 does so non-uniformly, distorting curve shape not just level. Clyde's call: **do not patch the current adapter** — carry it forward and build it correctly in the real implementation. Logged in Current State and resolves Open Question #2.
- **Initialised this folder as a git repository** with a `.gitignore`, and recorded in "External dependency" that the National Grid Tool stays out of it — separate production tool, imported at runtime, explicitly not to be renamed or stripped.
- Added an "Architecture we're borrowing" section documenting how the National Grid Tool actually works (settlement-period grid as universal join key, the layering, the two-backend warehouse) and how the cost/carbon calculation falls out of it.

### 2026-08-09 — session 5, same day
- **Caught up and found the doc was contradicting itself.** Current State said the adapter was "VERIFIED LIVE" in one bullet and "NOT yet verified" three bullets later, which led this session's opening instructions to ask for live verification that had already been done. Fixed, and the stale line left struck through rather than deleted so the failure mode is on the record. Lesson worth keeping: when marking something done, delete or strike the line that said it wasn't.
- **Built the first working vertical slice — real data end to end into a real decision.**
  - `adapters/gb.py`: corrected GB adapter. Price now comes from `APXMIDP` alone with non-positive values dropped, never a cross-provider mean. Verified: mean £111.91 over a real 2-day window, against the £57-ish the old averaging path produced. `gb_adapter.py` left untouched and superseded.
  - `core/grid.py`: market-agnostic accounting and algorithms — `run_immediately` (baseline), `cheapest_window`, `cleanest_window`, `compare`. Sliding-window minimum, no solver, no ML, per the reasoning already recorded in Open Questions.
  - `tests/test_grid.py`: 15 offline tests, all passing, zero network. Includes the rule that a window with missing price data is never selected — an unpriced window is not a cheap one.
- **Measured a real result:** 6.5 kW job, 4 h, 24 h deadline, GB 2026-08-05→08 → £3.21 to £0.21 and 2.10 to 0.97 kgCO₂. **93.6% cost, 54.0% carbon, for an 11.5 h delay.** Steps 5-7 substantially complete.
- **Found that the two objectives genuinely conflict**: on real data the carbon-optimal window sometimes costs *more* than running immediately. Logged in Current State — the scheduler must be told its objective, it cannot infer it.
- **Decided the deployment architecture** — see "UI and deployment" above. Short version: the real system cannot be a web portal because browsers cannot read hardware power draw, but a local process serving a browser UI gives both halves. Rejected Streamlit for visual control; the dashboard is hand-written HTML/CSS/SVG.
- **Built `app/dashboard.py`**, an Apple-styled dashboard rendered from live market data. Palette validated for colour-blind separation and contrast in both light and dark. Rendered and inspected both modes, which caught three defects that unit tests could not: axis ticks labelling padded bounds rather than real data (a price series with a £2.84 floor was showing "-16"), a stray hover dot parked at the origin because SVG ignores the HTML `hidden` attribute, and a "the objectives disagree" panel firing on a £0.00 trade-off. All fixed. Worth repeating the habit: render the thing and look at it.
- **Added `app/serve.py`** — a local server on `http://localhost:8765`, bound to loopback only, that rebuilds the page from live data on reload (5-minute cache; first load 1.3s, cached load 0.9ms). This is the normal way to run it; `app.dashboard` remains for rendering a single shareable file. Clarified in the doc that "in the browser" is not "on the web" — both paths are entirely local, and that is precisely what lets the real system read hardware power draw later.
- **Recorded that `demo/` is a prototype, not a spec** — see the section above, added so this doesn't have to be re-explained in every future session.
