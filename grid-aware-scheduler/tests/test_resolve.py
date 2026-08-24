"""Best-sourced device figures. The rule: a measurement is always used."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hardware.derive import DERIVED
from hardware.resolve import Figure, measured_from_baselines, resolve

MEASURED_M2_GFLOPS = 2583.2


@dataclass
class FakeDevice:
    peak_tflops_bf16: float = 3.6
    mfu: float = 0.20
    provenance: str = "ESTIMATED"


def _measured():
    return {"m2": {"achieved_gflops": MEASURED_M2_GFLOPS,
                   "bandwidth_gbs": 75.7}}


# --- the rule -------------------------------------------------------------

def test_a_stored_measurement_is_used_rather_than_the_catalogue():
    """The defect this module exists for: measured data sitting unused."""
    result = resolve("m2", measured=_measured(), catalogue_device=FakeDevice())
    assert result.achieved_gflops.provenance == "MEASURED"
    assert result.achieved_gflops.value == MEASURED_M2_GFLOPS
    assert "MEASURED" in result.summary()


def test_a_measurement_outranks_a_third_party_submission():
    result = resolve("m2", measured=_measured(),
                     published={"m2": {"achieved_gflops": 9999.0}})
    assert result.achieved_gflops.value == MEASURED_M2_GFLOPS


def test_a_published_submission_is_used_when_nothing_local_exists():
    result = resolve("h200-sxm",
                     published={"h200-sxm": {"achieved_gflops": 4310.0}})
    assert result.achieved_gflops.provenance == "PUBLISHED"


def test_an_unmeasured_unpublished_sibling_falls_back_to_derivation():
    result = resolve("m2-max", measured=_measured())
    assert result.achieved_gflops.provenance == DERIVED
    assert result.achieved_gflops.value > MEASURED_M2_GFLOPS


def test_a_device_nobody_knows_reports_unavailable_not_zero():
    result = resolve("some-future-part")
    assert not result.achieved_gflops.known
    assert result.achieved_gflops.provenance == "UNAVAILABLE"


# --- achieved is not peak -------------------------------------------------

def test_a_measurement_never_overwrites_the_theoretical_peak():
    """Different quantities. Substituting one would change every downstream
    calculation's meaning without saying so."""
    result = resolve("m2", measured=_measured(), catalogue_device=FakeDevice())
    assert result.peak_tflops.value == 3.6
    assert result.peak_tflops.provenance != "MEASURED"
    assert result.achieved_gflops.value == MEASURED_M2_GFLOPS


def test_utilisation_stays_estimated_even_on_a_measured_device():
    """Dense matmul is the ceiling; a transformer does not reach it."""
    result = resolve("m2", measured=_measured(), catalogue_device=FakeDevice())
    assert result.utilisation.provenance == "ESTIMATED"
    assert "utilisation ESTIMATED" in result.summary()


def test_the_ceiling_fraction_compares_against_the_measured_core_count():
    """The correction. The catalogue's 3.6 TFLOPS is the 10-core M2; the part
    measured here has 8 cores. Dividing by the 10-core figure gives 72%, which
    is what this project recorded and which understates the hardware. Against
    the 8-core peak of 2.88 TFLOPS the real figure is about 90%."""
    result = resolve("m2", measured=_measured(), catalogue_device=FakeDevice())
    assert result.comparable_peak_gflops == pytest.approx(2880.0)
    assert result.measured_utilisation == pytest.approx(0.897, abs=1e-3)
    assert result.measured_utilisation != pytest.approx(0.7176, abs=1e-3)
    assert any("core count" in note for note in result.notes)


def test_without_core_counts_the_raw_catalogue_peak_is_used():
    """An unknown part cannot be core-corrected, so it is not silently scaled."""
    result = resolve("unknown-part", measured={"unknown-part": {
        "achieved_gflops": 1800.0}}, catalogue_device=FakeDevice())
    assert result.comparable_peak_gflops == pytest.approx(3600.0)


def test_no_ceiling_fraction_without_both_halves():
    assert resolve("m2", measured=_measured()).measured_utilisation is None
    assert resolve("m2", catalogue_device=FakeDevice()).measured_utilisation is None


def test_a_zero_peak_does_not_divide_by_zero():
    result = resolve("m2", measured=_measured(),
                     catalogue_device=FakeDevice(peak_tflops_bf16=0.0))
    assert result.measured_utilisation is None


# --- derivation is never stacked on a measurement -------------------------

def test_the_measured_anchor_is_not_given_a_derived_figure():
    result = resolve("m2", measured=_measured())
    assert result.achieved_gflops.provenance == "MEASURED"
    assert not any("scaled" in note for note in result.notes)


def test_a_derived_device_carries_the_reasoning_that_produced_it():
    result = resolve("m2-ultra", measured=_measured())
    assert any("interconnect" in note for note in result.notes)
    assert any("bus" in note for note in result.notes)


# --- picking ---------------------------------------------------------------

def test_a_labelled_absence_never_beats_a_real_number():
    assert Figure(None, "MEASURED").known is False
    result = resolve("m2", measured={"m2": {"bandwidth_gbs": 75.7}},
                     published={"m2": {"achieved_gflops": 4000.0}})
    assert result.bandwidth_gbs.provenance == "MEASURED"
    assert result.achieved_gflops.provenance == "PUBLISHED"


# --- reading the baseline store -------------------------------------------

def test_baseline_rows_become_a_measured_map():
    rows = [
        {"device_key": "m2", "metric": "gemm_fp16_gflops", "value": 2583.2},
        {"device_key": "m2", "metric": "bandwidth_read_gbs", "value": 75.7},
    ]
    assert measured_from_baselines(rows) == {
        "m2": {"achieved_gflops": 2583.2, "bandwidth_gbs": 75.7}}


def test_baseline_rows_without_a_device_or_value_are_skipped():
    rows = [{"metric": "gemm_fp16_gflops", "value": 1.0},
            {"device_key": "m2", "metric": "gemm_fp16_gflops", "value": None}]
    assert measured_from_baselines(rows) == {}


def test_baseline_rows_may_be_objects_rather_than_dicts():
    @dataclass
    class Row:
        device_key: str
        metric: str
        value: float

    rows = [Row("m2", "gemm_fp16_gflops", 2583.2)]
    assert measured_from_baselines(rows)["m2"]["achieved_gflops"] == 2583.2


def test_no_rows_is_not_an_error():
    assert measured_from_baselines(None) == {}
    assert measured_from_baselines([]) == {}
