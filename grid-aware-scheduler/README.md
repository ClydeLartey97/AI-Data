# Grid-Aware Scheduler

Decides **which accelerator runs which AI workflow stage, where, when and on
which physically available energy supply**, using workload evidence, facility
constraints, generation forecasts, storage, electricity price and regional
carbon data.

**→ Read [`HANDOFF.md`](HANDOFF.md) first.** It is the single source of truth: full state, prior art, design decisions, measured findings, and everything still outstanding. Keep it current at the end of every session.

## Install it

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/grid-aware-scheduler          # http://localhost:8765
```

Three dependencies — `pandas`, `requests`, `psutil`. Everything else is
standard library. CAISO, NYISO and MISO work immediately: each talks to its
market operator directly, and none of them needs a key.

**GB is the one exception.** Its price and carbon come through a sibling
checkout of the National Grid Tool, which already had tested Elexon and Carbon
Intensity clients. That import is deferred to the first GB fetch, so a machine
that only plans US markets never needs the folder. To enable GB, check that
project out beside this one or point `NATIONAL_GRID_TOOL_PATH` at it, then
install its requirements into the same environment.

Apple-silicon measurement is optional and separate — `pip install '.[apple]'`
pulls MLX. It is needed only to *produce* `MEASURED` profiles; planning against
published or catalogue figures needs none of it.

On the configured Mac, **Grid-Aware Scheduler.app** on the Desktop does the
same thing without a terminal: it reuses a healthy local process or starts one,
waits for its health check, and opens the product. Rebuild it with
`python scripts/install_launcher.py`. Optionally prime the local cache once
with `python -m core.backfill --days 400` (about two minutes).

The local product has five linked operator surfaces:

- `/` is the AI data-centre operations home. It begins with workload demand,
  quality/evidence state, SLA and memory readiness, facility capacity and an
  exact queue recommendation. Its generation-aware layer models solar, wind,
  hydro, nuclear, geothermal, biomass, thermal generation, residual grid,
  time-varying PUE, base demand and storage, with exact operator-declared site
  and source coordinates, delivery losses and an earliest-run comparison.
- `/simulator` is the Fleet Lab. It estimates model runtime, memory, facility energy, cost and carbon
  across the hardware catalogue.
- `/planner` is the Placement Lab. It searches every feasible hardware and half-hour placement against
  explicit cost, carbon and delay weights.
- `/grid` is the detailed Sites & Grid terminal for regional electricity price,
  carbon and interactive market analytics.
- `/decisions` is the immutable decision journal, including forecast versus
  realised evidence and exact JSON export.

Every page has a persistent Light/Dark switch in the top-right. The preference
is shared locally across pages and survives reopening the product.

Everything is local. Nothing is hosted, and the server binds to loopback only.
Use `?market=GB&location=london` for regional GB carbon or
`?market=CAISO&location=sp15` for California nodal pricing. CAISO also accepts
an exact PNode through the location control. New York's eleven price zones use
`?market=NYISO&location=nyc` or another NYISO zone from the selector.
Facility coordinates and grid-connection identity are separate inputs. They
identify the physical site precisely without falsely increasing the spatial
resolution of the selected provider price or carbon feed.

The planner can export a review link, an auditable plan JSON file and ranked
alternatives as CSV. Local integrations can use the versioned endpoints at
`/api/v1/health`, `/api/v1/market`, `/api/v1/plan` and `/api/v1/portfolio`; see
[`docs/api.md`](docs/api.md).

## What it has measured

On 392 days of real GB data:

| | |
|---|---|
| Cost saved, 4 h job, 24 h deadline | **21.9% median** (75% at a week) |
| Carbon saved, same job, 24 h | **28.5% median** |
| Price range across the year | **£0.09 – £560.81** (6,231×) |
| Regional carbon spread, same instant | **0 – 358 gCO₂/kWh** |
| Price/carbon correlation | **r = 0.54** — cheapest decile is cleanest only **59%** of the time |

That last row is the load-bearing one: an operator optimising purely on price misses their carbon target 41% of the time, so the objective has to be stated rather than inferred.

## Layout

```
adapters/   GB national/regional, CAISO nodal and NYISO zonal market boundaries
core/       market-agnostic: exact planning, workload maths, analytics, cache
hardware/   57 accelerators across 6 vendors, with provenance on every figure
app/        five linked operator surfaces, charts, market context, and the local server
tests/      offline algorithm, page, persistence and HTTP contract tests
```

The exact planner, objective function, data semantics and current production
limits are documented in [`docs/planner.md`](docs/planner.md).
Quality-constrained model routing is documented in
[`docs/routing.md`](docs/routing.md). Local hardware discovery measures device
identity and installed memory while keeping performance and power provenance
separate. Exact-fingerprint empirical calibration is documented in
[`docs/calibration.md`](docs/calibration.md).
The modality-neutral evidence schema and multi-job capacity algorithm are
documented in [`docs/workload-optimisation.md`](docs/workload-optimisation.md).
Physical generation, battery dispatch, checkpointable work, operator energy
objectives and counterfactual reporting are documented in
[`docs/energy-dispatch.md`](docs/energy-dispatch.md).
The local governed workload evidence registry, Apple measurement collector and
measured-profile scheduling boundary are documented in
[`docs/apple-measurement-protocol.md`](docs/apple-measurement-protocol.md).
Optional Apple profiling dependencies are pinned in `requirements-apple.txt`.

The exact boundary between the current pilot foundation and a production
control plane is documented in
[`docs/commercial-readiness.md`](docs/commercial-readiness.md).

## Honesty rules

Every figure carries where it came from — `MEASURED`, `SPEC`, `ESTIMATED`, `SIMULATED` — and model sizes carry `PUBLISHED` / `REPORTED` / `UNPUBLISHED`, because frontier labs no longer publish parameter counts and a simulator that treats a rumour as a datasheet is worth nothing.

This is an applied synthesis of published work (Zeus, Perseus, Google's carbon-intelligent computing, Compute Gardener), not a new category, and it does **not** generate carbon credits. See HANDOFF.md.
