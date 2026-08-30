# Workload types and the general orchestration layer

## What this product is

**A general workload-to-power orchestration platform, not an AI carbon
calculator.** It decides which computational work runs where, when, and on
which physically available supply of electricity. The work can be AI training
or inference, but it can equally be proof-of-work mining, 3D rendering, HPC
simulation, ETL, or anything else that draws power and has some flexibility
about when it runs.

## The finding this layer is built on

Before extending anything, the existing scheduler was read to see how much of
it was actually AI-shaped. **Very little is.**

- `core/portfolio.py` schedules a `PortfolioJob`: `work_amount`, `work_unit`,
  `utility`, `earliest_start`, `deadline`, `depends_on`. No models, no tokens.
- `core/planner.py` places a `PlanningCandidate`: `runtime_hours`,
  `it_power_kw`, `pue`. It does not know what the work is.
- `core/energy.py` already models solar, wind, hydro, nuclear, geothermal,
  biomass, gas, coal, oil and grid supply, with battery storage, contractual
  delivery (PPA) and per-interval dispatch.

The AI assumptions lived above those, in `core/workload.py`'s two-value `Task`
enum, in the estimator, and in the interface. So this extension **adds a layer
and changes no scheduling logic**. That is why the exact planner did not need
re-testing for correctness: it sees the same contract it always did.

## Structure

```
core/workload_types.py   types, fields, flexibility, resources, compilers
core/objectives.py       named objectives -> planner weights, or a refusal
core/mining.py           dispatch for continuous revenue work
core/orchestration.py    the seven-step flow and the counterfactual
```

### Adding a ninth workload type

Add one `WorkloadDefinition` to `DEFINITIONS`: a label, its type-specific
fields, and optionally a deriver that turns those fields into duration, power
and work amount. Nothing else changes — not the planner, not the portfolio
scheduler, not the API, and not the interface, which builds its form from
`FieldSpec` rather than from hand-written markup per type.

### The eight types

| Type | Work unit | Duration derived from | Continuous |
|---|---|---|---|
| `ai_training` | optimizer_steps | caller | no |
| `ai_inference` | tokens | caller | no |
| `mining` | terahashes | n/a — no deadline | **yes** |
| `rendering` | frames | frames x seconds / parallel workers | no |
| `hpc` | core_hours | core-hours / cores | no |
| `data_processing` | gigabytes | dataset / throughput | no |
| `batch` | tasks | caller | no |
| `custom` | tasks | caller | no |

### Energy sources

Already present in `core/energy.py` before this work and unchanged: `solar`,
`wind`, `hydro`, `nuclear`, `geothermal`, `biomass`, `gas`, `coal`, `oil`,
`grid`, `other`, with delivery types `onsite`, `dedicated_wire`, `grid` and
`contractual` (the PPA case), plus `BatterySpec` for storage. A facility
declares any combination through `core/site_profile.py`.

## Why mining is separate

Every other workload is *fixed work, fixed deadline, choose a window*. Mining
is *no work, no deadline, choose whether this hour pays*. A rig can run every
hour of the year or none of them.

`core/mining.py` compares, per interval:

```
revenue  >  energy cost + operating cost + opportunity cost
```

The fourth term is the one naive models omit. If the site can export power or
store it, electricity a miner consumes has value even when it was not bought.
Above a certain export price the rational act is to sell and stop hashing —
on-site power is only free if it had nowhere else to go. A behind-the-meter
site that ignores this will over-mine in exactly the intervals when its power
is worth most.

The counterfactual is **always-on**, not idle, because a miner's default is to
run constantly. On a mock twelve-hour curve with an evening peak, curtailing
four intervals took margin from 5.05 to 9.39.

Revenue per TH/s per day is an **operator input**. This project has no feed for
network difficulty or coin price, and inventing one would make every result
fictional.

## Two claims this layer refuses to make

**Cheap electricity does not make hardware faster.** A workload placed in a
cheap window takes exactly as long as it would in an expensive one. Every
`PlanningCandidate` compiled from one workload carries the same
`runtime_hours`, and a test asserts it. Work finishes sooner only if the
platform allocates **more hardware or more power headroom** — a capacity
decision, expressed through `ResourceRequest`'s min/max range and reported
separately as `headroom_available`.

**A window with missing data is not a cheap window.** Any interval without a
price disqualifies the whole window rather than being treated as free.

## Objectives

| Objective | Served by |
|---|---|
| Lowest electricity cost | planner weights (1, 0, 0) |
| Lowest carbon | planner weights (0, 1, 0) |
| Balanced | planner weights (0.5, 0.5, 0) |
| Custom weighted | caller-supplied weights |
| Maximum on-site renewable | **refused** — `core/energy.dispatch_energy` |
| Maximum operating profit | **refused** — `core/mining.dispatch` |

The last two refuse rather than approximate. Grid carbon and on-site
generation are different signals: a site can be at its cleanest *grid* hour
while its own array produces nothing, so serving "maximum renewable" with
carbon weights would answer a different question with a confident number.

## Status

**Read-only recommendations only.** This layer produces a recommended schedule
and a counterfactual saving. It launches nothing, defers nothing and cancels
nothing. `docs/commercial-readiness.md` remains accurate: workload execution
is Not built; security, reliability and deployment are Development.

**Not production-ready**, and nothing in this document should be read as
saying otherwise. What is demonstrated is what the tests demonstrate.
