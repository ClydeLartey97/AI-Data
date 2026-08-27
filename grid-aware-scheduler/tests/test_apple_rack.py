"""A rack of Apple-silicon boards, modelled from one measured chip.

The tests that matter here are the refusals. An Apple-silicon rack looks like
a GPU rack from a distance and behaves unlike one in the two ways that decide
whether a deployment is possible at all, so those two are locked hardest.
"""
from __future__ import annotations

import pytest

from hardware import derive
from hardware.apple_rack import (DEFAULT_CHASSIS_OVERHEAD,
                                 INDEPENDENT_SCALING_EFFICIENCY,
                                 REPORTED_CHASSIS, BoardSpec, ChassisSpec,
                                 RackRefused, board_from_projection, plan)
from hardware.base import Provenance
from hardware.roofline import Workload


def _board(memory_gb: float = 24.0, watts: float = 20.0) -> BoardSpec:
    """A board carrying the chip this project actually measured."""
    return BoardSpec(
        name="Apple M2 (measured)",
        memory_gb=memory_gb,
        memory_bandwidth_gbs=derive.MEASURED_M2.memory_bandwidth_gbs,
        achievable_tflops=derive.MEASURED_M2.gemm_fp16_gflops / 1000,
        board_watts=watts,
        compute_provenance=Provenance.MEASURED.value,
        bandwidth_provenance=Provenance.MEASURED.value,
    )


def _small_model() -> Workload:
    return Workload("8B 4-bit", 8, weight_bits=4, batch=32)


def _large_model() -> Workload:
    return Workload("70B 4-bit", 70, weight_bits=4, batch=32)


# --- The defining constraint: boards do not share memory ---

def test_a_model_too_large_for_one_board_cannot_run_however_many_boards():
    """The whole architecture in one assertion.

    32 boards of 24 GB is 768 GB of silicon, and none of it helps: the model
    has to fit in a single board's memory because nothing pools it. Reporting
    the chassis total here would turn a physically impossible deployment into
    an attractive number.
    """
    result = plan(_large_model(), _board(memory_gb=24.0), REPORTED_CHASSIS)
    assert result.fits_per_board is False
    assert result.tokens_per_second is None
    assert result.memory_required_gb > 24.0


def test_the_refusal_says_the_chassis_total_is_not_available():
    result = plan(_large_model(), _board(memory_gb=24.0), REPORTED_CHASSIS)
    joined = " ".join(result.notes)
    assert "do not share memory" in joined
    assert "768" in joined  # the total that is deliberately not usable


def test_a_bigger_board_runs_what_a_smaller_one_refused():
    """The constraint is per-board memory, not the model or the rack."""
    small = plan(_large_model(), _board(memory_gb=24.0), REPORTED_CHASSIS)
    large = plan(_large_model(), _board(memory_gb=192.0), REPORTED_CHASSIS)
    assert small.fits_per_board is False
    assert large.fits_per_board is True
    assert large.tokens_per_second is not None


# --- Scaling licence, and its edge ---

def test_independent_requests_replicate_across_boards():
    chassis = ChassisSpec("test", boards=32)
    result = plan(_small_model(), _board(), chassis)
    assert result.board_tokens_per_second is not None
    expected = (result.board_tokens_per_second * 32
                * INDEPENDENT_SCALING_EFFICIENCY)
    assert result.tokens_per_second == pytest.approx(expected)


def test_one_board_is_not_penalised_for_being_alone():
    single = plan(_small_model(), _board(), ChassisSpec("one", boards=1))
    assert single.tokens_per_second == pytest.approx(
        single.board_tokens_per_second * INDEPENDENT_SCALING_EFFICIENCY)


def test_splitting_one_model_across_boards_is_refused_not_estimated():
    """The backplane is unmeasured, so a number here would be a guess."""
    with pytest.raises(RackRefused, match="do not share memory"):
        plan(_small_model(), _board(), REPORTED_CHASSIS,
             independent_requests=False)


def test_scaling_efficiency_above_linear_is_rejected():
    """MLPerf shows superlinear figures; they are tuning, not hardware."""
    with pytest.raises(ValueError):
        plan(_small_model(), _board(), REPORTED_CHASSIS,
             scaling_efficiency=1.4)


# --- Power, and the energy figure the rest of the project consumes ---

def test_chassis_power_includes_overhead_above_the_boards():
    chassis = ChassisSpec("test", boards=10, overhead_multiplier=1.2)
    result = plan(_small_model(), _board(watts=20.0), chassis)
    assert result.compute_kw == pytest.approx(0.2)
    assert result.total_kw == pytest.approx(0.24)


def test_overhead_can_never_reduce_draw():
    """Fans and power conversion add load. A multiplier below 1 is nonsense."""
    with pytest.raises(ValueError):
        ChassisSpec("impossible", boards=4, overhead_multiplier=0.8)


def test_tokens_per_kwh_is_derived_from_the_total_not_the_boards():
    """Energy accounting must include the chassis, or it flatters the rack."""
    chassis = ChassisSpec("test", boards=8, overhead_multiplier=1.5)
    result = plan(_small_model(), _board(), chassis)
    expected = result.tokens_per_second * 3600 / result.total_kw
    assert result.tokens_per_kwh == pytest.approx(expected)
    assert result.total_kw > result.compute_kw


def test_a_default_overhead_is_flagged_as_not_declared():
    result = plan(_small_model(), _board(), ChassisSpec("test", boards=4))
    assert any("module default" in note for note in result.notes)


# --- Provenance ---

def test_the_weakest_input_sets_the_result_provenance():
    """Measured compute plus an estimated wattage is not a measured rack."""
    result = plan(_small_model(), _board(), REPORTED_CHASSIS)
    assert result.provenance != Provenance.MEASURED.value


def test_the_reported_chassis_carries_its_source_into_every_result():
    result = plan(_small_model(), _board(), REPORTED_CHASSIS)
    joined = " ".join(result.notes)
    assert "press report" in joined
    assert "not reported" in joined


def test_the_reported_chassis_declares_only_a_board_count():
    """Memory, power and overhead were never published and are not invented."""
    assert REPORTED_CHASSIS.boards == 32
    assert REPORTED_CHASSIS.overhead_multiplier == DEFAULT_CHASSIS_OVERHEAD
    assert "board count only" in REPORTED_CHASSIS.source


def test_a_chassis_holds_at_least_one_board():
    with pytest.raises(ValueError):
        ChassisSpec("empty", boards=0)


# --- Building a board out of a projected part ---

def test_a_projected_part_becomes_a_board_carrying_its_provenance():
    projected = derive.project(
        derive.MEASURED_M2,
        derive.PublishedPart(key="server-soc", family="M5",
                             spec_peak_gflops=9000.0,
                             memory_bandwidth_gbs=400.0, max_memory_gb=64.0))
    board = board_from_projection(projected, board_watts=60.0)
    assert board.compute_provenance == derive.PROJECTED
    assert board.memory_gb == 64.0


def test_server_board_memory_can_be_overridden_below_the_retail_maximum():
    """A server board is populated for its role, not for a configurator.

    Since per-board memory is the binding constraint, defaulting to the retail
    maximum would be the most flattering wrong assumption available.
    """
    projected = derive.project(
        derive.MEASURED_M2,
        derive.PublishedPart(key="server-soc", family="M5",
                             spec_peak_gflops=9000.0,
                             memory_bandwidth_gbs=400.0, max_memory_gb=64.0))
    board = board_from_projection(projected, board_watts=60.0, memory_gb=16.0)
    assert board.memory_gb == 16.0


def test_a_plan_serialises_without_leaking_objects():
    payload = plan(_small_model(), _board(), REPORTED_CHASSIS).public_dict()
    assert payload["boards"] == 32
    assert payload["fits_per_board"] is True
    assert isinstance(payload["notes"], list)
    assert "board_prediction" not in payload
