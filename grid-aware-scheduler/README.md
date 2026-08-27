# AI Data Centre Optimiser

**Decides which accelerator runs which AI workload, where, when, and on which
physically available supply of electricity.**

AI facilities are power-constrained rather than clock-constrained. The binding
limit on a datacentre is increasingly its grid connection, not its capital
budget — so the question worth answering is not "how fast is this chip" but
*how much useful work can be done per megawatt, and when should it be done.*

This is that engine. It joins measured hardware capability, real electricity
price and carbon data from five markets, on-site generation forecasts and
half-hourly facility power limits into one exact, auditable placement
decision.

**→ Read [`HANDOFF.md`](HANDOFF.md) first.** It is the single source of truth:
full state, prior art, every design decision with its reasoning, measured
findings, and everything still outstanding.

---

## The idea in one line

Electricity is cheapest when supply is most abundant, so **price is a readable
proxy for generation strength** — and the heaviest work belongs where and when
generation is strongest. A data centre plugged into a solar plant should run
its hardest job when the sun is highest.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/grid-aware-scheduler          # http://localhost:8765
```

Three dependencies — `pandas`, `requests`, `psutil`. Everything else is
standard library. **CAISO, NYISO, MISO and ERCOT work immediately**: each
talks to its market operator directly and none needs an API key.

GB is the one exception. Its price and carbon come through a sibling checkout
of an earlier power-market data project, so that import is deferred to the
first GB fetch — a machine planning only US markets never needs the folder.
Point `NATIONAL_GRID_TOOL_PATH` at it to enable GB.

Apple-silicon measurement is optional and separate (`pip install '.[apple]'`,
which pulls MLX). It is needed only to *produce* `MEASURED` profiles. Planning
against measured, published or catalogue figures needs none of it.

Working in a cloud container? A devcontainer is included and needs no
configuration. MLX is deliberately excluded there — it has no Linux wheels,
and the measurement this project owns already travels with the source.

---

## What it has measured

### Grid timing, on 392 days of real GB data

| | |
|---|---|
| Cost saved, 4 h job, 24 h deadline | **21.9% median** (75% at a week) |
| Carbon saved, same job, 24 h | **28.5% median** |
| Price range across the year | **£0.09 – £560.81** (6,231×) |
| Regional carbon spread, same instant | **0 – 358 gCO₂/kWh** |
| Price/carbon correlation | **r = 0.54** — the cheapest decile is cleanest only **59%** of the time |

That last row is load-bearing: an operator optimising purely on price misses
their carbon target 41% of the time, so **the objective has to be stated, not
inferred.**

### The honest ceiling, on a real production trace

Replaying **83,152 real GPU training jobs** (Microsoft's Philly trace, 2.48
million GPU-hours) against real GB market data gives **0.45% cost and 0.51%
carbon saved** when each job may move only inside the queueing delay its own
cluster already spent — so nothing finishes later than it actually did.
Declaring a 24-hour deadline nobody declared in 2017 raises it to 6.88%.

**Quote that figure, not the synthetic one.** The project's own early headline
was 93.6% — one hand-picked job in one hand-picked window. Across a real
workload the honest number is single digits.

**Why**, and it redirected the whole product: **3.5% of jobs are longer than
24 hours and consume 90.9% of all GPU-hours.** A job that runs longer than its
deadline cannot be shifted at all. Time-shifting is structurally incapable of
touching where the energy actually is — so placement and location do the heavy
lifting, and the catalogue already shows a 20× energy spread for identical
work.

### Hardware, measured rather than assumed

An Apple M2 was measured across three preflight-validated runs: **2,583
GFLOP/s** dense fp16 GEMM and **75.7 GB/s** streaming read, 0.3% spread. That
is 89.7% of its published arithmetic peak and 75.7% of its published bus.

Those two constants are enough to model any transformer, because its two
phases are each bounded by one of them — decode by bandwidth, prefill by
arithmetic. Validated against published MLPerf submissions: the decode formula
reproduces H200, H100, B200 and MI300X per-accelerator throughput at implied
batch sizes of 63, 81, 54 and 37 — four vendors, one equation.

---

## Modelling a datacentre you cannot visit

Apple's AI servers are built from Apple silicon: rack chassis of small,
individually removable compute boards, each with one SoC and its own memory.
**That is the same silicon anyone can buy and measure.**

So a fleet built from a part you can hold is a fleet you can characterise
without physical access to it. Measure one chip, apply the published core and
bus ratios, apply a stated scaling law, and you get a defensible estimate of
what a rack does per kilowatt — no site visit, no vendor briefing. That
position exists *only* because the retail part and the server part are the
same design, which is not true for anyone shipping discrete GPUs.

Three rules keep it honest, and each is enforced in code rather than noted in
a comment:

**Boards do not share memory.** This is the property most likely to be got
wrong by analogy with GPU racks. Eight H200s on NVLink present as one pool;
thirty-two Apple boards on a backplane do not. A 70B model at 4-bit needs
42.2 GB and **will not run on a 24 GB board even though the chassis holds
768 GB in total.** The planner refuses rather than aggregating, because
aggregating would turn a physically impossible deployment into an attractive
number.

**Throughput replicates for independent requests only.** Inference serving
needs no gradient synchronisation, so it scales near-linearly — measured
across 83 multi-accelerator MLPerf curves. Splitting one model across boards
is refused outright, because the backplane is unmeasured.

**Geometry is declared, never assumed.** Published board counts are recorded
with their source attached, and what was *not* published is stated as not
published rather than guessed.

Scaling across Apple's own generations is separated into two tiers with
different licences. `DERIVED` scales a measured per-core rate to a sibling
built from the same core. `PROJECTED` crosses a generation boundary, where
that licence does not exist — so no rate travels, only the *achieved fraction*
of published peak, which is a property of the toolchain rather than the core.
A projection can never outrank a measurement.

Multi-die packages pay per crossing, compounding: a two-die part scales at
0.90, a quad-die at 0.81. A flat two-die derate applied to a quad-die package
overstates it by 11%.

---

## The product

Five linked local operator surfaces, plus a versioned JSON API:

- **`/`** — AI datacentre operations. Workload demand, quality and evidence
  state, SLA and memory readiness, facility capacity, and an exact queue
  recommendation. Models solar, wind, hydro, nuclear, geothermal, biomass and
  thermal generation, time-varying PUE, base demand and storage, with declared
  site coordinates and delivery losses.
- **`/simulator`** — Fleet Lab. Runtime, memory, energy, cost and carbon
  across the hardware catalogue.
- **`/planner`** — Placement Lab. Searches every feasible hardware and
  half-hour placement against explicit cost, carbon and delay weights.
- **`/grid`** — Sites & Grid terminal. Regional price, carbon and interactive
  market analytics.
- **`/decisions`** — the immutable decision journal, with forecast-versus-
  realised evidence and exact JSON export.

Everything is local. Nothing is hosted, and the server binds to loopback only.

```
?market=GB&location=london          regional GB carbon, national price
?market=CAISO&location=sp15         California nodal pricing
?market=NYISO&location=nyc          New York zonal LBMP
?market=MISO&location=indiana       Midcontinent hub LMP
?market=ERCOT&location=houston      Texas hub / load zone
```

## Markets

| Market | Price granularity | Key required | Carbon |
|---|---|---|---|
| GB | National, half-hourly | no | 18 regions, forecast |
| CAISO | Nodal (validated PNodes) | no | EIA-930 balancing area |
| NYISO | 11 zones | no | EIA-930 balancing area |
| MISO | 8 hubs | no | EIA-930 balancing area |
| ERCOT | 7 hubs, 8 load zones | **no** | EIA-930 balancing area |

Carbon is always explicitly balancing-area scoped and never relabelled as
nodal. GB settles one national wholesale price, so a GB location control
drives carbon and weather only — never presented as though it varied price.

## Layout

```
adapters/   five market boundaries, weather, and per-plant availability
core/       market-agnostic: exact planning, energy dispatch, audit, backtest
hardware/   catalogue, measurement, roofline prediction, cross-part scaling
app/        five operator surfaces, charts, JSON API, loopback server
tests/      478 offline algorithm, page, persistence and HTTP contract tests
```

Deeper documentation lives in [`docs/`](docs/): the exact planner equations and
limits, quality-constrained routing, calibration, the evidence schema, energy
dispatch, the site-profile boundary, generation validation, and the explicit
boundary between this pilot foundation and a production control plane.

## Honesty rules

Every figure carries where it came from — `MEASURED`, `PUBLISHED`, `DERIVED`,
`PROJECTED`, `REPORTED`, `SPEC`, `ESTIMATED`, `SIMULATED` — and model sizes
carry `PUBLISHED` / `REPORTED` / `UNPUBLISHED`, because frontier labs no
longer publish parameter counts and a simulator that treats a rumour as a
datasheet is worth nothing.

A derived figure is only ever as good as the weakest input behind it, and a
measurement always wins.

This is an applied synthesis of published work — Zeus, Perseus, Google's
carbon-intelligent computing, Compute Gardener — **not a new category**, and it
does **not** generate carbon credits. It is advisory: it must not yet be
trusted to launch or defer a customer's workload. See
[`docs/commercial-readiness.md`](docs/commercial-readiness.md) for the exact
boundary, and `HANDOFF.md` for the prior art it builds on.
