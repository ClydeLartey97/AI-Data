"""The seven-step flow, end to end, for any workload type.

Select a type, describe the work, pick a facility, pick an objective, get a
schedule with an explanation and a counterfactual. This module is the seam
that joins the new type layer to the scheduling engine that already existed;
it holds no scheduling logic of its own, which is deliberate — the exact
planner in `core/planner.py` stays the single place placement is decided.

**The counterfactual is the product, not the schedule.** A recommended start
time on its own is unfalsifiable. What an operator can check is the difference
between the recommendation and what they would have done anyway, which is run
immediately. Every result here therefore carries both, and the saving is the
gap between them — the same discipline `core/backtest.py` applies to scored
decisions.

**Two honesty rules are enforced here rather than described.**

Duration never varies with price. The same workload placed in a cheap window
and an expensive one takes exactly as long. Work finishes sooner only if more
hardware or power headroom is allocated, which is a capacity decision and is
reported separately as `headroom_available`.

Mining does not come through here. It has no deadline to schedule against, so
it is routed to `core.mining.dispatch` and the caller is told why.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core import objectives as objectives_module
from core import workload_types as wt


@dataclass(frozen=True)
class FacilityOption:
    """Where the work could run, and what the grid looks like there.

    `series` is whatever the caller has: live market data, a forecast, or
    operator-entered figures. Its provenance travels in `grid_provenance` so a
    schedule built on typed-in numbers can never present as one built on
    measured market data.
    """

    key: str
    name: str
    series: list
    market: str = "GB"
    location: str = "national"
    currency: str = "GBP"
    symbol: str = "£"
    hardware: str = "unspecified"
    pue: float = 1.2
    grid_provenance: str = "MEASURED"
    #: On-site sources declared at this facility, by kind. Used only to say
    #: whether the renewable objective can be served at all.
    energy_sources: tuple[str, ...] = ()

    @property
    def has_onsite_generation(self) -> bool:
        return any(kind not in ("grid",) for kind in self.energy_sources)


@dataclass
class IntervalCost:
    timestamp: datetime
    price_per_mwh: float | None
    carbon_g_per_kwh: float | None


@dataclass
class ScheduleOption:
    facility: str
    start: datetime
    end: datetime
    cost: float
    carbon_kg: float
    energy_kwh: float
    delay_hours: float
    complete: bool
    currency: str = "GBP"

    def public_dict(self) -> dict:
        return {
            "facility": self.facility,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "cost": round(self.cost, 4),
            "carbon_kg": round(self.carbon_kg, 4),
            "energy_kwh": round(self.energy_kwh, 3),
            "delay_hours": round(self.delay_hours, 3),
            "currency": self.currency,
        }


@dataclass
class Recommendation:
    workload: dict
    objective: str
    chosen: ScheduleOption | None
    immediate: ScheduleOption | None
    alternatives: list[ScheduleOption] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    headroom_available: bool = False

    @property
    def cost_saved(self) -> float | None:
        if self.chosen is None or self.immediate is None:
            return None
        return self.immediate.cost - self.chosen.cost

    @property
    def carbon_saved_kg(self) -> float | None:
        if self.chosen is None or self.immediate is None:
            return None
        return self.immediate.carbon_kg - self.chosen.carbon_kg

    def public_dict(self) -> dict:
        def percent(saved, base):
            if saved is None or not base:
                return None
            return round(100 * saved / base, 2)

        return {
            "workload": self.workload,
            "objective": self.objective,
            "chosen": self.chosen.public_dict() if self.chosen else None,
            "immediate": (self.immediate.public_dict()
                          if self.immediate else None),
            "cost_saved": (None if self.cost_saved is None
                           else round(self.cost_saved, 4)),
            "carbon_saved_kg": (None if self.carbon_saved_kg is None
                                else round(self.carbon_saved_kg, 4)),
            "cost_saved_percent": percent(
                self.cost_saved, self.immediate.cost if self.immediate else 0),
            "carbon_saved_percent": percent(
                self.carbon_saved_kg,
                self.immediate.carbon_kg if self.immediate else 0),
            "alternatives": [option.public_dict()
                             for option in self.alternatives[:5]],
            "explanation": list(self.explanation),
            "warnings": list(self.warnings),
            "headroom_available": self.headroom_available,
        }


def _interval_hours(series: list) -> float:
    """Spacing of the price series, in hours, from the data itself."""
    if len(series) < 2:
        return 0.5
    delta = (series[1].timestamp - series[0].timestamp).total_seconds() / 3600
    return delta if delta > 0 else 0.5


def _score_window(spec: wt.WorkloadSpec, facility: FacilityOption,
                  start_index: int, slots: int, step: float) -> ScheduleOption | None:
    """Cost and carbon for running the whole job from one starting slot.

    Returns None when any interval inside the run has no price. A window with
    missing data is not a cheap window — that rule already exists in
    `core/grid.py` and is repeated here rather than quietly assumed.
    """
    series = facility.series
    if start_index + slots > len(series):
        return None
    energy_per_slot = spec.power_kw * facility.pue * step
    cost = 0.0
    carbon_g = 0.0
    for point in series[start_index:start_index + slots]:
        price = getattr(point, "price", None)
        if price is None or not math.isfinite(price):
            return None
        cost += energy_per_slot * price / 1000
        intensity = getattr(point, "carbon_intensity", None)
        if intensity is not None and math.isfinite(intensity):
            carbon_g += energy_per_slot * intensity
    start = series[start_index].timestamp
    return ScheduleOption(
        facility=facility.key,
        start=start,
        end=start + timedelta(hours=spec.duration_hours),
        cost=cost,
        carbon_kg=carbon_g / 1000,
        energy_kwh=energy_per_slot * slots,
        delay_hours=(start - spec.earliest_start).total_seconds() / 3600,
        complete=True,
        currency=facility.currency,
    )


def recommend(spec: wt.WorkloadSpec, facilities: list[FacilityOption],
              objective: objectives_module.Objective | str = "balanced",
              *, custom_weights: objectives_module.ObjectiveWeights | None = None
              ) -> Recommendation:
    """Steps 5 to 7: place the work, cost it, and explain the choice.

    An exhaustive sweep of every legal starting slot at every facility. At the
    problem size this product handles — a few hundred half-hours across a
    handful of sites — exhaustive is exact and takes microseconds, so there is
    no reason to approximate.
    """
    if spec.continuous:
        raise wt.WorkloadRefused(
            f"{spec.definition.label} earns revenue continuously and has no "
            f"completion deadline, so there is no window to place it in. Use "
            f"core.mining.dispatch, which decides run-or-pause per interval.")
    if not facilities:
        raise ValueError("at least one facility is required")

    warnings: list[str] = []
    explanation: list[str] = []

    resolved = objectives_module.Objective(objective) if isinstance(
        objective, str) else objective
    if resolved is objectives_module.Objective.MAX_RENEWABLE and not any(
            f.has_onsite_generation for f in facilities):
        raise objectives_module.ObjectiveUnavailable(
            "no facility declares on-site generation, so there is no "
            "renewable output to maximise. Declare sources on the facility, "
            "or choose a grid-signal objective.")
    weights = objectives_module.resolve(resolved, custom_weights)

    candidates: list[ScheduleOption] = []
    immediate: ScheduleOption | None = None

    for facility in facilities:
        if not facility.series:
            warnings.append(f"{facility.name} has no market data; skipped.")
            continue
        step = _interval_hours(facility.series)
        slots = max(1, math.ceil(spec.duration_hours / step))
        first = _score_window(spec, facility, 0, slots, step)
        if first is not None and (immediate is None or first.cost < immediate.cost):
            immediate = first
        for index, point in enumerate(facility.series):
            if point.timestamp < spec.earliest_start:
                continue
            finish = point.timestamp + timedelta(hours=spec.duration_hours)
            if spec.deadline is not None and finish > spec.deadline:
                continue
            option = _score_window(spec, facility, index, slots, step)
            if option is not None:
                candidates.append(option)

    if not candidates:
        warnings.append(
            "no complete window exists between the earliest start and the "
            "deadline with unbroken price data. Nothing was scheduled.")
        return Recommendation(
            workload=spec.public_dict(), objective=resolved.value,
            chosen=None, immediate=immediate, explanation=explanation,
            warnings=warnings,
            headroom_available=spec.resources.scalable)

    currencies = {option.currency for option in candidates}
    if len(currencies) > 1:
        raise ValueError(
            f"candidates span {sorted(currencies)} and no conversion rate was "
            f"supplied; ranking them would compare unlike money")

    cost_span = max(o.cost for o in candidates) - min(o.cost for o in candidates)
    carbon_span = (max(o.carbon_kg for o in candidates)
                   - min(o.carbon_kg for o in candidates))
    delay_span = (max(o.delay_hours for o in candidates)
                  - min(o.delay_hours for o in candidates))

    def normalise(value, low, span):
        return 0.0 if span <= 0 else (value - low) / span

    low_cost = min(o.cost for o in candidates)
    low_carbon = min(o.carbon_kg for o in candidates)
    low_delay = min(o.delay_hours for o in candidates)

    def score(option: ScheduleOption) -> float:
        return (weights.cost_weight * normalise(option.cost, low_cost, cost_span)
                + weights.carbon_weight * normalise(option.carbon_kg, low_carbon,
                                                    carbon_span)
                + weights.delay_weight * normalise(option.delay_hours, low_delay,
                                                   delay_span))

    ranked = sorted(candidates, key=lambda o: (score(o), o.delay_hours))
    chosen = ranked[0]

    # Step 7: why this window.
    explanation.append(
        f"{spec.name} is {spec.duration_hours:.2f} h of "
        f"{spec.definition.label.lower()} drawing {spec.power_kw:.2f} kW, "
        f"so {chosen.energy_kwh:.1f} kWh at the meter.")
    explanation.append(
        f"Objective '{objectives_module.CATALOGUE[resolved].label}' weights "
        f"cost {weights.cost_weight:g}, carbon {weights.carbon_weight:g}, "
        f"delay {weights.delay_weight:g}.")
    explanation.append(
        f"Searched {len(candidates)} legal start times across "
        f"{len({o.facility for o in candidates})} facilit"
        f"{'y' if len({o.facility for o in candidates}) == 1 else 'ies'}; "
        f"chose {chosen.start:%Y-%m-%d %H:%M} at {chosen.facility}.")
    if chosen.delay_hours > 0:
        explanation.append(
            f"That defers the start by {chosen.delay_hours:.2f} h, spending "
            f"slack the deadline already allowed. The run itself is not "
            f"shortened — cheaper electricity does not make hardware faster.")
    else:
        explanation.append("The best window is the earliest one, so nothing "
                           "is deferred.")
    if immediate is not None:
        explanation.append(
            f"Against running immediately: cost "
            f"{immediate.cost:.2f} to {chosen.cost:.2f}, carbon "
            f"{immediate.carbon_kg:.2f} kg to {chosen.carbon_kg:.2f} kg.")

    if spec.resources.scalable:
        explanation.append(
            "This workload declares resource headroom, so allocating more "
            "hardware in the chosen window could genuinely shorten it. That "
            "is a capacity decision, not a consequence of price.")
    if spec.provenance != "MEASURED":
        warnings.append(
            f"duration and power are {spec.provenance}, not measured, so "
            f"every cost and carbon figure inherits that provenance.")
    if any(f.grid_provenance != "MEASURED" for f in facilities):
        warnings.append(
            "at least one facility's price or carbon series is not measured "
            "market data.")

    return Recommendation(
        workload=spec.public_dict(), objective=resolved.value, chosen=chosen,
        immediate=immediate, alternatives=ranked[1:6],
        explanation=explanation, warnings=warnings,
        headroom_available=spec.resources.scalable)
