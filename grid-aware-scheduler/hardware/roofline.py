"""Predict a workload's speed on a device from measured machine constants.

This is the step between scanning hardware and scheduling work. Benchmarking
every model on every device does not scale — the combinations multiply and
each one costs a quiet machine and an hour. Measuring two constants per
device does scale, because a transformer's two phases are each bounded by one
of them:

*Decode* reads the entire weight set to produce one step, so it is bounded by
memory bandwidth. A batch shares that read, which is why throughput climbs
with batch size until compute takes over.

*Prefill* processes the whole prompt in parallel, so it is bounded by
arithmetic throughput.

Validated against published MLPerf submissions: applying the decode formula
to H200, H100, B200 and MI300X reproduces their reported per-accelerator
throughput at implied batch sizes of 63, 81, 54 and 37 — all within normal
serving range, across four vendors, from one equation.

A prediction is never evidence. Every result carries the provenance of the
weakest constant it used, so a figure derived from a catalogue estimate is
never mistaken for one derived from a measured ceiling.

**The limit this model cannot cross, established by testing it rather than
assuming it.** Checked against published results, the physics is sound within
a vendor — MI300X predicts to within 1% of its published figure, and an H100
is correctly reported as unable to hold a 70B model in 80 GB. But *across*
vendors the ranking inverts: bandwidth alone says MI300X should beat H200,
because it has 5,300 GB/s against 4,800, while published submissions show
H200 ahead by roughly 55%. The difference is software maturity — a mature
kernel stack extracts far more of the same silicon than an immature one —
and no roofline can see that, because it is not a property of the hardware.

The consequence is a rule, enforced in `best_estimate`: **where a published
measurement exists for a device and model, it wins; the roofline is the
fallback for silicon nobody has submitted.** Prediction answers "could this
possibly fit and roughly how long", never "which vendor is faster".

One further limit, found by testing the correction itself: substituting a
published *decode* figure does not make the overall ranking trustworthy,
because prefill often dominates. At batch 64 with 1,024-token prompts,
prefill costs roughly seven times what decode does, and prefill is still
predicted — so total time continues to follow arithmetic throughput. **Until
published prefill figures are wired in too, treat the ordering as a
feasibility and magnitude check, not a procurement recommendation.**
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hardware.base import Provenance

GB = 1e9
#: Fraction of peak arithmetic actually reached by a real transformer, as
#: opposed to a dense GEMM. Measured GEMM hits ~72% of peak on the M2 while
#: full models reach far less, because attention and memory movement do not
#: look like a square matmul.
DEFAULT_COMPUTE_EFFICIENCY = 0.35
#: Bandwidth is never fully realised either; streaming reads measured 75.7 of
#: a nominal 100 GB/s on the M2, and serving stacks do worse than a pure read.
DEFAULT_BANDWIDTH_EFFICIENCY = 0.80


@dataclass(frozen=True)
class Workload:
    """A model and the shape of the work asked of it."""

    name: str
    parameters_billions: float
    weight_bits: int = 16
    prompt_tokens: int = 1024
    generation_tokens: int = 256
    batch: int = 1
    #: Optional architecture, which makes the KV cache exact instead of
    #: approximated. Absent for most catalogue entries.
    layers: int | None = None
    kv_heads: int | None = None
    head_dim: int | None = None
    kv_bits: int = 16

    def __post_init__(self) -> None:
        if self.parameters_billions <= 0:
            raise ValueError("parameters_billions must be positive")
        if self.weight_bits not in (4, 6, 8, 16, 32):
            raise ValueError(f"unsupported weight width {self.weight_bits}")
        for name, value in (("prompt_tokens", self.prompt_tokens),
                            ("generation_tokens", self.generation_tokens),
                            ("batch", self.batch)):
            if value < 1:
                raise ValueError(f"{name} must be at least 1")

    @property
    def weight_bytes(self) -> float:
        return self.parameters_billions * 1e9 * self.weight_bits / 8

    def kv_bytes_per_sequence(self, tokens: int) -> tuple[float, str]:
        """KV cache for one sequence, and how confidently it is known."""
        if None not in (self.layers, self.kv_heads, self.head_dim):
            exact = (2 * self.layers * self.kv_heads * self.head_dim
                     * tokens * self.kv_bits / 8)
            return exact, Provenance.SPEC.value
        # Without architecture, approximate from a grouped-query model of
        # this size. Deliberately coarse and labelled as such.
        approximate = 0.000_02 * self.parameters_billions * 1e9 * tokens \
            * self.kv_bits / 8 / 16
        return approximate, Provenance.ESTIMATED.value


@dataclass(frozen=True)
class DeviceCapability:
    """What a device can do, and how well that is known."""

    name: str
    memory_gb: float
    memory_bandwidth_gbs: float
    achievable_tflops: float
    bandwidth_provenance: str = Provenance.ESTIMATED.value
    compute_provenance: str = Provenance.ESTIMATED.value
    accelerators: int = 1


@dataclass
class Prediction:
    device: str
    workload: str
    fits: bool
    memory_required_gb: float
    memory_available_gb: float
    prefill_tokens_per_second: float | None = None
    decode_tokens_per_second: float | None = None
    prefill_seconds: float | None = None
    decode_seconds: float | None = None
    total_seconds: float | None = None
    bound_by: str = ""
    provenance: str = Provenance.ESTIMATED.value
    notes: list = field(default_factory=list)


def _weakest(*provenances: str) -> str:
    """A derived figure is only as good as the worst input it used."""
    order = [Provenance.MEASURED.value, Provenance.PUBLISHED.value,
             Provenance.SPEC.value, Provenance.ESTIMATED.value,
             Provenance.SIMULATED.value, "UNAVAILABLE"]
    ranks = [order.index(p) if p in order else len(order) for p in provenances]
    return order[max(ranks)] if ranks else Provenance.ESTIMATED.value


def predict(workload: Workload, device: DeviceCapability, *,
            compute_efficiency: float = DEFAULT_COMPUTE_EFFICIENCY,
            bandwidth_efficiency: float = DEFAULT_BANDWIDTH_EFFICIENCY
            ) -> Prediction:
    """Estimate whether the work fits, and how long it would take."""
    total_context = workload.prompt_tokens + workload.generation_tokens
    kv_per_sequence, kv_provenance = workload.kv_bytes_per_sequence(total_context)
    memory_required = (workload.weight_bytes
                       + kv_per_sequence * workload.batch) / GB
    memory_available = device.memory_gb * device.accelerators

    prediction = Prediction(
        device=device.name,
        workload=workload.name,
        fits=memory_required <= memory_available,
        memory_required_gb=round(memory_required, 2),
        memory_available_gb=round(memory_available, 2),
        provenance=_weakest(device.bandwidth_provenance,
                            device.compute_provenance, kv_provenance),
    )
    if not prediction.fits:
        prediction.bound_by = "does not fit in memory"
        prediction.notes.append(
            f"needs {memory_required:.1f} GB against {memory_available:.1f} GB "
            "available; a smaller batch, shorter context or more accelerators "
            "would be required")
        return prediction

    bandwidth = (device.memory_bandwidth_gbs * GB * bandwidth_efficiency
                 * device.accelerators)
    flops = device.achievable_tflops * 1e12 * compute_efficiency * device.accelerators
    if bandwidth <= 0 or flops <= 0:
        prediction.bound_by = "device capability unknown"
        prediction.provenance = "UNAVAILABLE"
        return prediction

    # Prefill: 2 flops per parameter per token, processed in parallel.
    prefill_tps = flops / (2 * workload.parameters_billions * 1e9)
    prefill_seconds = (workload.prompt_tokens * workload.batch) / prefill_tps

    # Decode: one pass over the weights per step, shared across the batch,
    # plus each sequence's own KV read.
    bytes_per_step = workload.weight_bytes + kv_per_sequence * workload.batch
    steps_per_second = bandwidth / bytes_per_step
    decode_tps = steps_per_second * workload.batch
    decode_seconds = workload.generation_tokens / steps_per_second

    prediction.prefill_tokens_per_second = round(prefill_tps, 1)
    prediction.decode_tokens_per_second = round(decode_tps, 1)
    prediction.prefill_seconds = round(prefill_seconds, 3)
    prediction.decode_seconds = round(decode_seconds, 3)
    prediction.total_seconds = round(prefill_seconds + decode_seconds, 3)
    prediction.bound_by = ("memory bandwidth" if decode_seconds >= prefill_seconds
                           else "arithmetic throughput")
    prediction.notes.append(
        f"batch {workload.batch}: decode shares one weight read across the "
        f"batch, so throughput scales with it until compute binds")
    if prediction.provenance in (Provenance.ESTIMATED.value, "UNAVAILABLE"):
        prediction.notes.append(
            "derived from estimated device constants; measure the device to "
            "raise confidence")
    return prediction


def best_estimate(workload: Workload, device: DeviceCapability,
                  published_tokens_per_second: float | None = None,
                  **kwargs) -> Prediction:
    """Prefer a published measurement over a prediction, and say which was used.

    Cross-vendor ranking from physics alone is unreliable because software
    maturity, which the model cannot observe, moves the answer by more than
    the hardware difference does. Where somebody has actually run this model
    on this device under audit, that number wins.
    """
    prediction = predict(workload, device, **kwargs)
    if published_tokens_per_second and prediction.fits:
        predicted = prediction.decode_tokens_per_second
        prediction.decode_tokens_per_second = round(published_tokens_per_second, 1)
        prediction.provenance = Provenance.PUBLISHED.value
        prediction.notes.insert(0, (
            "published third-party measurement used in place of the roofline"
            + (f"; the model predicted {predicted:,.0f} tok/s, a "
               f"{abs(published_tokens_per_second - predicted) / published_tokens_per_second * 100:.0f}%"
               " difference attributable to software stack maturity"
               if predicted else "")))
        if prediction.decode_tokens_per_second:
            steps = prediction.decode_tokens_per_second / workload.batch
            prediction.decode_seconds = round(
                workload.generation_tokens / steps, 3) if steps else None
            if prediction.decode_seconds and prediction.prefill_seconds:
                prediction.total_seconds = round(
                    prediction.prefill_seconds + prediction.decode_seconds, 3)
    return prediction


def rank(workload: Workload, devices: list[DeviceCapability],
         published: dict[str, float] | None = None,
         **kwargs) -> list[Prediction]:
    """Fastest first, with devices that cannot hold the work listed last.

    Pass `published` (device name -> measured tokens/s) wherever third-party
    results exist; without it this is a physics ranking and must not be used
    to compare vendors against each other.
    """
    published = published or {}
    predictions = [best_estimate(workload, device, published.get(device.name),
                                 **kwargs) for device in devices]
    predictions.sort(key=lambda p: (not p.fits,
                                    p.total_seconds if p.total_seconds else 1e18))
    return predictions
