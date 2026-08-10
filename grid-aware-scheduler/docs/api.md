# Local planning API

The local server exposes a small versioned JSON contract for integrations. It
binds to `127.0.0.1`, does not enable cross-origin access and does not execute a
workload. Version 1 is an auditable planning interface over the same canonical
Python estimator and exact planner used by batch code. The current product
version is `0.8.0`; the contract remains `v1`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Process and contract version |
| `GET` | `/api/v1/market?market=GB&location=london` | Current replay points and provenance |
| `POST` | `/api/v1/plan?market=GB&location=london` | Estimate and optimise one workload |
| `POST` | `/api/v1/portfolio?market=GB&location=london` | Schedule a quality-qualified workload queue against facility capacity |
| `GET` | `/api/v1/decisions` | Recent persisted decisions |
| `GET` | `/api/v1/decisions/{id}` | Exact request, response, signals and realised score |
| `POST` | `/api/v1/decisions/{id}/score` | Score the fixed decision on realised points |

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

## Portfolio request

The AI Operations home at `/` submits one or more jobs. Every execution
variant carries model/version, run mode, precision, compute unit, memory fit,
an explicit quality score and evidence provenance. Estimated quality is
rejected by default unless `require_measured_quality` is false.

```json
{
  "facility": {
    "max_power_kw": 200,
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

The exact pilot objective first maximises completed operator utility, then
minimises operational carbon, electricity cost and delay. Mandatory jobs may
not be omitted. Unlike useful-work units are reported separately. Facility
power is enforced in every occupied half-hour, and total cost/carbon caps are
hard constraints. Quality, memory and deadline feasibility are checked before
grid ranking. The response reports assignments, model execution metadata,
energy/carbon/cost per useful-work unit, unscheduled jobs, work completed by
unit, aggregate energy/cost/carbon, search bound and exactness flag.

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
