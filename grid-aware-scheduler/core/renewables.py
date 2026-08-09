"""
On-site renewable output, and how much of a load it can actually cover.

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
night. Matching is computed per settlement period for that reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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


@dataclass
class Interval:
    """One settlement period of supply against demand."""

    timestamp: datetime
    demand_kw: float
    solar_kw: float
    wind_kw: float

    @property
    def available_kw(self) -> float:
        return self.solar_kw + self.wind_kw

    @property
    def matched_kw(self) -> float:
        """On-site generation actually consumed — never more than demand."""
        return min(self.available_kw, self.demand_kw)

    @property
    def imported_kw(self) -> float:
        """Load that must be offloaded to the grid."""
        return max(0.0, self.demand_kw - self.available_kw)

    @property
    def curtailed_kw(self) -> float:
        """Generation with nowhere to go — exported or spilled."""
        return max(0.0, self.available_kw - self.demand_kw)


@dataclass
class MatchResult:
    """How well a site's own generation covered its load."""

    intervals: list[Interval] = field(default_factory=list)
    period_hours: float = 0.5

    def _sum(self, attr: str) -> float:
        return sum(getattr(i, attr) for i in self.intervals) * self.period_hours

    @property
    def demand_kwh(self) -> float:
        return self._sum("demand_kw")

    @property
    def matched_kwh(self) -> float:
        return self._sum("matched_kw")

    @property
    def imported_kwh(self) -> float:
        return self._sum("imported_kw")

    @property
    def curtailed_kwh(self) -> float:
        return self._sum("curtailed_kw")

    @property
    def hourly_matched_pct(self) -> float:
        """Share of demand met on-site **period by period**.

        This is the honest number. An annual-average figure can read 100%
        while the site imports fossil power every night; matching each
        half-hour separately is what 24/7 carbon-free accounting means, and it
        is always the lower, less flattering figure.
        """
        return 0.0 if not self.demand_kwh else self.matched_kwh / self.demand_kwh * 100.0

    @property
    def annual_style_pct(self) -> float:
        """Generation as a share of demand, ignoring *when* it arrived.

        Reported only so the two can be shown side by side. The gap between
        this and ``hourly_matched_pct`` is exactly the claim that netting-off
        annual totals conceals.
        """
        generated = self._sum("available_kw")
        return 0.0 if not self.demand_kwh else min(generated / self.demand_kwh * 100.0, 1e6)

    @property
    def fully_covered_periods(self) -> int:
        return sum(1 for i in self.intervals if i.imported_kw <= 1e-9)


def match_load(
    weather: list,
    capacity: SiteCapacity,
    demand_kw: float,
    *,
    period_hours: float = 0.5,
) -> MatchResult:
    """Match a constant load against on-site generation, period by period.

    ``weather`` is a list of objects exposing ``timestamp``,
    ``solar_radiation_wm2``, ``wind_speed_100m_ms`` and ``temperature_c`` —
    i.e. what ``adapters.weather.WeatherAdapter`` returns.
    """
    intervals = [
        Interval(
            timestamp=w.timestamp,
            demand_kw=demand_kw,
            solar_kw=capacity.solar_kw * solar_capacity_factor(
                getattr(w, "solar_radiation_wm2", None), getattr(w, "temperature_c", None)),
            wind_kw=capacity.wind_kw * wind_capacity_factor(
                getattr(w, "wind_speed_100m_ms", None)),
        )
        for w in weather
    ]
    return MatchResult(intervals=intervals, period_hours=period_hours)
