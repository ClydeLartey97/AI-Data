"""Canonical Python workload-to-hardware estimator.

The interactive pages mirror these equations in JavaScript for immediate
scenario changes. Batch jobs, future APIs and the quality router use this
module as the server-side contract. Every result remains ESTIMATED until a
measured hardware profile replaces the catalogue figures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from adapters.base_adapter import GridDataPoint
from core import models
from core.planner import (PlanResult, PlanningCandidate, PlanningRequest,
                          optimise)
from hardware import catalogue
from hardware.base import Device, INTERCONNECT_GBS, Interconnect, Provenance
from hardware.calibration import CalibrationProfile

Task = Literal["training", "inference"]
MemoryMode = Literal["zero3", "replicated"]


@dataclass(frozen=True)
class WorkloadSpec:
    model_key: str
    task: Task
    precision: str
    tokens: float
    accelerator_count: int
    pue: float = 1.2
    system_efficiency: float = 0.85
    memory_mode: MemoryMode = "zero3"
    training_state_bytes_per_param: float = 16.0
    activation_buffer_headroom: float = 0.20
    context_length: int = 8192
    batch_size: int = 8
    kv_precision: str = "bf16"
    calibration_stack: str = ""

    def __post_init__(self) -> None:
        if self.model_key not in models.CATALOGUE:
            raise ValueError(f"unknown model {self.model_key!r}")
        if self.task not in ("training", "inference"):
            raise ValueError("task must be training or inference")
        if self.precision not in models.BYTES_PER_PARAM:
            raise ValueError("unsupported weight precision")
        if self.kv_precision not in models.BYTES_PER_PARAM:
            raise ValueError("unsupported KV-cache precision")
        if (isinstance(self.tokens, bool) or not isinstance(self.tokens, (int, float))
                or self.tokens <= 0 or self.tokens > 1e18
                or not math.isfinite(self.tokens)):
            raise ValueError("tokens must be finite and in (0, 1e18]")
        if (isinstance(self.accelerator_count, bool)
                or not isinstance(self.accelerator_count, int)
                or not 0 < self.accelerator_count <= 100_000):
            raise ValueError("accelerator_count must be an integer in [1, 100000]")
        if (isinstance(self.pue, bool) or not isinstance(self.pue, (int, float))
                or self.pue < 1 or self.pue > 5 or not math.isfinite(self.pue)):
            raise ValueError("PUE must be finite and in [1.0, 5.0]")
        if (isinstance(self.system_efficiency, bool)
                or not isinstance(self.system_efficiency, (int, float))
                or not math.isfinite(self.system_efficiency)
                or not 0 < self.system_efficiency <= 1):
            raise ValueError("system_efficiency must be in (0, 1]")
        if self.memory_mode not in ("zero3", "replicated"):
            raise ValueError("memory_mode must be zero3 or replicated")
        if (isinstance(self.training_state_bytes_per_param, bool)
                or not isinstance(self.training_state_bytes_per_param, (int, float))
                or not math.isfinite(self.training_state_bytes_per_param)
                or not 0 < self.training_state_bytes_per_param <= 128):
            raise ValueError("training state bytes must be finite and in (0, 128]")
        if (isinstance(self.activation_buffer_headroom, bool)
                or not isinstance(self.activation_buffer_headroom, (int, float))
                or not math.isfinite(self.activation_buffer_headroom)
                or not 0 <= self.activation_buffer_headroom <= 10):
            raise ValueError("activation headroom must be finite and in [0, 10]")
        for name, value, upper in (
            ("context length", self.context_length, 10_000_000),
            ("batch size", self.batch_size, 1_000_000),
        ):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not 0 < value <= upper):
                raise ValueError(f"{name} must be an integer in [1, {upper}]")
        if len(self.calibration_stack) > 200:
            raise ValueError("calibration_stack cannot exceed 200 characters")

    @property
    def model(self) -> models.Model:
        return models.CATALOGUE[self.model_key]


@dataclass(frozen=True)
class DeviceEstimate:
    device_key: str
    hardware_name: str
    runtime_hours: float
    it_power_kw: float
    memory_available_gb: float
    memory_required_gb: float
    memory_ok: bool
    scaling_efficiency: float
    provenance: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class GridLocation:
    market: str
    location: str
    series: list[GridDataPoint]
    currency: str
    provenance: str


def memory_required_gb(spec: WorkloadSpec) -> float:
    model = spec.model
    if spec.task == "inference":
        architecture = models.architecture_for(spec.model_key, model.params_b)
        return (
            model.weight_gb(spec.precision)
            + models.kv_cache_gb(
                architecture, spec.context_length, spec.batch_size,
                spec.kv_precision,
            )
        )
    return (
        model.params_b
        * spec.training_state_bytes_per_param
        * (1 + spec.activation_buffer_headroom)
    )


def _scaling(spec: WorkloadSpec, device: Device) -> float:
    n = spec.accelerator_count
    if n <= 1 or device.interconnect is Interconnect.UNIFIED:
        return spec.system_efficiency
    link_gbs = INTERCONNECT_GBS.get(device.interconnect, 0.0)
    if link_gbs <= 0:
        return spec.system_efficiency
    model = spec.model
    representative_step_tokens = 2e6
    compute_seconds = (
        6 * model.compute_params_b * 1e9 * representative_step_tokens
        / (device.peak_tflops_bf16 * device.mfu * n * 1e12)
    )
    all_reduce_bytes = (
        2 * (n - 1) / n * model.weight_bytes(spec.precision)
    )
    communication_seconds = all_reduce_bytes / (link_gbs * 1e9)
    topology_efficiency = compute_seconds / (
        compute_seconds + communication_seconds
    )
    return topology_efficiency * spec.system_efficiency


def estimate_device(spec: WorkloadSpec, device: Device,
                    calibration: CalibrationProfile | None = None) -> DeviceEstimate:
    n, model = spec.accelerator_count, spec.model
    efficiency = _scaling(spec, device)
    calibrated = calibration is not None and calibration.matches(
        device_key=device.key,
        model_key=spec.model_key,
        task=spec.task,
        precision=spec.precision,
        accelerator_count=n,
        stack_fingerprint=spec.calibration_stack,
    )
    if calibrated:
        runtime = spec.tokens / calibration.tokens_per_second / 3600
        it_power = calibration.average_it_power_watts / 1000
        efficiency = 1.0
    elif spec.task == "training":
        runtime = (
            6 * model.compute_params_b * 1e9 * spec.tokens
            / (device.peak_tflops_bf16 * device.mfu * n * efficiency * 1e12)
            / 3600
        )
        it_power = device.tdp_watts * n / 1000
    else:
        aggregate_bytes_per_second = device.memory_bandwidth_gbs * 1e9 * n
        tokens_per_second = aggregate_bytes_per_second / model.weight_bytes(spec.precision)
        runtime = spec.tokens / tokens_per_second / spec.system_efficiency / 3600
        decode_watts = device.idle_watts + (device.tdp_watts - device.idle_watts) * 0.6
        it_power = decode_watts * n / 1000

    required = memory_required_gb(spec)
    available = device.memory_gb * n
    if spec.task == "training" and spec.memory_mode == "replicated":
        memory_ok = device.memory_gb >= required
        displayed_required = required * n
    else:
        memory_ok = available >= required
        displayed_required = required

    if calibrated:
        assumptions = (
            f"Measured median across {calibration.sample_count} exact-fingerprint runs",
            f"Calibration stack {calibration.stack_fingerprint}",
            f"Throughput robust variation ±{calibration.throughput_relative_mad:.1%}",
            f"Power robust variation ±{calibration.power_relative_mad:.1%}",
            f"Facility PUE {spec.pue:.2f}",
        )
    else:
        assumptions = (
            f"System efficiency {spec.system_efficiency:.0%}",
            f"Facility PUE {spec.pue:.2f}",
            (f"Training state {spec.training_state_bytes_per_param:g} bytes/parameter "
             f"plus {spec.activation_buffer_headroom:.0%} reserve"
             if spec.task == "training" else
             f"KV cache {spec.kv_precision}, context {spec.context_length}, batch {spec.batch_size}"),
        )
    return DeviceEstimate(
        device_key=device.key,
        hardware_name=f"{n}x {device.name}",
        runtime_hours=runtime,
        it_power_kw=it_power,
        memory_available_gb=available,
        memory_required_gb=displayed_required,
        memory_ok=memory_ok,
        scaling_efficiency=efficiency,
        provenance=(Provenance.MEASURED.value if calibrated
                    else Provenance.ESTIMATED.value),
        assumptions=assumptions,
    )


def planning_candidates(spec: WorkloadSpec, locations: list[GridLocation],
                        device_keys: list[str] | None = None,
                        calibrations: list[CalibrationProfile] | None = None,
                        ) -> list[PlanningCandidate]:
    keys = device_keys or list(catalogue.CATALOGUE)
    candidates: list[PlanningCandidate] = []
    for key in keys:
        if key not in catalogue.CATALOGUE:
            raise ValueError(f"unknown hardware {key!r}")
        matching = sorted(
            (profile for profile in (calibrations or []) if profile.matches(
                device_key=key,
                model_key=spec.model_key,
                task=spec.task,
                precision=spec.precision,
                accelerator_count=spec.accelerator_count,
                stack_fingerprint=spec.calibration_stack,
            )),
            key=lambda profile: profile.calibrated_at,
            reverse=True,
        )
        estimate = estimate_device(
            spec, catalogue.CATALOGUE[key], matching[0] if matching else None
        )
        for location in locations:
            candidates.append(PlanningCandidate(
                key=f"{key}:{location.market}:{location.location}",
                hardware=estimate.hardware_name,
                market=location.market,
                location=location.location,
                series=location.series,
                runtime_hours=estimate.runtime_hours,
                it_power_kw=estimate.it_power_kw,
                pue=spec.pue,
                memory_ok=estimate.memory_ok,
                currency=location.currency,
                hardware_provenance=estimate.provenance,
                grid_provenance=location.provenance,
                notes=estimate.assumptions,
            ))
    return candidates


def plan_workload(spec: WorkloadSpec, locations: list[GridLocation],
                  request: PlanningRequest,
                  device_keys: list[str] | None = None,
                  calibrations: list[CalibrationProfile] | None = None) -> PlanResult:
    return optimise(
        planning_candidates(spec, locations, device_keys, calibrations), request
    )
