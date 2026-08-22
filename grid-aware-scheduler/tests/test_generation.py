"""Per-kind generation availability, and the run-or-import comparison."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from core import generation
from core.supply_advice import (MATERIAL_DIFFERENCE_G_PER_KWH, SourceOption,
                                advise, advise_interval)

T0 = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


@dataclass
class Point:
    solar_radiation_wm2: float | None = None
    wind_speed_100m_ms: float | None = None
    temperature_c: float | None = None


# --- which kinds can honestly be forecast ---------------------------------

def test_only_kinds_with_a_real_model_claim_a_forecast():
    assert generation.can_model_from_weather("solar")
    assert generation.can_model_from_weather("wind")
    assert generation.can_model_from_weather("gas")
    for kind in ("nuclear", "hydro", "geothermal", "biomass", "coal"):
        assert not generation.can_model_from_weather(kind)


def test_unmodellable_kinds_return_none_not_full_output():
    """The defect this replaces: a silent 1.0 read as a plant at nameplate."""
    point = Point(solar_radiation_wm2=800, wind_speed_100m_ms=9,
                  temperature_c=20)
    for kind in ("nuclear", "hydro", "geothermal", "biomass", "coal"):
        assert generation.weather_capacity_factor(kind, point) is None


def test_solar_and_wind_still_track_the_weather():
    bright = generation.weather_capacity_factor(
        "solar", Point(solar_radiation_wm2=800, temperature_c=20))
    dark = generation.weather_capacity_factor(
        "solar", Point(solar_radiation_wm2=0, temperature_c=20))
    assert bright > 0.4 and dark == 0.0

    windy = generation.weather_capacity_factor(
        "wind", Point(wind_speed_100m_ms=12))
    calm = generation.weather_capacity_factor(
        "wind", Point(wind_speed_100m_ms=1))
    assert windy == 1.0 and calm == 0.0


# --- turbines lose output in hot air, but not without limit ---------------

def test_turbine_output_falls_as_ambient_temperature_rises():
    cool = generation.turbine_temperature_factor(5)
    iso = generation.turbine_temperature_factor(15)
    hot = generation.turbine_temperature_factor(35)
    assert cool > iso > hot
    assert iso == pytest.approx(1.0)


def test_the_turbine_derate_is_bounded_at_both_ends():
    """A linear derate extended far enough predicts nonsense in both directions."""
    assert generation.turbine_temperature_factor(200) == generation.TURBINE_MIN_FACTOR
    assert generation.turbine_temperature_factor(-100) == generation.TURBINE_MAX_FACTOR


def test_a_missing_temperature_does_not_derate():
    assert generation.turbine_temperature_factor(None) == 1.0


# --- the warning that makes the fallback visible --------------------------

def test_asking_for_a_forecast_that_cannot_be_made_is_warned():
    note = generation.availability_note("nuclear", "weather")
    assert note is not None and "flat by design" in note

    hydro = generation.availability_note("hydro", "weather")
    assert hydro is not None and "river flow" in hydro


def test_no_warning_when_the_method_is_honest():
    assert generation.availability_note("solar", "weather") is None
    assert generation.availability_note("nuclear", "series") is None
    assert generation.availability_note("nuclear", "flat") is None


# --- run or import --------------------------------------------------------

def _gas(kw=1000.0, carbon=450.0):
    return SourceOption("gas-1", "gas", kw, carbon, dispatchable=True,
                        must_run=False)


def _solar(kw=500.0):
    return SourceOption("pv-1", "solar", kw, 0.0, dispatchable=False,
                        must_run=True)


def test_own_gas_beats_a_dirty_grid():
    advice = advise_interval(T0, [_gas()], grid_carbon_g_per_kwh=600)
    assert advice.recommendation == "use_onsite"
    assert advice.dispatchable_worth_running_kw == 1000.0


def test_own_gas_loses_to_a_clean_grid_and_importing_is_advised():
    """The case the 'run when your plant is producing' story gets wrong."""
    advice = advise_interval(T0, [_gas()], grid_carbon_g_per_kwh=60)
    assert advice.recommendation == "import"
    assert advice.dispatchable_worth_running_kw == 0.0
    assert advice.carbon_saved_g_per_kwh == pytest.approx(390.0)
    assert "dirtier than the grid" in advice.reason


def test_a_marginal_difference_is_not_treated_as_a_decision():
    """One side is a regional average; a few gCO2 of edge is noise."""
    grid = 450.0 + MATERIAL_DIFFERENCE_G_PER_KWH / 2
    advice = advise_interval(T0, [_gas(carbon=450.0)],
                             grid_carbon_g_per_kwh=grid)
    assert advice.recommendation == "import"  # not worth switching to onsite


def test_must_run_generation_is_always_taken_regardless_of_grid_carbon():
    """Solar is produced anyway. Refusing it curtails rather than saves."""
    advice = advise_interval(T0, [_solar()], grid_carbon_g_per_kwh=5)
    assert advice.recommendation == "use_onsite"
    assert advice.must_run_kw == 500.0
    assert "produced anyway" in advice.reason


def test_a_mixed_fleet_runs_only_the_sources_that_beat_the_grid():
    clean_gas = SourceOption("g2", "gas", 400.0, 200.0, True, False)
    advice = advise_interval(T0, [_gas(carbon=450.0), clean_gas],
                             grid_carbon_g_per_kwh=300)
    assert advice.recommendation == "mixed"
    assert advice.dispatchable_worth_running_kw == 400.0


def test_a_missing_grid_figure_leaves_the_interval_unadvised():
    """No signal is not a licence to recommend burning fuel."""
    advice = advise_interval(T0, [_gas()], grid_carbon_g_per_kwh=None)
    assert advice.recommendation == "unknown"
    assert advice.dispatchable_worth_running_kw == 0.0


def test_no_generation_at_all_falls_back_to_the_grid():
    advice = advise_interval(T0, [], grid_carbon_g_per_kwh=300)
    assert advice.recommendation == "import"


def test_summary_reports_the_share_of_intervals_the_plant_is_dirtier():
    stamps = [T0 + timedelta(hours=h) for h in range(4)]
    options = [(s, [_gas()]) for s in stamps]
    grid = {stamps[0]: 60.0, stamps[1]: 60.0,   # own gas loses
            stamps[2]: 600.0, stamps[3]: 600.0}  # own gas wins
    result = advise(options, grid)
    assert result.dirtier_share == pytest.approx(0.5)
    assert "50%" in result.summary()


def test_the_average_versus_marginal_limit_travels_with_every_result():
    result = advise([(T0, [_gas()])], {T0: 300.0})
    assert any("marginal" in note for note in result.notes)
    assert any("balancing-area" in note for note in result.notes)


def test_a_site_with_no_dispatchable_plant_has_no_choice_to_make():
    result = advise([(T0, [_solar()])], {T0: 300.0})
    assert result.dirtier_share is None
    assert "no run-or-import choice" in result.summary()
