# Grid-Aware Scheduler

Decides **which accelerator runs which job, and when**, using live electricity price and carbon-intensity data. Built for a data-centre operator with a carbon-neutrality target.

**→ Read [`HANDOFF.md`](HANDOFF.md) first.** It is the single source of truth: full state, prior art, design decisions, measured findings, and everything still outstanding. Keep it current at the end of every session.

## Run it

```bash
python3 -m venv ~/venvs/national-grid
~/venvs/national-grid/bin/python -m pip install -r "../National-Grid-Tool/requirements.txt"

~/venvs/national-grid/bin/python -m core.backfill --days 400   # one-time, ~2 min
~/venvs/national-grid/bin/python -m app.serve                  # http://localhost:8765
```

`/` is the grid terminal, `/simulator` the model-on-hardware simulator. Everything is local — nothing is hosted, and the server binds to loopback only.

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
adapters/   one file per market — GB national, GB regional, worldwide weather
core/       market-agnostic: scheduling, workload maths, analytics, cache
hardware/   57 accelerators across 6 vendors, with provenance on every figure
app/        the two pages, charts, panels, and the local server
tests/      37 tests, no network required
```

## Honesty rules

Every figure carries where it came from — `MEASURED`, `SPEC`, `ESTIMATED`, `SIMULATED` — and model sizes carry `PUBLISHED` / `REPORTED` / `UNPUBLISHED`, because frontier labs no longer publish parameter counts and a simulator that treats a rumour as a datasheet is worth nothing.

This is an applied synthesis of published work (Zeus, Perseus, Google's carbon-intelligent computing, Compute Gardener), not a new category, and it does **not** generate carbon credits. See HANDOFF.md.
