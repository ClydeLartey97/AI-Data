"""What a declared plant will produce, by plant kind.

The scheduler places heavy work where the generation feeding a site is
strongest. That is a forecast question, and the answer is different for every
kind of plant — which is the distinction this module exists to hold.

**Three behaviours, and conflating them is the failure mode.**

*Weather-driven and must-run.* Solar and wind produce what the weather gives
them. There is a real peak to catch, and catching it absorbs energy that would
otherwise be curtailed. This is where the environmental argument is strongest
and where a forecast genuinely helps.

*Dispatchable.* A gas turbine has no peak to catch. It produces what it is
asked to produce, so its *availability* is close to nameplate whenever it is
running, and asking it for more does not harvest free energy — it burns more
fuel. Weather still matters a little, because turbine output falls in hot air,
but the scheduling logic is not "wait for the good hour".

*Near-constant.* Nuclear, geothermal and biomass run flat by design. Their real
variation comes from planned outages and fuel or steam limits, none of which is
in a weather forecast. Modelling them from weather would be inventing a shape.

**The defect this replaces.** The previous code asked for a weather forecast and
fell through to a capacity factor of exactly 1.0 for every kind except solar and
wind — silently. A hydro plant in drought, a nuclear unit mid-refuelling and a
biomass plant out of fuel all read as producing full nameplate in every interval,
and nothing said so. A forecast that cannot be made must be reported as absent,
never as full output.
"""
from __future__ import annotations

from typing import Any

from core.renewables import solar_capacity_factor, wind_capacity_factor

#: Kinds whose output this project can genuinely derive from the weather it
#: fetches. Anything outside this set gets a declared availability and a
#: warning, never a modelled shape.
WEATHER_MODELLED_KINDS = frozenset({"solar", "wind", "gas", "oil"})

#: Kinds that produce what the weather gives them, with no operator choice in
#: the matter. These are the ones with a peak worth scheduling into.
MUST_RUN_VARIABLE_KINDS = frozenset({"solar", "wind"})

#: Kinds that run flat by design. Their variation is outages and fuel limits,
#: which no weather forecast contains.
NEAR_CONSTANT_KINDS = frozenset({"nuclear", "geothermal", "biomass", "coal"})

#: Gas and oil turbines are rated at ISO 2314 conditions — 15 °C at sea level.
#: Output falls in warmer air because the compressor ingests less mass per
#: unit volume. Roughly 0.6% per degree is the conventional planning figure;
#: a specific machine's curve is the manufacturer's, not ours, so this is an
#: ESTIMATED correction and is bounded rather than extrapolated indefinitely.
TURBINE_ISO_TEMP_C = 15.0
TURBINE_DERATE_PER_C = 0.006
TURBINE_MIN_FACTOR = 0.80
TURBINE_MAX_FACTOR = 1.05


def can_model_from_weather(kind: str) -> bool:
    """Whether a real forecast exists for this plant kind."""
    return kind in WEATHER_MODELLED_KINDS


def turbine_temperature_factor(temperature_c: float | None) -> float:
    """Combustion-turbine output against ambient temperature, 0-1ish.

    Bounded at both ends on purpose. The linear derate is a planning
    approximation valid across ordinary ambient conditions; extended far
    enough it would predict a turbine producing nothing in a heatwave or
    comfortably above nameplate in an Arctic winter, and neither is true.
    """
    if temperature_c is None:
        return 1.0
    factor = 1.0 - TURBINE_DERATE_PER_C * (temperature_c - TURBINE_ISO_TEMP_C)
    return max(TURBINE_MIN_FACTOR, min(TURBINE_MAX_FACTOR, factor))


def weather_capacity_factor(kind: str, point: Any) -> float | None:
    """Capacity factor for one interval, or None if this kind has no forecast.

    ``None`` is the load-bearing return value. It means "no forecast exists
    for this plant kind", and the caller must fall back to a declared
    availability and say that it did — not substitute full output.
    """
    if point is None:
        return None
    if kind == "solar":
        return solar_capacity_factor(
            getattr(point, "solar_radiation_wm2", None),
            getattr(point, "temperature_c", None))
    if kind == "wind":
        return wind_capacity_factor(getattr(point, "wind_speed_100m_ms", None))
    if kind in {"gas", "oil"}:
        # Availability, not output. A dispatchable machine can deliver close
        # to nameplate whenever it is running; hot air is the one physical
        # reason it cannot. Whether it *should* run is a carbon question,
        # answered in core.supply_advice, not a forecast question.
        return turbine_temperature_factor(getattr(point, "temperature_c", None))
    return None


def availability_note(kind: str, method: str) -> str | None:
    """Why a declared method will not do what its name suggests, if so.

    Returned as a warning rather than an exception because a site is usually
    declaring several plants at once, and refusing the whole document over one
    optimistic method would lose the rest of a valid declaration.
    """
    if method != "weather":
        return None
    if can_model_from_weather(kind):
        return None
    if kind in NEAR_CONSTANT_KINDS:
        return (f"{kind} runs flat by design — its real variation is planned "
                f"outages and fuel limits, which no weather forecast contains. "
                f"Using the declared availability instead; declare "
                f"'availability_factor', or 'method': 'series' if you have a "
                f"real output profile.")
    if kind == "hydro":
        return ("hydro output depends on river flow and reservoir management, "
                "which this project does not fetch. Using the declared "
                "availability instead; 'method': 'series' with a real profile "
                "is the honest option.")
    return (f"no weather model exists for '{kind}'. Using the declared "
            f"availability instead.")


def describe(kind: str) -> dict[str, Any]:
    """How this plant kind behaves, for an operator surface to render."""
    return {
        "kind": kind,
        "weather_modelled": can_model_from_weather(kind),
        "must_run_variable": kind in MUST_RUN_VARIABLE_KINDS,
        "near_constant": kind in NEAR_CONSTANT_KINDS,
        "has_peak_worth_scheduling_into": kind in MUST_RUN_VARIABLE_KINDS,
    }
