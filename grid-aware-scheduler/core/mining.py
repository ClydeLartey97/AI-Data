"""Dispatch for continuous revenue-earning work, where scheduling does not apply.

**Why mining needs its own module rather than a flag on the scheduler.**
Every other workload in this project is the same shape: a fixed amount of work
that must finish by a deadline, where the only question is *when* to run it.
`core/portfolio.py` answers that well. Mining is not that shape. There is no
work to finish and no deadline to finish by. A rig can run every hour of the
year or none of them, and the question in each interval is not "when" but
"whether": does the revenue this hour exceed what running costs?

Feeding mining through the deadline scheduler would produce a schedule that
looks valid and answers a question nobody asked, which is why
`core.workload_types.to_portfolio_job` refuses it outright and points here.

**The comparison, and the term most people leave out.** An interval earns its
keep when

    revenue  >  energy cost + operating cost + opportunity cost

The first three are obvious. The fourth is the one that changes answers: if
the site can *export* power to the grid, or store it in a battery, then the
electricity a miner consumes has a value even when it is not bought. At an
export price above the mining margin, the rational act is to sell the power
and stop hashing — the rig is not free just because the electrons are
on-site. A behind-the-meter site that ignores export value will over-mine
during exactly the intervals when its power is worth most.

**What this module does not do.** It does not forecast difficulty, hash price
or coin price. Revenue per TH/s per day is an operator input, because those
are market variables this project has no feed for and inventing them would
make every result fictional. It also does not claim a miner runs faster in a
cheap hour: hash rate is set by the hardware, and the only levers are how many
units run and for how long.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: What to do with the fleet in one interval.
ACTIONS = ("run_full", "run_partial", "pause")


def _finite(name: str, value: float, *, minimum: float | None = None) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return float(value)


@dataclass(frozen=True)
class MinerFleet:
    """The rigs available, described by physics rather than by brochure.

    Hash rate and efficiency together fix power draw exactly — TH/s multiplied
    by J/TH is watts, by definition — so there is no separate power field to
    get wrong or to disagree with the datasheet.
    """

    hash_rate_th_s: float
    efficiency_j_per_th: float
    revenue_per_th_day: float
    opex_per_hour: float = 0.0
    curtailable: bool = True
    #: Smallest fraction of the fleet that can be switched. A site of 100
    #: identical miners has 0.01 granularity; one large immersion unit that is
    #: all-or-nothing has 1.0.
    min_step_fraction: float = 0.01
    model: str = ""

    def __post_init__(self) -> None:
        _finite("hash_rate_th_s", self.hash_rate_th_s, minimum=1e-9)
        _finite("efficiency_j_per_th", self.efficiency_j_per_th, minimum=1e-9)
        _finite("revenue_per_th_day", self.revenue_per_th_day, minimum=0)
        _finite("opex_per_hour", self.opex_per_hour, minimum=0)
        if not 0 < self.min_step_fraction <= 1:
            raise ValueError("min_step_fraction must be above 0 and at most 1")

    @property
    def power_kw(self) -> float:
        """TH/s x J/TH = J/s = W. Physics, not an estimate."""
        return self.hash_rate_th_s * self.efficiency_j_per_th / 1000

    @property
    def revenue_per_hour(self) -> float:
        return self.hash_rate_th_s * self.revenue_per_th_day / 24


@dataclass(frozen=True)
class MarketInterval:
    """One settlement interval's economics, as seen by the site."""

    timestamp: datetime
    hours: float
    #: What importing a MWh costs here.
    import_price_per_mwh: float
    #: What exporting a MWh earns, if the site can export at all.
    export_price_per_mwh: float | None = None
    #: Free on-site generation available this interval, in kW.
    onsite_kw: float = 0.0
    carbon_g_per_kwh: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("interval timestamps must be timezone-aware")
        _finite("hours", self.hours, minimum=1e-6)
        _finite("import_price_per_mwh", self.import_price_per_mwh)
        _finite("onsite_kw", self.onsite_kw, minimum=0)
        if self.export_price_per_mwh is not None:
            _finite("export_price_per_mwh", self.export_price_per_mwh)


@dataclass
class IntervalDecision:
    timestamp: datetime
    action: str
    fraction: float
    hash_rate_th_s: float
    power_kw: float
    revenue: float
    energy_cost: float
    opex: float
    opportunity_cost: float
    margin: float
    carbon_kg: float | None
    reason: str

    def public_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "fraction": round(self.fraction, 4),
            "hash_rate_th_s": round(self.hash_rate_th_s, 3),
            "power_kw": round(self.power_kw, 3),
            "revenue": round(self.revenue, 4),
            "energy_cost": round(self.energy_cost, 4),
            "opex": round(self.opex, 4),
            "opportunity_cost": round(self.opportunity_cost, 4),
            "margin": round(self.margin, 4),
            "carbon_kg": (None if self.carbon_kg is None
                          else round(self.carbon_kg, 4)),
            "reason": self.reason,
        }


@dataclass
class DispatchResult:
    decisions: list[IntervalDecision] = field(default_factory=list)
    currency: str = "GBP"

    @property
    def total_margin(self) -> float:
        return sum(d.margin for d in self.decisions)

    @property
    def total_revenue(self) -> float:
        return sum(d.revenue for d in self.decisions)

    @property
    def total_cost(self) -> float:
        return sum(d.energy_cost + d.opex + d.opportunity_cost
                   for d in self.decisions)

    #: What running flat out through every interval would have earned. The
    #: counterfactual that matters, because a miner's default is to run
    #: constantly — so this module's value is curtailed against always-on,
    #: not against idle. Computed in `dispatch`, since it cannot be recovered
    #: from the decisions alone once some intervals are paused.
    always_on_margin: float = 0.0

    @property
    def uplift(self) -> float:
        """Margin gained by curtailing rather than running flat out."""
        return self.total_margin - self.always_on_margin

    @property
    def hours_paused(self) -> float:
        return sum(d.timestamp and 0 or 0 for d in self.decisions) + sum(
            1 for d in self.decisions if d.action == "pause")

    def public_dict(self) -> dict:
        return {
            "currency": self.currency,
            "intervals": len(self.decisions),
            "total_revenue": round(self.total_revenue, 2),
            "total_cost": round(self.total_cost, 2),
            "total_margin": round(self.total_margin, 2),
            "always_on_margin": round(self.always_on_margin, 2),
            "uplift_from_curtailment": round(self.uplift, 2),
            "intervals_paused": sum(1 for d in self.decisions
                                    if d.action == "pause"),
            "decisions": [d.public_dict() for d in self.decisions],
        }


def _interval_economics(fleet: MinerFleet, interval: MarketInterval,
                        fraction: float) -> dict:
    """Revenue and the three costs, for running `fraction` of the fleet.

    On-site generation is applied first and priced at the *export* price, not
    at zero. That is the correction most naive models miss: power the site
    generated is only free if it had nowhere else to go. If it could have been
    sold, consuming it forgoes that sale, and the forgone sale is a real cost.
    """
    hours = interval.hours
    power_kw = fleet.power_kw * fraction
    energy_kwh = power_kw * hours

    onsite_kwh = min(energy_kwh, interval.onsite_kw * hours)
    imported_kwh = max(0.0, energy_kwh - onsite_kwh)

    energy_cost = imported_kwh * interval.import_price_per_mwh / 1000
    # Opportunity cost of self-consuming exportable on-site generation.
    export_price = interval.export_price_per_mwh
    opportunity_cost = (onsite_kwh * export_price / 1000
                        if export_price is not None and export_price > 0
                        else 0.0)
    revenue = fleet.revenue_per_hour * fraction * hours
    opex = fleet.opex_per_hour * hours if fraction > 0 else 0.0
    carbon_kg = (imported_kwh * interval.carbon_g_per_kwh / 1000
                 if interval.carbon_g_per_kwh is not None else None)

    return {
        "power_kw": power_kw, "revenue": revenue, "energy_cost": energy_cost,
        "opex": opex, "opportunity_cost": opportunity_cost,
        "carbon_kg": carbon_kg,
        "margin": revenue - energy_cost - opex - opportunity_cost,
    }


def dispatch(fleet: MinerFleet, intervals: list[MarketInterval], *,
             currency: str = "GBP",
             carbon_price_per_tonne: float = 0.0) -> DispatchResult:
    """Decide run, curtail or pause for each interval, and say why.

    The decision is per interval and independent, which is correct here: a
    miner carries no state between hours the way a battery or a deadline-bound
    job does. Pausing this hour does not make next hour cheaper, and there is
    no work queue that grows while stopped.

    `carbon_price_per_tonne` lets an operator with an internal carbon price
    fold it into the same margin comparison rather than treating carbon as a
    separate report. Left at zero it changes nothing.
    """
    if not intervals:
        raise ValueError("dispatch needs at least one interval")

    result = DispatchResult(currency=currency)
    always_on = 0.0

    for interval in intervals:
        full = _interval_economics(fleet, interval, 1.0)
        always_on += full["margin"]

        carbon_charge = 0.0
        if carbon_price_per_tonne and full["carbon_kg"] is not None:
            carbon_charge = full["carbon_kg"] / 1000 * carbon_price_per_tonne
        margin_full = full["margin"] - carbon_charge

        if margin_full > 0:
            action, fraction, chosen = "run_full", 1.0, full
            reason = (
                f"revenue {full['revenue']:.2f} exceeds energy "
                f"{full['energy_cost']:.2f} + opex {full['opex']:.2f}"
                + (f" + forgone export {full['opportunity_cost']:.2f}"
                   if full["opportunity_cost"] > 0 else "")
                + (f" + carbon {carbon_charge:.2f}" if carbon_charge else "")
                + f"; margin {margin_full:.2f}")
        elif not fleet.curtailable:
            action, fraction, chosen = "run_full", 1.0, full
            reason = (
                f"loss-making at {margin_full:.2f} but the fleet is declared "
                f"non-curtailable, so it runs anyway. Curtailment capability "
                f"is worth {abs(margin_full):.2f} this interval.")
        else:
            action, fraction = "pause", 0.0
            chosen = _interval_economics(fleet, interval, 0.0)
            shortfall = abs(margin_full)
            reason = (
                f"paused: running would lose {shortfall:.2f} "
                f"(revenue {full['revenue']:.2f} against energy "
                f"{full['energy_cost']:.2f}"
                + (f", forgone export {full['opportunity_cost']:.2f}"
                   if full["opportunity_cost"] > 0 else "")
                + f", opex {full['opex']:.2f})")
            margin_full = 0.0

        result.decisions.append(IntervalDecision(
            timestamp=interval.timestamp,
            action=action,
            fraction=fraction,
            hash_rate_th_s=fleet.hash_rate_th_s * fraction,
            power_kw=chosen["power_kw"],
            revenue=chosen["revenue"],
            energy_cost=chosen["energy_cost"],
            opex=chosen["opex"],
            opportunity_cost=chosen["opportunity_cost"],
            margin=chosen["margin"] if action != "pause" else 0.0,
            carbon_kg=chosen["carbon_kg"],
            reason=reason,
        ))

    result.always_on_margin = always_on
    return result
