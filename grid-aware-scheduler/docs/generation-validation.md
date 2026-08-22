# Validating the generation model

## Why this exists

The product's central claim is a **timing** claim: put the heaviest work in the
window the plant feeding the site produces most. On the grid that window is
where supply is most abundant, which is also where price is lowest — price is
the readable proxy, not the goal. Behind a declared plant it is wherever that
plant is actually generating, which is a physical question about sun and wind.

So the scheduler is only as good as its answer to "when will this plant
produce most?". `core/renewables.py` answers it locally, from Open-Meteo
irradiance and hub-height wind, because a planner that recomputes on every
slider drag cannot wait on a rate-limited API. Its own docstring is honest that
this costs accuracy and names Renewables.ninja as the cross-check that was never
run. This document is that cross-check.

## Level and shape are different failures

They are reported separately because they break different things, and a single
blended accuracy score would hide the distinction.

**Level error is a capacity error.** If a 10 MW array is modelled at 6 MW when
it delivers 4 MW, the site's power envelope is overstated and the scheduler
admits work the plant cannot carry.

**Shape error is a timing error**, and timing is the product. A model can be
30% high at *every* hour and still place every job perfectly, because a
constant factor cancels out of an argmax.

The metric that decides whether an error mattered is neither: it is **window
agreement**. Given a job of some duration, does the local model choose the
window the reference would have chosen, and if not, how much generation did
that cost? A model can be wrong everywhere and never change an answer. This is
the same reasoning as `core/backtest.py`, which scores decisions rather than
forecasts.

## First run, and why it supports no bias correction yet

Scored against the captured London fixture (51.5°N, -0.12°E, 2023-06-01), eight
overlapping hours:

| | solar | wind |
|---|---|---|
| Local mean vs reference | 0.027 vs 0.069 | 0.310 vs 0.461 |
| Ratio | 0.40x | 0.67x |
| Shape correlation | **0.993** | **-0.379** |
| Peak offset | 0 h | +5 h |
| Window agreement | **100%** | 0% |

**The level figures must not be used as correction factors, and the wind shape
figure is not a finding about the local model.** Reading the hours individually
is what shows why, and both causes are comparability errors rather than model
errors:

1. **The overlap is eight hours from midnight**, of which five are fully dark
   and the other three are low-angle morning sun (23, 85 and 155 W/m² against a
   London June midday peak above 800). The window contains almost none of the
   day's generation, so no annual bias can be read off it.
2. **The two models simulate different objects.** Renewables.ninja was asked
   for a 35°-tilted, south-facing array; `core/renewables.py` treats global
   horizontal irradiance as though it fell on the panel. A tilted south-facing
   surface collects far more than a horizontal one at low sun angles, which is
   exactly where this sample sits. That is a geometry difference, not an error
   to correct away.
3. **The reanalyses differ in time resolution.** MERRA-2 is three-hourly, and
   the raw fixture shows it — the first three wind hours are identical at 0.494
   while ERA5's hourly wind genuinely varies. Correlating an hourly series
   against an interpolated three-hourly one over eight hours measures the
   interpolation, not the model. The negative correlation is an artefact of
   that, on a sample far too short to mean anything.

**What the run does establish** is the reassuring half, and it is the half the
product rests on: for solar the shape correlation is 0.993 with a zero-hour
peak offset and 100% window agreement, so the local model already places solar
work in the right window even while its level is wrong. Timing survives a level
error, which is precisely what the level/shape split predicted.

## What has to happen before a correction factor is claimed

1. **Register a token.** Free at renewables.ninja; set `RENEWABLES_NINJA_TOKEN`.
   Nothing here can run live without it. This is a blocking input.
2. **Add plane-of-array transposition to `core/renewables.py`**, or request a
   horizontal simulation (`tilt=0`) from the reference. Comparing a tilted array
   against horizontal irradiance is not a valid comparison in either direction.
3. **Run a full year, not a day**, at several sites spanning latitudes and
   climates. A year in one call stays inside the 50-per-hour allowance.
4. **Compare at three-hourly resolution for wind**, or accept that hourly
   correlation against MERRA-2 is partly measuring interpolation.

Until those are done the local model stays `ESTIMATED`, which is what
`core/site_profile.py` already caps a modelled shape at regardless of how well
the capacity behind it is known.

## Running it

```bash
export RENEWABLES_NINJA_TOKEN=...
python -m core.generation_check --lat 51.5 --lon -0.12 \
    --start 2024-01-01 --end 2024-12-31 --source solar
```

Renewables.ninja is historical only — MERRA-2 reanalysis, published in arrears.
The adapter **refuses a future date** rather than clamping it, because the one
genuinely dangerous mistake here would be letting a reanalysis product look
like a forecast. The division of labour is fixed: the reference calibrates the
local model over history, and the calibrated local model runs forward on
Open-Meteo forecasts at decision time.
