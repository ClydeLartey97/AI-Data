"""Scaling a measured chip to its siblings, and refusing to when it cannot."""
from __future__ import annotations

import pytest

import hardware.derive as derive_module
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


# --- Multi-die packages, and the quad-die part that broke the flat derate ---

def test_a_single_die_pays_no_interconnect_cost():
    assert derive_module.interconnect_hops(1) == 0
    assert derive_module.multi_die_efficiency(1) == 1.0


def test_two_dies_are_one_crossing_and_match_the_original_constant():
    """The two-die case must not move: every existing Ultra depends on it."""
    assert derive_module.interconnect_hops(2) == 1
    assert derive_module.multi_die_efficiency(2) == pytest.approx(0.90)


def test_a_quad_die_package_pays_two_crossings_not_one():
    """The bug this fixes. A quad-die Ultra is two dual-die parts joined.

    Applying the two-die derate to it overstates dense throughput by about
    11%, silently, on exactly the part someone reaches for when modelling a
    large Apple-silicon deployment.
    """
    assert derive_module.interconnect_hops(4) == 2
    assert derive_module.multi_die_efficiency(4) == pytest.approx(0.81)
    flat = derive_module.ULTRAFUSION_EFFICIENCY
    overstatement = flat / derive_module.multi_die_efficiency(4)
    assert overstatement == pytest.approx(1.111, abs=0.01)


def test_die_count_must_be_physical():
    with pytest.raises(ValueError):
        derive_module.interconnect_hops(0)


# --- Crossing a generation boundary ---

def _published_part(**kwargs):
    defaults = dict(key="future-ultra", family="M5", spec_peak_gflops=20000.0,
                    memory_bandwidth_gbs=1600.0, max_memory_gb=512.0,
                    gpu_cores=160)
    defaults.update(kwargs)
    return derive_module.PublishedPart(**defaults)


def test_the_anchor_knows_what_fraction_of_peak_it_reached():
    anchor = derive_module.MEASURED_M2
    assert anchor.compute_efficiency == pytest.approx(0.897, abs=0.005)
    assert anchor.bandwidth_efficiency == pytest.approx(0.757, abs=0.005)


def test_projection_applies_achieved_fractions_not_the_per_core_rate():
    """Across a generation only the efficiency travels, never the rate."""
    anchor = derive_module.MEASURED_M2
    result = derive_module.project(anchor, _published_part(dies=1))
    assert result.provenance == derive_module.PROJECTED
    assert result.gemm_fp16_gflops == pytest.approx(
        20000.0 * anchor.compute_efficiency)
    assert result.memory_bandwidth_gbs == pytest.approx(
        1600.0 * anchor.bandwidth_efficiency)


def test_a_projection_never_outranks_a_measurement_or_a_publication():
    rank = derive_module.PROVENANCE_RANK
    assert rank["MEASURED"] < rank["PUBLISHED"] < rank[DERIVED] \
        < rank[derive_module.PROJECTED] < rank["SPEC"]


def test_projection_onto_a_quad_die_part_pays_both_crossings():
    anchor = derive_module.MEASURED_M2
    single = derive_module.project(anchor, _published_part(dies=1))
    quad = derive_module.project(anchor, _published_part(dies=4))
    assert quad.gemm_fp16_gflops == pytest.approx(
        single.gemm_fp16_gflops * 0.81)


def test_projection_refuses_a_part_with_no_published_figures_at_all():
    """A part nobody has described cannot be projected onto. Refuse, not guess."""
    with pytest.raises(derive_module.DerivationRefused):
        derive_module.project(
            derive_module.MEASURED_M2,
            _published_part(spec_peak_gflops=None, memory_bandwidth_gbs=None))


def test_projection_refuses_an_anchor_with_no_published_peak():
    """Without a peak there is no achieved fraction, so nothing travels."""
    blind = derive_module.Measurement(
        chip_key="m2", gpu_cores=8, gemm_fp16_gflops=2583.2,
        memory_bandwidth_gbs=75.7, spec_bandwidth_gbs=100.0,
        spec_peak_gflops=None)
    with pytest.raises(derive_module.DerivationRefused):
        derive_module.project(blind, _published_part())


def test_the_source_of_a_published_figure_travels_into_the_result():
    """A press report must never read like a datasheet downstream."""
    result = derive_module.project(
        derive_module.MEASURED_M2,
        _published_part(source="press announcement, not a datasheet"))
    assert any("press announcement" in note for note in result.notes)


# --- Validating the projection method against a second real part ---

def _m1_ultra_part():
    return derive_module.published_part(
        "m1-ultra", "M1", derive_module.M1_FAMILY["m1-ultra"],
        source="Apple published specifications")


def test_the_m1_family_is_per_core_consistent_like_the_m2_family():
    """Published peaks divided by published cores should agree across members.

    If they do not, one of the rows is the wrong configuration, which is the
    error that produced the "72% of peak" figure earlier in this project.
    """
    rates = {
        key: derive_module.PUBLISHED_PEAK_GFLOPS[key] / spec.gpu_cores
        for key, spec in derive_module.M1_FAMILY.items()
    }
    assert max(rates.values()) - min(rates.values()) < 10.0


def test_the_ultra_is_recorded_as_a_two_die_part():
    assert derive_module.M1_FAMILY["m1-ultra"].dies == 2
    assert derive_module.M1_FAMILY["m1-max"].dies == 1


def test_a_projection_onto_the_m1_ultra_is_a_concrete_falsifiable_number():
    predicted = derive_module.project(derive_module.MEASURED_M2, _m1_ultra_part())
    assert predicted.provenance == derive_module.PROJECTED
    assert predicted.gemm_fp16_gflops > 0
    assert predicted.memory_bandwidth_gbs > 0


def _measured(gflops: float, bandwidth: float) -> "derive_module.Measurement":
    return derive_module.Measurement(
        chip_key="m1-ultra", gpu_cores=64, gemm_fp16_gflops=gflops,
        memory_bandwidth_gbs=bandwidth, spec_bandwidth_gbs=800.0,
        spec_peak_gflops=21000.0)


def test_a_projection_that_lands_close_is_reported_as_holding():
    part = _m1_ultra_part()
    predicted = derive_module.project(derive_module.MEASURED_M2, part)
    close = _measured(predicted.gemm_fp16_gflops * 1.05,
                      predicted.memory_bandwidth_gbs * 0.95)
    score = derive_module.validate_projection(
        derive_module.MEASURED_M2, part, close)
    assert score.holds is True
    assert score.compute_ratio == pytest.approx(1.05, abs=0.01)


def test_a_projection_that_misses_badly_is_reported_as_failing():
    """A failure is the useful result. It must not be softened."""
    part = _m1_ultra_part()
    predicted = derive_module.project(derive_module.MEASURED_M2, part)
    far = _measured(predicted.gemm_fp16_gflops * 0.5,
                    predicted.memory_bandwidth_gbs * 0.5)
    score = derive_module.validate_projection(
        derive_module.MEASURED_M2, part, far)
    assert score.holds is False
    assert any("did NOT hold" in note for note in score.notes)


def test_a_failing_projection_says_to_widen_or_withdraw_the_tier():
    part = _m1_ultra_part()
    predicted = derive_module.project(derive_module.MEASURED_M2, part)
    far = _measured(predicted.gemm_fp16_gflops * 0.4,
                    predicted.memory_bandwidth_gbs * 0.4)
    score = derive_module.validate_projection(
        derive_module.MEASURED_M2, part, far)
    joined = " ".join(score.notes)
    assert "withdraw" in joined


def test_scoring_a_two_die_target_notes_it_also_bounds_the_derate():
    part = _m1_ultra_part()
    predicted = derive_module.project(derive_module.MEASURED_M2, part)
    score = derive_module.validate_projection(
        derive_module.MEASURED_M2, part,
        _measured(predicted.gemm_fp16_gflops, predicted.memory_bandwidth_gbs))
    assert any("interconnect derate" in note for note in score.notes)


def test_no_measurement_of_the_target_ever_feeds_its_own_prediction():
    """The prediction must use published figures only, or it proves nothing."""
    part = _m1_ultra_part()
    low = derive_module.validate_projection(
        derive_module.MEASURED_M2, part, _measured(1000.0, 100.0))
    high = derive_module.validate_projection(
        derive_module.MEASURED_M2, part, _measured(30000.0, 900.0))
    assert low.predicted_gflops == high.predicted_gflops


def test_a_measurement_of_zero_cannot_be_scored():
    with pytest.raises(derive_module.DerivationRefused):
        derive_module.validate_projection(
            derive_module.MEASURED_M2, _m1_ultra_part(), _measured(0.0, 0.0))


def test_tolerance_must_be_a_fraction():
    with pytest.raises(ValueError):
        derive_module.validate_projection(
            derive_module.MEASURED_M2, _m1_ultra_part(),
            _measured(1.0, 1.0), tolerance=1.5)


def test_a_score_serialises_for_the_record():
    part = _m1_ultra_part()
    predicted = derive_module.project(derive_module.MEASURED_M2, part)
    payload = derive_module.validate_projection(
        derive_module.MEASURED_M2, part,
        _measured(predicted.gemm_fp16_gflops,
                  predicted.memory_bandwidth_gbs)).public_dict()
    assert payload["target"] == "m1-ultra"
    assert payload["holds"] is True
