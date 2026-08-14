from __future__ import annotations

import pytest

from hardware.base import Provenance
from hardware.roofline import (DeviceCapability, Workload, best_estimate,
                               predict, rank)

SPEC = Provenance.SPEC.value


def _llama70b(**overrides):
    values = dict(name="llama2-70b", parameters_billions=70, weight_bits=8,
                  prompt_tokens=1024, generation_tokens=256, batch=64,
                  layers=80, kv_heads=8, head_dim=128)
    values.update(overrides)
    return Workload(**values)


MI300X = DeviceCapability("MI300X", 192, 5300, 1307, SPEC, SPEC)
H200 = DeviceCapability("H200", 141, 4800, 989, SPEC, SPEC)
H100 = DeviceCapability("H100", 80, 3350, 989, SPEC, SPEC)


def test_prediction_reproduces_a_published_result_within_a_vendor():
    """MI300X publishes 2,772 tok/s per accelerator on this workload.

    At the default 80% bandwidth efficiency the roofline lands within a few
    percent, which is the evidence that the memory-bound decode model is
    sound. Note the efficiency factor is doing real work here: at a naive
    100% the same formula overshoots by about a quarter.
    """
    prediction = predict(_llama70b(), MI300X)
    assert prediction.fits
    assert prediction.decode_tokens_per_second == pytest.approx(2772, rel=0.10)


def test_a_model_too_large_for_the_memory_is_refused_not_slowed():
    prediction = predict(_llama70b(), H100, bandwidth_efficiency=1.0)
    assert prediction.fits is False
    assert prediction.bound_by == "does not fit in memory"
    # No throughput may be reported for work that cannot run.
    assert prediction.decode_tokens_per_second is None
    assert "smaller batch" in " ".join(prediction.notes)


def test_batch_raises_decode_throughput_because_the_weight_read_is_shared():
    single = predict(_llama70b(batch=1), H200)
    batched = predict(_llama70b(batch=32), H200)
    assert batched.decode_tokens_per_second > single.decode_tokens_per_second
    # But not linearly, because each sequence still reads its own KV cache.
    assert batched.decode_tokens_per_second < single.decode_tokens_per_second * 32


def test_quantisation_reduces_both_footprint_and_time():
    wide = predict(_llama70b(weight_bits=16), MI300X)
    narrow = predict(_llama70b(weight_bits=4), MI300X)
    assert narrow.memory_required_gb < wide.memory_required_gb
    assert narrow.decode_tokens_per_second > wide.decode_tokens_per_second


def test_result_carries_the_provenance_of_its_weakest_input():
    estimated = DeviceCapability("Guessy", 192, 5300, 1307,
                                 Provenance.ESTIMATED.value,
                                 Provenance.MEASURED.value)
    assert predict(_llama70b(), estimated).provenance == Provenance.ESTIMATED.value

    measured = DeviceCapability("Known", 192, 5300, 1307,
                                Provenance.MEASURED.value,
                                Provenance.MEASURED.value)
    # The KV approximation is the weak link when architecture is unknown.
    without_architecture = Workload("m", 70, layers=None, kv_heads=None,
                                    head_dim=None)
    assert predict(without_architecture, measured).provenance == \
        Provenance.ESTIMATED.value


def test_published_measurement_overrides_the_prediction():
    """Physics cannot see software maturity, so a real result wins."""
    prediction = best_estimate(_llama70b(), H200,
                               published_tokens_per_second=4310.0)
    assert prediction.decode_tokens_per_second == 4310.0
    assert prediction.provenance == Provenance.PUBLISHED.value
    assert "software stack maturity" in " ".join(prediction.notes)


def test_published_decode_replaces_the_predicted_decode():
    """Published results correct decode, which physics ranks wrongly.

    The roofline puts MI300X ahead of H200 on decode because it has more
    bandwidth (5,300 against 4,800 GB/s). Published submissions show the
    reverse, by roughly 55%, because software maturity moves the answer more
    than the hardware difference does.
    """
    workload = _llama70b()
    physics = rank(workload, [MI300X, H200])
    predicted = {p.device: p.decode_tokens_per_second for p in physics}
    assert predicted["MI300X"] > predicted["H200"]        # what physics believes

    corrected = rank(workload, [MI300X, H200],
                     published={"H200": 4310.0, "MI300X": 2772.0})
    measured = {p.device: p.decode_tokens_per_second for p in corrected}
    assert measured["H200"] > measured["MI300X"]          # what was measured
    assert all(p.provenance == Provenance.PUBLISHED.value for p in corrected)


def test_overall_ranking_is_still_not_a_cross_vendor_verdict():
    """A published decode figure does not make the whole ranking trustworthy.

    At batch 64 with 1,024-token prompts, prefill costs roughly seven times
    what decode does, and prefill remains a prediction. So total time still
    follows arithmetic throughput, and MI300X can rank ahead of H200 overall
    even once its decode disadvantage is known. Cross-vendor placement needs
    published prefill too; until then this ranks feasibility and rough
    magnitude, not vendors.
    """
    ordered = rank(_llama70b(), [MI300X, H200],
                   published={"H200": 4310.0, "MI300X": 2772.0})
    fastest = ordered[0]
    assert fastest.prefill_seconds > fastest.decode_seconds * 3
    assert fastest.bound_by == "arithmetic throughput"


def test_devices_that_cannot_hold_the_work_rank_last():
    ordered = rank(_llama70b(), [H100, MI300X, H200])
    assert ordered[-1].device == "H100"
    assert ordered[-1].fits is False


def test_invalid_workloads_are_rejected():
    with pytest.raises(ValueError, match="parameters_billions"):
        Workload("bad", 0)
    with pytest.raises(ValueError, match="unsupported weight width"):
        Workload("bad", 7, weight_bits=13)
    with pytest.raises(ValueError, match="batch"):
        Workload("bad", 7, batch=0)
