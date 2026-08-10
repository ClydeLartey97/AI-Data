# AI workload evidence and portfolio optimisation

## Product decision

The product does not choose between AI telemetry and electricity-market data.
It joins them.

Measured workload evidence answers:

- how much useful work an execution option completes;
- whether it meets a versioned quality floor;
- how long it runs;
- how much IT energy and memory it uses;
- which model, precision, compute unit, device and software stack produced the result.

Grid evidence answers:

- what electricity costs at the facility and time;
- how carbon intensive electricity is at the facility and time;
- whether a proposed run meets an operator's cost and emissions policy.

For execution option `i` at time `t`:

```text
runtime_i = required_work / measured_work_rate_i
facility_energy_i = measured_IT_power_i × PUE_i × runtime_i
cost_i,t = sum(period_energy_i,t × price_t / 1,000)
carbon_i,t = sum(period_energy_i,t × carbon_intensity_t) / 1,000
```

Low price is not treated as evidence of low emissions. Price and carbon remain
separate inputs and separate outputs.

## Privacy-preserving measurement contract

`core/evidence.py` defines `workload-evidence-v1`. A run records only:

- run, workload, model and version identifiers;
- run mode, precision, compute unit and content-free shape fingerprint;
- device and software-stack fingerprints without host serial numbers;
- useful-work amount and unit;
- duration, IT energy, derived power, peak memory and thermal state;
- native task-quality value plus a suite-defined normalised score;
- versioned evaluation suite and observation time.

Prompts, text, images, audio, labels and customer content are not fields in the
contract. Shared telemetry must remain metadata-only.

Supported useful-work units in the first schema are tokens, images, audio
seconds, samples, training examples and optimiser steps. The optimiser never
adds unlike units. It reports each unit separately.

At least three runs with one exact workload, model, version, precision,
device, compute-unit, stack, shape and evaluation fingerprint are required to
form a measured profile. The profile uses robust medians and records relative
median absolute deviation for throughput and energy. A fingerprint mismatch
cannot inherit measured status.

## Portfolio objective

`core/portfolio.py` schedules several quality-qualified jobs against explicit
facility power limits. A job can be mandatory or optional. Each job carries an
operator-defined utility so that the product does not pretend one token has the
same service value as one image or one second of audio.

The exact objective is lexicographic:

1. schedule every feasible mandatory job;
2. maximise completed operator utility across optional jobs;
3. minimise operational carbon;
4. minimise electricity cost;
5. minimise delay;
6. apply a deterministic key tie-break.

Total cost and carbon budgets are hard constraints. A facility's instantaneous
power capacity is enforced in every occupied half-hour. Candidate options with
missing required price or carbon, timestamp gaps, memory failure, missed
deadlines or missing facility capacity fail closed.

The result includes assignments, unscheduled jobs, completed work by unit,
completed utility, facility energy, cost, carbon, delay, search-space bound and
the number of complete schedules considered.

The AI Operations contract also carries workload class, run mode,
model/version, precision, compute unit, declared memory fit, quality suite and
evidence provenance. Each selected assignment reports energy, carbon and cost
per native useful-work unit. These ratios remain unit-specific and are never
summed across tokens, images, audio seconds, examples or optimiser steps.

## Environmental claim

The first defensible outcome measure is absolute operational carbon avoided
against running the same quality-qualified workload immediately:

```text
carbon_avoided = realised_immediate_carbon - realised_selected_carbon
```

It must be calculated from realised grid data after execution. Forecast carbon
is a decision input, not proof of an achieved saving. `core/backtest.py`
already preserves this boundary for one job. Portfolio-level realised scoring
is still required before the dashboard can make a multi-job savings claim.

Average grid carbon is currently available. Marginal emissions would better
represent the effect of changing demand, but dependable regional marginal
signals are not yet wired for every supported market. The interface must label
the distinction rather than presenting average carbon as marginal impact.

## Exactness boundary

The pilot portfolio solver uses bounded exhaustive search. It is exact and
auditable for a small workload queue. It rejects a request when the unpruned
combination bound exceeds the configured limit rather than silently returning
a heuristic result.

A production fleet with thousands of jobs needs a time-indexed mixed-integer
or constraint-programming implementation, rolling-horizon re-planning and a
published optimality gap. That future solver must retain this input contract,
policy boundary and auditable baseline.
