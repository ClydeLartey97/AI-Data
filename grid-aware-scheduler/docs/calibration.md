# Hardware calibration

Hardware detection proves identity and installed memory. It does not prove
workload throughput or sustained power. A calibration profile can replace the
catalogue runtime and power estimate only when all of these fields match:

- device catalogue key;
- model catalogue key;
- training or inference task;
- precision;
- accelerator count;
- a caller-defined software-stack fingerprint.

At least three repeated observations are required. The profile uses median
tokens per second and median IT power. It carries normalised median absolute
deviation for both fields so run-to-run variation remains visible in the
planner's assumptions. A mismatched or absent fingerprint falls back to
`ESTIMATED`; device detection alone never promotes performance evidence.

## Observation input

Create a local JSON file in this shape. `average_it_power_watts` is the total
IT power for the declared accelerator count, not per-device TDP and not
facility power after PUE.

```json
{
  "observations": [
    {
      "device_key": "h100-sxm",
      "model_key": "llama31-8b",
      "task": "training",
      "precision": "bf16",
      "accelerator_count": 8,
      "stack_fingerprint": "torch-2.8_cuda-13_kernel-a1",
      "tokens": 100000000,
      "duration_seconds": 1320.5,
      "average_it_power_watts": 4210.0,
      "observed_at": "2026-08-10T12:00:00+00:00"
    }
  ]
}
```

Each exact fingerprint needs at least three rows. Aggregate and validate it:

```bash
~/venvs/national-grid/bin/python -m hardware.calibration observations.json
```

The default output is `data/calibration/profiles.json`, which is local and
excluded from git. Set `workload.calibration_stack` to the same fingerprint in
the planning API. The server then loads the newest exact profile and marks
only the calibrated runtime and power path as `MEASURED`.

The observation collector itself remains an execution-environment concern.
Production collection must use a monotonic runtime, trusted device or
wall-power telemetry, a warm-up policy and a record of failed or throttled
runs. Manually entering a vendor TDP is not calibration.
