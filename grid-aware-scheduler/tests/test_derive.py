"""Scaling a measured chip to its siblings, and refusing to when it cannot."""
from __future__ import annotations

import pytest

from hardware.derive import (DERIVED, M2_FAMILY, MEASURED_M2,
                             ULTRAFUSION_EFFICIENCY, ChipSpec,
                             DerivationRefused, Measurement, best, derive,
                             derive_family)


def _anchor() -> tuple[Measurement, ChipSpec]:
    return MEASURED_M2, M2_FAMILY["m2"]


# --- the licence, and its edges -------------------------------------------

def test_a_different_generation_is_refused_rather_than_scaled():
    """The whole licence is one core design per generation. M3 is not M2."""
    anchor, spec = _anchor()
    m3_max = ChipSpec("m3-max", "M3", 40, 12, 4, 400.0, 128.0)
    with pytest.raises(DerivationRefused, match="different GPU core"):
        derive(anchor, m3_max, spec)


def test_a_zero_core_target_is_refused():
    anchor, spec = _anchor()
    broken = ChipSpec("weird", "M2", 0, 4, 4, 100.0, 8.0)
    with pytest.raises(DerivationRefused):
        derive(anchor, broken, spec)


def test_an_anchor_outside_the_family_table_cannot_find_its_siblings():
    stranger = Measurement("not-a-chip", 8, 2583.2, 75.7, 100.0)
    with pytest.raises(DerivationRefused, match="not in the family table"):
        derive_family(stranger)


# --- compute follows cores ------------------------------------------------

def test_per_core_rate_uses_the_measured_core_count():
    """8 cores, not the 10-core top configuration."""
    assert MEASURED_M2.gpu_cores == 8
    assert MEASURED_M2.gflops_per_gpu_core == pytest.approx(2583.2 / 8)


def test_compute_scales_linearly_with_gpu_cores_within_the_family():
    anchor, spec = _anchor()
    pro = derive(anchor, M2_FAMILY["m2-pro"], spec)
    expected = anchor.gflops_per_gpu_core * M2_FAMILY["m2-pro"].gpu_cores
    assert pro.gemm_fp16_gflops == pytest.approx(expected)
    # 19 cores against the anchor's 8 — comfortably more than double.
    assert pro.gemm_fp16_gflops > 2 * anchor.gemm_fp16_gflops


# --- bandwidth follows the bus, and this is the easy mistake --------------

def test_bandwidth_comes_from_the_bus_not_the_core_count():
    """Cores and bus widen on different schedules; conflating them is wrong."""
    anchor, spec = _anchor()
    pro = derive(anchor, M2_FAMILY["m2-pro"], spec)

    by_bus = 200.0 * anchor.bandwidth_efficiency
    by_cores = anchor.memory_bandwidth_gbs * (19 / 8)
    assert pro.memory_bandwidth_gbs == pytest.approx(by_bus)
    assert pro.memory_bandwidth_gbs != pytest.approx(by_cores)


def test_the_achieved_fraction_of_the_bus_is_what_travels():
    anchor, _ = _anchor()
    assert anchor.bandwidth_efficiency == pytest.approx(0.757)


def test_a_zero_spec_bus_does_not_divide_by_zero():
    odd = Measurement("m2", 8, 2583.2, 75.7, 0.0)
    assert odd.bandwidth_efficiency == 1.0


def test_every_derived_device_explains_its_bandwidth():
    anchor, spec = _anchor()
    for key in ("m2-pro", "m2-max", "m2-ultra"):
        device = derive(anchor, M2_FAMILY[key], spec)
        assert any("bus" in note for note in device.notes)


# --- the two non-linear derates -------------------------------------------

def test_the_two_die_ultra_is_not_claimed_to_be_linear():
    anchor, spec = _anchor()
    ultra = derive(anchor, M2_FAMILY["m2-ultra"], spec)
    linear = anchor.gflops_per_gpu_core * 76
    assert ultra.gemm_fp16_gflops == pytest.approx(
        linear * ULTRAFUSION_EFFICIENCY)
    assert any("interconnect" in note for note in ultra.notes)


def test_a_cooled_target_records_that_the_fanless_anchor_understates_it():
    anchor, spec = _anchor()
    max_chip = derive(anchor, M2_FAMILY["m2-max"], spec)
    assert any("fanless" in note for note in max_chip.notes)
    assert any("Conservative" in note for note in max_chip.notes)


# --- provenance -----------------------------------------------------------

def test_derived_figures_are_labelled_derived_and_name_their_anchor():
    anchor, spec = _anchor()
    pro = derive(anchor, M2_FAMILY["m2-pro"], spec)
    assert pro.provenance == DERIVED
    assert pro.anchor_key == "m2"
    assert pro.public_dict()["derived_from"] == "m2"


def test_the_anchor_is_never_given_a_derived_figure_over_its_measurement():
    """A derived number must never sit on top of a real measurement."""
    assert "m2" not in derive_family(MEASURED_M2)
    assert set(derive_family(MEASURED_M2)) == {"m2-pro", "m2-max", "m2-ultra"}


def test_a_measurement_always_beats_a_derivation():
    assert best((DERIVED, 5000.0), ("MEASURED", 2583.2)) == ("MEASURED", 2583.2)


def test_derived_beats_a_vendor_spec_and_an_estimate():
    assert best(("SPEC", 4000.0), (DERIVED, 6134.0)) == (DERIVED, 6134.0)
    assert best(("ESTIMATED", 1.0), (DERIVED, 2.0)) == (DERIVED, 2.0)


def test_published_third_party_data_outranks_our_derivation():
    assert best((DERIVED, 9.0), ("PUBLISHED", 8.0)) == ("PUBLISHED", 8.0)


def test_a_well_sourced_absence_never_wins():
    """MEASURED None is still nothing. A worse-labelled real number beats it."""
    assert best(("MEASURED", None), ("SPEC", 4000.0)) == ("SPEC", 4000.0)


def test_nothing_at_all_reports_unavailable():
    assert best(("MEASURED", None), (DERIVED, None)) == ("UNAVAILABLE", None)


def test_an_unknown_provenance_label_loses_to_a_known_one():
    assert best(("INVENTED", 1.0), ("SPEC", 2.0)) == ("SPEC", 2.0)
