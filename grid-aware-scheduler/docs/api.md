# Local planning API

The local server exposes a small versioned JSON contract for integrations. It
binds to `127.0.0.1`, does not enable cross-origin access and does not execute a
workload. Version 1 is an auditable planning interface over the same canonical
Python estimator and exact planner used by batch code. The current product
version is `0.12.0`; the contract remains `v1`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Process and contract version |
| `GET` | `/api/v1/market?market=GB&location=london` | Current replay points and provenance |
| `POST` | `/api/v1/plan?market=GB&location=london` | Estimate and optimise one workload |
| `POST` | `/api/v1/portfolio?market=GB&location=london` | Schedule a quality-qualified workload queue against facility capacity |
| `GET` | `/api/v1/evidence/profiles` | List governed measured profiles and registry counts |
| `POST` | `/api/v1/evidence/observations` | Ingest one immutable metadata-only observation |
| `POST` | `/api/v1/evidence/probe` | Run a short local MLX performance-only probe; never creates a profile |
| `GET` | `/api/v1/decisions` | Recent persisted decisions |
| `GET` | `/api/v1/decisions/{id}` | Exact request, response, signals and realised score |
| `POST` | `/api/v1/decisions/{id}/score` | Score the fixed decision on realised points |
| `GET` | `/api/v1/inventory` | Latest facility discovery snapshot, store summary and whether discovery is configured |
| `POST` | `/api/v1/inventory/refresh` | Walk the declared Redfish endpoints read-only and record one snapshot |
| `GET` | `/api/v1/telemetry` | One live host occupancy reading |
| `GET` | `/api/v1/telemetry/stream?interval=2` | Server-Sent Events; one reading per interval on a single connection |

Telemetry reports **occupancy, not performance**: available memory, storage
headroom, CPU and accelerator busy percentages, and accelerator memory in use.
These are read from the operating system without privilege — `psutil`,
`ioreg` on Apple silicon, `nvidia-smi` where present — and are `MEASURED`.
They never promote throughput or a power curve, which still require the
repeated calibration runs described in `docs/calibration.md`. An unavailable
source is reported in `warnings` rather than guessed.

The stream is capped at ten minutes and its interval clamped to 0.5–30
seconds; browsers reconnect automatically via `EventSource`. Note that an
open stream keeps the connection active, so automated page capture should use
the one-shot endpoint instead of waiting for network idle.

Discovery endpoints are declared in `data/discovery.json` (schema
`facility-discovery-v1`, see `docs/discovery.md`; override the path with the
`DISCOVERY_CONFIG` environment variable). Refresh returns `409` when no
configuration exists, `201` with the recorded snapshot when the walk ran.
Snapshots are append-only and contain keyed device digests, never raw
serial numbers, UUIDs or credentials.

The operator interface at `/decisions` consumes these endpoints. It can be
opened even when a market-data provider is unavailable because the journal is
served from the local audit store.

CAISO uses `market=CAISO` and a location such as `sp15`, or an exact PNode in
the `location` parameter. NYISO uses `market=NYISO` and one of eleven zone
keys, such as `nyc`, `longisland`, `capital` or `west`.

## Plan request

```json
{
  "workload": {
    "model_key": "<catalogue-model-key>",
    "task": "training",
    "precision": "bf16",
    "tokens": 1000000000,
    "accelerator_count": 8,
    "pue": 1.2,
    "system_efficiency": 0.85,
    "memory_mode": "zero3",
    "training_state_bytes_per_param": 16,
    "activation_buffer_headroom": 0.2,
    "context_length": 8192,
    "batch_size": 8,
    "kv_precision": "bf16",
    "calibration_stack": ""
  },
  "planning": {
    "deadline_hours": 24,
    "cost_weight": 0.5,
    "carbon_weight": 0.5,
    "delay_weight": 0,
    "max_cost": null,
    "max_carbon_kg": null,
    "max_delay_hours": null
  },
  "device_keys": ["h100-sxm", "a100-80-sxm"]
}
```

`device_keys` is optional. Omitting it searches the complete hardware
catalogue. Training-only and inference-only fields may remain present because
the estimator uses only the fields relevant to the selected task.

The three `max_` fields are optional hard constraints. A qualifying signal is
required when its cap is active, and a window outside any cap is removed before
weighted ranking.

`calibration_stack` is optional. When it exactly matches a validated local
profile for device, model, task, precision and accelerator count, the server
uses measured median throughput and IT power. Otherwise it falls safely back
to the catalogue estimate. See [`calibration.md`](calibration.md).

## Plan response

The response includes:

- `algorithm: exact-enumeration-v1` and contract/product versions;
- the complete echoed workload and planning configuration;
- market, location, currency, price/carbon labels and provenance;
- the selected hardware, start, finish, runtime, IT power, PUE, facility
  energy, cost, carbon, delay, score and Pareto status;
- up to 100 ranked alternatives, the total feasible count and rejected
  candidate reasons.
- an untruncated `candidate_snapshot` of the feasible hardware inputs used for
  later realised scoring, even if calibration files change afterwards.

Every successful API plan also returns `decision_id`. The server stores the
exact request, response and decision-time signal snapshot in
`data/cache/audit.sqlite`. This generated local database is excluded from git.
A realised score can be attached later by the backtesting pipeline without
altering the original decision record.

The score endpoint accepts `realised_points`, each with `timestamp`, `price`
and `carbon_intensity_g_per_kwh`. It reconstructs the original candidates from
the stored request and forecast snapshot, keeps the chosen start fixed, then
stores realised performance versus immediate execution and the hindsight
oracle. It cannot revise the original decision.

The API fails closed on an unknown model or device, invalid constraints,
missing required signals, timestamp gaps and mixed currencies. Request bodies
are limited to 64 KiB.

## Measured evidence registry

`POST /api/v1/evidence/observations` accepts one `workload-evidence-v1`
observation. A run ID is immutable: replaying the identical record is
idempotent, while changing a record under an existing ID is rejected. Serious
or critical thermal runs are rejected. Three exact-fingerprint observations
produce a stable profile ID in the ignored local SQLite evidence store.

Every observation explicitly identifies its energy method, scope and
provenance. Supported combinations are calibrated external device-input
metering, Apple CPU/GPU/ANE subsystem estimates and integrated NVIDIA board
power. Apple subsystem estimates cannot be ranked against another device.
Energy Impact, duration and battery percentage are not accepted as watt-hours.

A portfolio variant may provide `evidence_profile_id`. The server then derives
model/version, precision, compute unit, hardware, runtime, IT power, memory,
quality score and evaluation version from the stored profile. Conflicting
client values are rejected. Assignment output includes profile ID, sample
count, energy method/scope and robust throughput/energy variation.

A job may instead set `auto_evidence_profiles: true`. The server ignores
editable variants and creates candidates from every stored profile with the
same workload class, run mode and work unit. It applies the quality floor
before comparing hardware, model, precision, time and energy supply. Eligible
variant counts are returned by job. Scores from different evaluation-suite
versions are never ranked against one another.

## Portfolio request

The AI Operations home at `/` submits one or more jobs. Every execution
variant carries model/version, run mode, precision, compute unit, memory fit,
an explicit quality score and evidence provenance. Estimated quality is
rejected by default unless `require_measured_quality` is false.

```json
{
  "facility": {
    "max_power_kw": 200,
    "site": {
      "site_id": "facility-1",
      "name": "AI facility",
      "latitude": 51.5074,
      "longitude": -0.1278,
      "grid_connection_id": "operator-connection-1",
      "time_zone": "Europe/London"
    },
    "base_load_kw": 60,
    "pue_profile": 1.12,
    "energy_priority": "renewable",
    "energy_sources": [{
      "source_id": "dedicated-wind",
      "name": "Dedicated wind supply",
      "kind": "wind",
      "availability_kw": [80, 100, 120, 90],
      "confidence": 0.9,
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
      "round_trip_efficiency": 0.9
    },
    "max_total_cost": 500,
    "max_total_carbon_kg": 1000
  },
  "jobs": [{
    "job_id": "speech-evaluation-17",
    "workload_class": "speech_transcription",
    "run_mode": "evaluation",
    "earliest_delay_hours": 0,
    "deadline_hours": 12,
    "work_amount": 36000,
    "work_unit": "audio_seconds",
    "utility": 5,
    "minimum_quality": 0.85,
    "mandatory": true,
    "checkpointable": false,
    "checkpoint_count": 1,
    "require_measured_quality": true,
    "variants": [{
      "candidate_key": "speech-evaluation-17:device-a:int8",
      "hardware": "Device A",
      "model_id": "speech-model-a",
      "model_version": "2026.08",
      "precision": "int8",
      "compute_unit": "neural_engine",
      "runtime_hours": 1.4,
      "it_power_kw": 28,
      "pue": 1.18,
      "memory_required_gb": 4.2,
      "memory_available_gb": 16,
      "quality_score": 0.91,
      "quality_provenance": "MEASURED",
      "evaluation_suite": "operator-speech-eval",
      "evaluation_version": "2026.08",
      "hardware_provenance": "MEASURED"
    }]
  }]
}
```

The exact pilot objective first maximises completed operator utility. With an
energy profile, `energy_priority` then selects renewable-first,
carbon-free-first, operational-carbon-first or electricity-cost-first
lexicographic ranking. Mandatory jobs may not be omitted. Unlike useful-work
units are reported separately. Facility power, base demand and time-varying
PUE are enforced in every occupied half-hour, and total cost/carbon caps are
hard constraints. Quality, memory and deadline feasibility are checked before
energy ranking.

The response reports assignment-level source energy, renewable and carbon-free
match, grid and battery energy, interval dispatch, source accounting and an
earliest-feasible run counterfactual. Explicitly checkpointable jobs can be
expanded into two to 24 ordered chunks; non-checkpointable work remains
continuous. See [`energy-dispatch.md`](energy-dispatch.md) for the physical
claim boundary, dispatch order, full input schema and current limitations.

`spatial_precision` reports the exact physical facility separately from the
provider scope of price and carbon. Source accounting includes origin
coordinates, grid-connection identity, great-circle distance, declared loss
fraction and lost kWh. Coordinates do not upgrade a national, regional, zonal
or balancing-area signal to site-level measurement.
The same object distinguishes `decision_interval_minutes` from
`provider_native_resolution_minutes`; US hourly observations are not
misrepresented as independent half-hour measurements.

The endpoint currently uses one selected market/location context per request.
The core portfolio engine supports multiple facility candidates, but a future
multi-region API must also define currency conversion and data residency rules
before it can rank cross-market placements.

## Boundary before remote deployment

This is a local integration surface, not a remotely exposed service. A remote
deployment would require authentication, authorisation, tenant isolation,
rate limits, a production audit store, encrypted secrets, execution policy and network
hardening. Binding this development server to a public interface is not a
substitute for those controls.
