"""The scan's matching and the bridge from a scanned device to a prediction.

Everything here is offline. The scan itself reads six live-ish sources, but
the two things worth locking down are pure functions over their output: how a
device name is matched to third-party data, and how a scanned record becomes
predictor input without quietly upgrading the provenance of what it rests on.
"""
from __future__ import annotations

from hardware import scan
from hardware.base import Provenance
from hardware.mlperf import DeviceProfile
from hardware.roofline import Workload

MEASURED = Provenance.MEASURED.value
PUBLISHED = Provenance.PUBLISHED.value
SPEC = Provenance.SPEC.value


def _profile(accelerator="NVIDIA H200-SXM-141GB", model="llama2-70b-99.9",
             units="Tokens/s", rate=4310.0, **overrides):
    values = dict(accelerator=accelerator, model=model, scenario="Offline",
                  units=units, per_accelerator_median=rate,
                  per_accelerator_best=rate * 1.1, submissions=24)
    values.update(overrides)
    return DeviceProfile(**values)


def _device(name="NVIDIA H200 SXM", **overrides):
    values = dict(name=name, kind="GPU", source="test",
                  identity={"memory_total_gb": 141})
    values.update(overrides)
    return scan.ScannedDevice(**values)


# --- matching --------------------------------------------------------------

def test_the_same_silicon_written_two_ways_still_matches():
    """A submission says "NVIDIA H200-SXM-141GB"; a BMC says "H200 SXM"."""
    found = scan.match_published("H200 SXM", [_profile()])
    assert len(found) == 1


def test_a_vendor_name_alone_cannot_create_a_match():
    """Without a model number, "NVIDIA" would match every NVIDIA profile.

    This is the guard that keeps the scan from attaching a B200's throughput
    to an unidentified NVIDIA card, which would look like knowledge and be
    fabrication.
    """
    assert scan.match_published("NVIDIA Accelerator", [_profile()]) == []


def test_a_device_nobody_submits_matches_nothing():
    """Apple does not submit to MLPerf, and an empty list is the right answer."""
    assert scan.match_published("Apple M2", [_profile()]) == []


# --- capability ------------------------------------------------------------

def test_a_measured_ceiling_beats_the_catalogue_and_says_so():
    device = _device(
        name="Apple M2", identity={"memory_total_gb": 8},
        measured={"gemm_fp16_gflops": {"median": 2583.2},
                  "memory_bandwidth_gbs": {"median": 75.7}},
        catalogue={"peak_tflops_bf16": 3.6, "memory_bandwidth_gbs": 100.0,
                   "provenance": Provenance.ESTIMATED.value})
    found = scan.capability(device)
    assert found.memory_bandwidth_gbs == 75.7
    assert found.achievable_tflops == 2583.2 / 1000
    assert found.bandwidth_provenance == MEASURED
    assert found.compute_provenance == MEASURED


def test_the_catalogue_fills_in_only_what_was_never_measured():
    """A partial measurement must not drag the unmeasured half up with it."""
    device = _device(
        measured={"memory_bandwidth_gbs": {"median": 4100.0}},
        catalogue={"peak_tflops_bf16": 989.0, "memory_bandwidth_gbs": 4800.0,
                   "provenance": SPEC})
    found = scan.capability(device)
    assert found.bandwidth_provenance == MEASURED
    assert found.compute_provenance == SPEC
    assert found.achievable_tflops == 989.0


def test_a_device_with_no_memory_figure_is_not_given_one():
    assert scan.capability(_device(identity={})) is None


def test_a_device_with_neither_measurement_nor_catalogue_entry_is_refused():
    """The alternative — a default constant — is an invented device."""
    assert scan.capability(_device(measured=None, catalogue=None)) is None


# --- published rates -------------------------------------------------------

def test_a_published_rate_scales_with_the_number_of_accelerators():
    device = _device(count=8, published=[{"model": "llama2-70b-99.9",
                                          "per_accelerator": 4310.0,
                                          "units": "Tokens/s"}])
    assert scan.published_rates(device, "llama2-70b") == 4310.0 * 8


def test_a_rate_in_other_units_is_not_read_as_tokens():
    """Most powered MLPerf rows are ResNet in samples/s. Different quantity."""
    device = _device(published=[{"model": "resnet50", "units": "Samples/s",
                                 "per_accelerator": 90000.0}])
    assert scan.published_rates(device, "") is None


def test_a_rate_for_a_different_model_is_not_borrowed():
    device = _device(published=[{"model": "stable-diffusion-xl",
                                 "units": "Tokens/s", "per_accelerator": 10.0}])
    assert scan.published_rates(device, "llama2-70b") is None


# --- plan ------------------------------------------------------------------

def _report(devices):
    return scan.ScanReport(scanned_at="2026-08-16T00:00:00+00:00",
                           devices=list(devices))


def test_a_device_of_unknown_capability_is_listed_but_never_ranked():
    """An unranked device is honest; a guessed one is worse than silence."""
    known = _device(name="NVIDIA H200 SXM",
                    catalogue={"peak_tflops_bf16": 989.0,
                               "memory_bandwidth_gbs": 4800.0,
                               "provenance": SPEC})
    unknown = _device(name="Mystery FPGA", identity={}, catalogue=None)
    result = scan.plan(_report([known, unknown]),
                       Workload("llama2-70b", 70, weight_bits=8, batch=32))
    assert [row["device"] for row in result["ranked"]] == ["NVIDIA H200 SXM"]
    assert [row["device"] for row in result["unranked"]] == ["Mystery FPGA"]


def test_the_ranking_carries_its_own_limit_with_it():
    """Prefill is still predicted, so the order is not a procurement verdict.

    Stated in the payload rather than in a comment, because the payload is
    what reaches an operator.
    """
    result = scan.plan(_report([_device(catalogue={
        "peak_tflops_bf16": 989.0, "memory_bandwidth_gbs": 4800.0,
        "provenance": SPEC})]), Workload("llama2-70b", 70, batch=32))
    assert "not a cross-vendor verdict" in result["note"]


def test_work_that_fits_nowhere_still_returns_a_reason():
    """The M2's 8 GB cannot hold a 70B model, and saying so is the result."""
    m2 = _device(name="Apple M2", identity={"memory_total_gb": 8},
                 catalogue={"peak_tflops_bf16": 3.6,
                            "memory_bandwidth_gbs": 100.0,
                            "provenance": Provenance.ESTIMATED.value})
    result = scan.plan(_report([m2]),
                       Workload("llama2-70b", 70, weight_bits=4, batch=32))
    row = result["ranked"][0]
    assert row["fits"] is False
    assert row["decode_tokens_per_second"] is None
    assert "memory" in row["bound_by"]


def test_a_published_measurement_is_preferred_over_the_prediction():
    """The roofline cannot see software-stack maturity; a submission can.

    Cross-vendor the physics ranking actually inverts against published
    results, which is why a measured rate must win wherever one exists.
    """
    device = _device(count=1, catalogue={"peak_tflops_bf16": 989.0,
                                         "memory_bandwidth_gbs": 4800.0,
                                         "provenance": SPEC},
                     published=[{"model": "llama2-70b-99.9",
                                 "units": "Tokens/s",
                                 "per_accelerator": 4310.0}])
    result = scan.plan(_report([device]),
                       Workload("llama2-70b", 70, weight_bits=8, batch=64))
    row = result["ranked"][0]
    assert row["decode_tokens_per_second"] == 4310.0
    assert row["provenance"] == PUBLISHED
