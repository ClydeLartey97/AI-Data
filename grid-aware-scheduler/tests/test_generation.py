"""Per-kind generation availability."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core import generation


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
