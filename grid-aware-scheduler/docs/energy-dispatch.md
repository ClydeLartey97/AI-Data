# Generation-aware AI workload dispatch

The energy layer answers two separate questions for every half-hour:

1. when can a quality-qualified AI workflow run without breaking its dependency,
   hardware, memory, power or deadline constraints;
2. which physical energy sources can serve the fixed facility load and the
   resulting flexible AI load at that time.

The implementation is source-agnostic. Solar is one input, not the scheduling
rule. Supported source kinds are solar, wind, hydro, nuclear, geothermal,
biomass, gas, coal, oil, residual grid and an explicitly labelled other class.

## Interval model

For source `s` and interval `t`, usable power is:

```text
firm_available_kw[s,t] = forecast_available_kw[s,t] × confidence[s,t]
delivered_available_kw[s,t] = firm_available_kw[s,t] × (1 − delivery_loss_fraction[s])
```

Facility demand is:

```text
facility_kw[t] = base_load_kw[t] + Σ(it_power_kw[j] × PUE[t])
```

The dispatcher consumes must-take physical generation first, then eligible
stored energy and dispatchable sources under the operator's renewable,
carbon-free, carbon or cost merit order. Residual grid supply is an explicit dispatchable source, not an
implicit zero-impact fallback. Surplus renewable or carbon-free physical
generation may charge the battery subject to charge power, storage capacity
and round-trip efficiency. Remaining surplus is reported as curtailment.

The exact workload search evaluates this dispatch for every complete legal
placement. A placement with unmet physical demand is infeasible. Cost and
carbon caps are then enforced on the energy attributed to AI work.

## Physical location and signal granularity

An operator may identify a facility with an exact WGS84 latitude/longitude,
site ID, grid-connection or meter ID and IANA time zone. Every energy source
may independently carry its origin coordinates, connection ID and declared
delivery-loss fraction. The response reports great-circle source-to-site
distance, the declared percentage loss and the resulting lost kWh. The loss is
applied before energy can serve base or AI demand.

This physical precision does not manufacture more precise market data. GB
price remains national while carbon is national or grid-regional. CAISO price
may be selected at an exact published pricing node while carbon remains
balancing-area. NYISO price remains zonal and carbon remains balancing-area.
The API reports these scopes separately in `spatial_precision`. It also keeps
the 30-minute decision interval separate from provider-native resolution:
current CAISO and NYISO inputs are hourly observations expanded across two
decision intervals, while the current GB inputs are half-hourly.

Distance is an auditable geometric fact, not an inferred electrical loss. The
operator must supply the loss factor from the actual interconnection,
transmission study, meter reconciliation or contract. When source coordinates
are absent, distance is reported as unavailable rather than guessed.

## Physical delivery versus contractual matching

`onsite`, `dedicated_wire` and `grid` sources can serve demand. A
`contractual` source is recorded as contractual availability but cannot
satisfy the physical energy balance. This prevents a virtual PPA or annual
certificate from being presented as simultaneous physical delivery.

Location-based grid emissions, physical renewable matching, carbon-free
matching and contractual instruments remain separate quantities. The current
pilot does not claim 24/7 CFE compliance, market-based Scope 2 accounting,
avoided emissions, additionality or carbon credits.

## Operator objectives

Useful work and mandatory admission are always the first objective. The
operator then chooses one lexicographic energy policy:

- maximise renewable match percentage;
- maximise carbon-free match percentage;
- minimise operational carbon;
- minimise electricity cost.

Remaining deterministic tie-breaks consider the other energy metrics,
facility energy, curtailment, delay and a stable schedule signature. Match is
ranked as a percentage rather than raw kWh, so an inefficient high-PUE interval
cannot win simply by consuming more matched energy.

## Earliest-run counterfactual

Every portfolio response includes the same admitted and quality-qualified work
scheduled at the earliest capacity-feasible times. This baseline preserves
workflow dependencies, deadlines, time-varying PUE, fixed facility load and
facility capacity, but it does not optimise energy outcomes or apply policy
caps. The response reports:

- first-start and workflow-completion delay;
- facility-energy difference caused by time-varying PUE;
- electricity cost and operational-carbon difference;
- renewable and carbon-free match uplift;
- residual-grid energy avoided.

This is a scenario counterfactual until forecast values are compared with
realised generation, demand, battery state, price and emissions.

## Checkpointable work

An explicitly checkpointable job can be expanded into two to 24 ordered
chunks. Runtime, useful work and utility are divided across those chunks and
each restart depends on the preceding checkpoint. The exact scheduler may
place them in separate generation windows. A non-checkpointable job remains a
single continuous run and unapproved splitting is rejected.

The current chunk model assumes no checkpoint save/restore energy or time
overhead. A production integration must measure that overhead and include it
in the execution profile.

## API input

`POST /api/v1/portfolio` accepts these optional facility fields:

```json
{
  "max_power_kw": 200,
  "site": {
    "site_id": "facility-1",
    "name": "AI facility",
    "latitude": 51.5074,
    "longitude": -0.1278,
    "grid_connection_id": "operator-connection-1",
    "time_zone": "Europe/London"
  },
  "base_load_profile_kw": [60, 60],
  "pue_profile": [1.08, 1.12],
  "energy_priority": "renewable",
  "energy_sources": [{
    "source_id": "wind-farm-a",
    "name": "Wind farm A",
    "kind": "wind",
    "availability_kw": [80, 110],
    "confidence": [0.82, 0.88],
    "cost_per_mwh": 12,
    "carbon_g_per_kwh": 0,
    "renewable": true,
    "carbon_free": true,
    "delivery_type": "dedicated_wire",
    "latitude": 52.1,
    "longitude": -0.4,
    "grid_connection_id": "wind-export-1",
    "delivery_loss_fraction": 0.025,
    "dispatchable": false,
    "provenance": "OPERATOR_FORECAST"
  }],
  "battery": {
    "capacity_kwh": 100,
    "max_charge_kw": 50,
    "max_discharge_kw": 50,
    "initial_energy_kwh": 0,
    "round_trip_efficiency": 0.9
  }
}
```

Every scalar source field may be supplied as one value for the complete
horizon. Availability, confidence, cost and carbon may instead be arrays with
exactly one value per market interval. The service automatically adds residual
grid availability using the selected market's price and carbon series.

## Current limits

- The local interface creates ESTIMATED standard generation shapes. They are
  controls for demonstrating the algorithm, not forecasts from a real plant.
- Dispatch follows a documented deterministic policy. It is not yet a joint
  mixed-integer unit-commitment, battery and compute optimiser.
- Source carbon values are direct operator inputs. Lifecycle emissions and
  marginal avoided emissions are not inferred.
- Source-to-site distance does not automatically imply electrical loss; a
  declared, evidenced loss factor is required.
- The current API schedules one selected market/location per request.
- Reliability reserve, generator ramp rates, minimum up/down time,
  interconnection export and demand-response settlement are not yet modelled.

Production validation requires plant and facility telemetry, forecast/outturn
reconciliation, measured workload profiles, uncertainty calibration and a
shadow operating period before the service controls live work.
