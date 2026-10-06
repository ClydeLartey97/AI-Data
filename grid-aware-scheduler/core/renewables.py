"""
On-site renewable output from weather: solar and wind capacity factors.

**Why this is built rather than called out to.** Renewables.ninja is the
reference tool for this and it is genuinely better modelled: MERRA-2/SARAH
reanalysis, real turbine power curves, bias correction validated against
metered national output. But it needs a token, rate-limits hard (tens of
requests an hour), and answers in seconds. A simulator that recomputes when
you drag a slider cannot live on that.

The physics it wraps is standard and publishable in a few lines, and the
inputs — global horizontal irradiance and 100 m wind speed — are already being
fetched from Open-Meteo for the weather panel. So this computes locally, in
microseconds, on data we already have.

**What is given up by doing so, stated plainly:** no bias correction against
metered output, a generic turbine power curve instead of a specific model, no
shading/soiling/downtime, and irradiance treated as plane-of-array when a real
installation has tilt and azimuth. Expect the shape to be right and the level
to be optimistic by some margin. These are ESTIMATED figures and must be
labelled as such. Renewables.ninja remains the right cross-check, and
validating this against it on a handful of sites is the obvious next step —
the same "derive, then check against a known-good source" method used for
hardware profiling.

The output that actually matters is not generation, it is **matching**: for
each half-hour, how much of the load on-site renewables could serve, and how
much has to come off the grid. Averages hide this completely — a site can be
"100% renewable on an annual average" while importing fossil power every
night. That matching is done per settlement period by the dispatch in
`core/energy.py`, using the capacity factors computed here.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- PV -------------------------------------------------------------------
#: Standard test condition irradiance, W/m2.
STC_IRRADIANCE = 1000.0
#: Power lost per degree the cell sits above 25 C. Typical crystalline silicon.
PV_TEMP_COEFF = 0.004
#: Nominal operating cell temperature, C — the usual datasheet value.
NOCT = 45.0
#: Everything not captured elsewhere: inverter losses, wiring, soiling,
#: mismatch. 0.80 is the conventional default performance ratio.
PERFORMANCE_RATIO = 0.80

# --- wind -----------------------------------------------------------------
#: Generic utility-scale turbine, 100 m hub height. A real power curve is
#: turbine-specific; this is the standard cubic idealisation between cut-in
#: and rated, which is right in shape and approximate in level.
WIND_CUT_IN_MS = 3.0
WIND_RATED_MS = 12.0
WIND_CUT_OUT_MS = 25.0


def solar_capacity_factor(ghi_wm2: float | None, temp_c: float | None = None) -> float:
    """Fraction of rated DC capacity a PV array would produce, 0-1.

    Cell temperature is derived from air temperature and irradiance, because
    panels lose efficiency as they heat — which is why a hot cloudless
    afternoon can under-perform a cool bright morning.
    """
    if not ghi_wm2 or ghi_wm2 <= 0:
        return 0.0
    air = 15.0 if temp_c is None else temp_c
    cell = air + (ghi_wm2 / 800.0) * (NOCT - 20.0)
    temp_derate = max(0.0, 1.0 - PV_TEMP_COEFF * (cell - 25.0))
    return max(0.0, min(1.0, (ghi_wm2 / STC_IRRADIANCE) * temp_derate * PERFORMANCE_RATIO))


def wind_capacity_factor(wind_ms: float | None) -> float:
    """Fraction of rated capacity a turbine would produce, 0-1.

    Cubic between cut-in and rated because power in wind goes as the cube of
    speed — the single most important fact about wind, and the reason a modest
    increase in wind speed produces a large increase in output.
    """
    if wind_ms is None or wind_ms < WIND_CUT_IN_MS or wind_ms >= WIND_CUT_OUT_MS:
        return 0.0
    if wind_ms >= WIND_RATED_MS:
        return 1.0
    lo, hi = WIND_CUT_IN_MS ** 3, WIND_RATED_MS ** 3
    return (wind_ms ** 3 - lo) / (hi - lo)


@dataclass
class SiteCapacity:
    """Installed on-site generation, in kW of rated capacity."""

    solar_kw: float = 0.0
    wind_kw: float = 0.0

    @property
    def total_kw(self) -> float:
        return self.solar_kw + self.wind_kw


