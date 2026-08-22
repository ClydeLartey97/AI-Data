"""Should this interval be served by the site's own plant, or by the grid?

Placing work where generation is strongest is the right rule for a plant that
produces whether you want it to or not. Solar and wind give you what the
weather gives you, and taking it displaces something dirtier or it is curtailed
and wasted. There is no decision to make: use it.

**A dispatchable plant is the opposite, and this is the case the "run when your
plant is producing" story gets wrong.** A gas turbine has no peak to catch. It
burns fuel in proportion to what you ask of it, so drawing more load produces
more emissions rather than harvesting free energy. Whether to run it is a real
choice with a real answer, and the answer changes hour to hour: against a grid
running on wind at 60 gCO2/kWh a 450 gCO2/kWh turbine is the dirty option, and
against the same grid at 600 in a still evening peak it is the clean one.

So this module answers, per interval, the question the power envelope alone
cannot: **of the supply physically available, which of it should actually be
used?** Must-run generation is always taken. Dispatchable on-site generation is
compared against the grid at that moment and recommended only when it wins.

## Two honesty limits, both structural

**Average against marginal.** Published grid carbon intensity is an *average*
across everything generating. The theoretically correct signal for "what does
one more kWh of my demand cause?" is the *marginal* intensity — the plant that
actually ramps to serve it — and marginal is normally higher than average,
because the marginal unit is usually thermal. Using the published average
therefore makes importing look *better* than it truly is, so this advice is
biased toward the grid. The bias direction is stated rather than corrected,
because a marginal-emissions feed is a different data product this project does
not have.

**Scope.** The grid figure is balancing-area or regional, as `adapters/`
already labels it. The on-site figure is one specific plant. Comparing them is
comparing a regional average against a point source; it is the comparison an
operator has to make, but it is not like-for-like and must never be presented
as though it were.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: Below this difference the two options are not meaningfully distinguishable,
#: given that one side is a regional average and the other a single plant. A
#: recommendation to switch supply on a 3 gCO2/kWh edge would be noise dressed
#: as a decision.
MATERIAL_DIFFERENCE_G_PER_KWH = 25.0


@dataclass(frozen=True)
class SourceOption:
    """One physically available supply option in one interval."""

    source_id: str
    kind: str
    available_kw: float
    carbon_g_per_kwh: float
    dispatchable: bool
    #: Must-run generation is taken whenever it exists: it is produced anyway,
    #: and refusing it curtails rather than saves.
    must_run: bool


@dataclass(frozen=True)
class IntervalAdvice:
    timestamp: datetime
    grid_carbon_g_per_kwh: float | None
    must_run_kw: float
    dispatchable_kw: float
    #: Dispatchable capacity that is cleaner than the grid right now.
    dispatchable_worth_running_kw: float
    recommendation: str
    reason: str
    #: gCO2 avoided per kWh by following the advice instead of always running
    #: the site's own dispatchable plant.
    carbon_saved_g_per_kwh: float = 0.0

    @property
    def usable_clean_kw(self) -> float:
        return self.must_run_kw + self.dispatchable_worth_running_kw


@dataclass(frozen=True)
class SupplyAdvice:
    intervals: list[IntervalAdvice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def intervals_own_plant_dirtier(self) -> int:
        return sum(1 for i in self.intervals if i.recommendation == "import")

    @property
    def dirtier_share(self) -> float | None:
        considered = [i for i in self.intervals if i.dispatchable_kw > 0
                      and i.grid_carbon_g_per_kwh is not None]
        if not considered:
            return None
        dirtier = sum(1 for i in considered if i.recommendation == "import")
        return dirtier / len(considered)

    def summary(self) -> str:
        if not self.intervals:
            return "No intervals to advise on."
        lines = [f"Advised on {len(self.intervals)} intervals."]
        share = self.dirtier_share
        if share is None:
            lines.append(
                "No dispatchable on-site generation was declared, so there is "
                "no run-or-import choice to make: must-run generation is "
                "always taken and the rest comes from the grid.")
        else:
            lines.append(
                f"The site's own dispatchable plant is dirtier than the grid "
                f"in {share:.0%} of the intervals where it could have run. "
                f"Running it only when it is cleaner is worth "
                f"{self.mean_saving:.0f} gCO2/kWh on average across those "
                f"intervals.")
        for note in self.notes:
            lines.append(f"Note: {note}")
        return "\n".join(lines)

    @property
    def mean_saving(self) -> float:
        relevant = [i for i in self.intervals if i.dispatchable_kw > 0]
        if not relevant:
            return 0.0
        return sum(i.carbon_saved_g_per_kwh for i in relevant) / len(relevant)


def advise_interval(timestamp: datetime, options: list[SourceOption],
                    grid_carbon_g_per_kwh: float | None) -> IntervalAdvice:
    """Decide what to use in one interval."""
    must_run_kw = sum(o.available_kw for o in options if o.must_run)
    dispatchable = [o for o in options if not o.must_run and o.available_kw > 0]
    dispatchable_kw = sum(o.available_kw for o in dispatchable)

    if not dispatchable:
        return IntervalAdvice(
            timestamp=timestamp,
            grid_carbon_g_per_kwh=grid_carbon_g_per_kwh,
            must_run_kw=must_run_kw, dispatchable_kw=0.0,
            dispatchable_worth_running_kw=0.0,
            recommendation="use_onsite" if must_run_kw > 0 else "import",
            reason=("must-run generation is produced anyway and is always "
                    "taken" if must_run_kw > 0
                    else "no on-site generation available in this interval"))

    if grid_carbon_g_per_kwh is None:
        # No grid signal is not a licence to assume the grid is dirty. Fail to
        # the status quo and say why, rather than recommending fuel burn on a
        # comparison that could not be made.
        return IntervalAdvice(
            timestamp=timestamp,
            grid_carbon_g_per_kwh=None,
            must_run_kw=must_run_kw, dispatchable_kw=dispatchable_kw,
            dispatchable_worth_running_kw=0.0,
            recommendation="unknown",
            reason="no grid carbon figure for this interval, so on-site and "
                   "grid supply cannot be compared")

    cleaner = [o for o in dispatchable
               if o.carbon_g_per_kwh
               < grid_carbon_g_per_kwh - MATERIAL_DIFFERENCE_G_PER_KWH]
    worth_running_kw = sum(o.available_kw for o in cleaner)

    # What following the advice avoids, against the naive policy of always
    # burning your own fuel because it is yours.
    naive = (sum(o.carbon_g_per_kwh * o.available_kw for o in dispatchable)
             / dispatchable_kw)
    advised = (
        sum(o.carbon_g_per_kwh * o.available_kw for o in cleaner)
        + grid_carbon_g_per_kwh * (dispatchable_kw - worth_running_kw)
    ) / dispatchable_kw
    saved = max(0.0, naive - advised)

    if worth_running_kw <= 0:
        recommendation = "import"
        reason = (f"every dispatchable source on site is dirtier than the grid "
                  f"at {grid_carbon_g_per_kwh:.0f} gCO2/kWh — importing is the "
                  f"cleaner option in this interval")
    elif worth_running_kw >= dispatchable_kw:
        recommendation = "use_onsite"
        reason = (f"on-site dispatchable generation is cleaner than the grid "
                  f"at {grid_carbon_g_per_kwh:.0f} gCO2/kWh")
    else:
        recommendation = "mixed"
        reason = (f"{worth_running_kw:.0f} kW of on-site dispatchable "
                  f"generation beats the grid at "
                  f"{grid_carbon_g_per_kwh:.0f} gCO2/kWh; the rest does not")

    return IntervalAdvice(
        timestamp=timestamp,
        grid_carbon_g_per_kwh=grid_carbon_g_per_kwh,
        must_run_kw=must_run_kw, dispatchable_kw=dispatchable_kw,
        dispatchable_worth_running_kw=worth_running_kw,
        recommendation=recommendation, reason=reason,
        carbon_saved_g_per_kwh=saved)


def advise(options_by_interval: list[tuple[datetime, list[SourceOption]]],
           grid_carbon: dict[datetime, float | None]) -> SupplyAdvice:
    """Run the run-or-import comparison across a horizon."""
    intervals = [
        advise_interval(stamp, options, grid_carbon.get(stamp))
        for stamp, options in options_by_interval
    ]

    notes = [
        "Grid carbon is a published average, not a marginal intensity. The "
        "marginal unit serving extra demand is normally dirtier than the "
        "average, so this comparison is biased in the grid's favour.",
        "The grid figure is balancing-area or regional; the on-site figure is "
        "one plant. This is the comparison an operator must make, but it is "
        "not like-for-like.",
    ]
    if any(i.recommendation == "unknown" for i in intervals):
        notes.append(
            "Some intervals had no grid carbon figure and were left "
            "unadvised rather than defaulted.")
    return SupplyAdvice(intervals=intervals, notes=notes)


def options_from_profile(profile, timestamps: list[datetime], weather=None,
                         plant_data=None
                         ) -> list[tuple[datetime, list[SourceOption]]]:
    """Build per-interval supply options from a declared site profile."""
    from core import generation, site_profile

    per_source = {}
    for source in profile.sources:
        if source.delivery_type == "contractual":
            continue  # an instrument moves no electrons
        served = site_profile.availability_kw(source, timestamps, weather,
                                             plant_data)
        loss = 1.0 - source.delivery_loss_percent / 100
        per_source[source.source_id] = (
            source, [value * source.confidence * loss for value in served])

    out = []
    for index, stamp in enumerate(timestamps):
        options = []
        for source, series in per_source.values():
            options.append(SourceOption(
                source_id=source.source_id,
                kind=source.kind,
                available_kw=series[index],
                carbon_g_per_kwh=source.carbon_g_per_kwh,
                dispatchable=source.dispatchable,
                must_run=source.kind in generation.MUST_RUN_VARIABLE_KINDS,
            ))
        out.append((stamp, options))
    return out
