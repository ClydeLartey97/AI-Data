# Placement algorithm and grid data contract

This document describes what the planner decides, how it produces the result,
and where its current precision stops. It is intended to let an operator,
reviewer or future API consumer reproduce a recommendation without trusting a
black box.

## Decision boundary

The planner selects a hardware configuration and start time at an
operator-selected facility location. The market-agnostic engine can also rank
multiple locations when candidates share one currency, but the current page
treats location as a facility constraint because a workload cannot be assumed
portable between sites.

For each candidate, the engine receives:

| Input | Unit | Meaning |
|---|---:|---|
| Runtime | hours | Estimated wall-clock time for the workload and hardware |
| IT power | kW | Accelerator power before facility overhead |
| PUE | ratio | Facility energy divided by IT energy |
| Memory feasible | boolean | Whether weights, training state or inference KV cache fit |
| Grid series | half-hourly | Price and carbon intensity at the selected market/location |
| Deadline | hours | Latest permitted finish relative to the first interval |
| Objective weights | unitless | Explicit relative weights for cost, carbon and delay |
| Policy caps | currency, kg, hours | Optional maximum cost, carbon or start delay |

## Exact enumeration

For candidate `i` and legal start `s`, facility power and energy are:

```text
facility_power_i = IT_power_i × PUE_i
facility_energy_i = facility_power_i × runtime_i
```

The engine rejects a candidate before scoring if memory does not fit or its
runtime exceeds the deadline. It then enumerates every contiguous half-hour
start for which:

```text
start_s + runtime_i <= first_interval + deadline
```

For each half-hour `t` in that run:

```text
period_hours_t = min(0.5, remaining_runtime)
period_energy_t = facility_power_i × period_hours_t
period_cost_t = period_energy_t × price_t / 1,000
period_carbon_t = period_energy_t × intensity_t
```

The final period is fractional when runtime is not a multiple of thirty
minutes. A window containing a timestamp gap or a required missing signal is
rejected. Missing data is never converted to zero.

Optional hard policy caps are applied after signal completeness and before
normalisation. A window exceeding `max_cost`, `max_carbon_kg` or
`max_delay_hours` is infeasible regardless of its weighted score. This lets an
operator express rules such as "minimise carbon, but never spend more than
£20" without hoping that a relative weight approximates the rule.

## Multi-objective score

Cost, carbon and delay have different units. Each is min-max normalised across
the feasible option set:

```text
normal(x) = 0                              if maximum = minimum
normal(x) = (x - minimum) / (maximum - minimum) otherwise
```

The score is then:

```text
score = (w_cost × normal(cost)
       + w_carbon × normal(carbon)
       + w_delay × normal(delay))
       / (w_cost + w_carbon + w_delay)
```

The lowest score wins. Ties are broken deterministically by cost, carbon,
finish time and candidate key. At least one weight must be positive. Costs in
different currencies cannot be ranked until an explicit conversion policy is
provided, so the engine rejects mixed-currency candidate sets.

The cost/carbon Pareto frontier is also marked. A frontier option is not beaten
by another option on both cost and carbon, with at least one strict
improvement. The implementation sorts by cost and scans the best carbon seen,
so frontier calculation is `O(n log n)` rather than a quadratic pairwise scan.

## Grid precision and provenance

The common scheduler sees only timestamp, price and carbon. Market-specific
meaning remains in the adapter and page context.

| Market view | Price precision | Carbon precision | Current planning mode |
|---|---|---|---|
| Great Britain national | National Market Index price | National forecast | Recent complete replay |
| Great Britain region | National Market Index price | One of fourteen grid regions | Recent complete replay |
| California ISO | Selected trading hub, load aggregation point or custom PNode | CAISO balancing-area consumption rate, with production fallback | Recent complete replay |
| New York ISO | One of eleven NYISO load zones | NYISO balancing-area consumption rate, with production fallback | Recent complete replay |

GB does not have a separate wholesale price for each displayed carbon region.
The product therefore changes regional carbon while keeping the national price
fixed.

CAISO day-ahead locational marginal price comes from the official OASIS
interface. The preferred carbon value is EIA-930's published consumption rate,
which models interchange. That series is delayed, so recent periods use a
production estimate calculated from CAISO hourly generation by known fuel and
EIA operational emissions factors. The fallback excludes imports and
unclassified "other" generation. Neither measure is a nodal marginal-emissions
signal. The interface states the active method where the value is shown.

NYISO day-ahead zonal LBMP comes from its official keyless MIS daily files.
The selected zone changes price, while EIA-930 carbon remains scoped to the
NYISO balancing area. It is not presented as zonal carbon.

Primary data references:

- [CAISO OASIS API specification](https://www.caiso.com/documents/oasisapispecification.pdf)
- [EIA Hourly Electric Grid Monitor methodology](https://www.eia.gov/electricity/gridmonitor/about)
- [EIA carbon dioxide factors by fuel](https://www.eia.gov/tools/faqs/faq.php?id=74&t=11)
- [GB Carbon Intensity API](https://carbon-intensity.github.io/api-definitions/)
- [Elexon Insights API](https://bmrs.elexon.co.uk/api-documentation)
- [NYISO pricing data directory](https://mis.nyiso.com/public/csv/damlbmp/)

## Complexity

With `C` hardware/location candidates and `H` half-hour intervals, enumeration
is `O(C × H × R)`, where `R` is the number of periods in one runtime. The
current interactive problem is small enough for exact search and benefits from
its auditability. If the candidate set grows to thousands of sites or the
horizon grows to months, rolling window sums reduce the repeated `R` work
without changing the decision rule.

## Current limits before production use

- The page uses historical replay because a complete forward price curve is
  not yet available through every current public source. It must not be sold as
  a dispatch forecast.
- Hardware runtime and power remain catalogue estimates unless an exact
  device/model/task/precision/count/software-stack profile has at least three
  empirical runs. The current calibration path carries robust variation but
  trusted automatic telemetry collection is not yet built.
- PUE is a user-selected constant. Production use should accept site and
  time-specific PUE plus cooling and power-cap constraints.
- The current carbon calculation covers operational electricity emissions, not
  embodied hardware emissions or lifecycle accounting.
- No workload launcher, admission controller, retry policy, authentication,
  tenancy, production audit store, metering reconciliation or billing
  integration exists yet. A durable local decision journal is built.
- The market-agnostic backtest engine separates forecast-time decisions from
  realised scoring, immediate baselines and perfect-hindsight regret. Decision
  inputs, feasible-candidate parameters and signal snapshots are persisted;
  automatic realised-outturn ingestion is not wired yet. It is required before
  savings claims can support a customer contract.

These limits do not invalidate the optimiser. They define the remaining work
between an auditable decision prototype and an operational control plane.
