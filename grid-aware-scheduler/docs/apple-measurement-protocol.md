# Apple-device workload measurement protocol

## Purpose

This protocol produces evidence for the workload scheduler. It does not claim
that a MacBook, iPhone or iPad reproduces Private Cloud Compute hardware. The
devices are an accessible Apple-silicon testbed for measuring how model,
precision, compute unit and workload shape change useful work, quality,
runtime and energy behaviour.

## Current testbed

- MacBook Air with Apple M2, 8 GB memory and macOS 26.1.
- iPhone 16 Plus with iOS 26.5, currently paired but offline.
- Physical iPad model and operating-system version still required.

Device serial numbers, UUIDs and host identifiers must never enter benchmark
records or public exports.

## Evidence hierarchy

Measurement provenance must distinguish these levels:

1. **External energy measurement.** A calibrated wall meter or supported
   battery-energy instrument measures joules or watt-hours over the run. This
   is the preferred path for comparing energy between different devices.
2. **Apple subsystem estimate.** `powermetrics` estimates CPU, GPU and Neural
   Engine power on supported Macs. Apple's command help states that these
   values may be inaccurate and must not be used for cross-device comparison.
   They can support same-Mac configuration optimisation when the method and
   uncertainty remain visible.
3. **Energy impact proxy.** Instruments or operating-system energy-impact
   scores can help diagnose an application but are not joules. They must not be
   converted to kWh, cost or carbon.
4. **Catalogue estimate.** A published or modelled power value is an estimate,
   not an observation.

Only levels 1 and 2 can populate `it_energy_wh`. Level 2 profiles must carry a
method label that prevents cross-device ranking. Levels 3 and 4 remain useful
for diagnostics or simulation but cannot be promoted to measured energy.

## Run controls

Every evidence profile requires at least three exact-fingerprint runs. For an
operator-facing case study, use five after one unrecorded warm-up.

Hold constant:

- model identifier and model version;
- weights and KV-cache precision;
- framework and version;
- compute unit, such as CPU, GPU or Neural Engine;
- input/output shape, batch and context;
- quality suite and version;
- power source, display state and background-application policy;
- operating-system version and thermal start state.

Reject or repeat a run when:

- the device reports serious or critical thermal pressure;
- the workload fails or produces incomplete output;
- the quality result is missing;
- the measurement interval does not fully contain the workload;
- another process materially contaminates a system-wide power trace;
- software, model, precision, shape or compute unit differs from the group.

## Reference workload registry

The eight-day evidence set should cover four representative paths:

| Workload | Useful-work unit | Quality evidence | Apple execution path |
|---|---|---|---|
| Language-model inference | output tokens | Versioned task accuracy plus native metric | MLX on Mac GPU |
| Lightweight language fine-tuning | optimiser steps and training examples | Held-out task score | MLX on Mac GPU |
| Vision classification/evaluation | images | Top-1 accuracy | Core ML CPU, GPU and Neural Engine where available |
| Speech transcription | audio seconds | Word error rate plus normalised suite score | Core ML or MLX, held constant across runs |

The registry is extensible. These workloads are a representative first
evidence set, not a claim to cover all AI training and evaluation activity.

## Quality and useful work

Quality is a feasibility gate before energy optimisation. A lower-energy
variant that fails the operator's versioned minimum quality is rejected.

Native metrics remain visible. A suite-defined 0-to-1 score is used only to
route variants evaluated by the same suite and version. The scheduler does not
invent or translate scores between unrelated benchmarks.

Useful-work units remain separate. Tokens, images, audio seconds, examples and
optimiser steps are never added together. Cross-workload admission decisions
use explicit operator utility.

## Environmental outcome

For the same quality-qualified workload, the decision-time forecast is:

```text
forecast_emissions = measured_energy_kWh × forecast_carbon_gCO2_per_kWh
```

The achieved outcome is calculated later from realised grid carbon:

```text
carbon_avoided_kg = realised_run_immediately_kg - realised_scheduled_kg
```

Electricity price stays separate:

```text
cost = measured_energy_kWh × price_per_MWh / 1,000
```

The first public case study should report energy, forecast/realised carbon,
cost, deadline completion, quality, uncertainty and the run-immediately
baseline. Low price must not be described as low carbon.

## Installation boundary

The optional Apple profiling dependencies are pinned in
`requirements-apple.txt`. Model files and evaluation data are deliberately not
installed automatically. The Mac currently has limited free storage, so the
first downloaded model set must remain below 2 GiB unless storage is cleared
deliberately.
