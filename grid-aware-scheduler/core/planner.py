"""Joint hardware, location and time planning.

The planner deliberately uses exhaustive enumeration rather than an opaque
model.  For each feasible hardware/location candidate it prices every legal
start window before the deadline, then ranks the complete set using explicit
cost, carbon and delay weights.  At the current problem size this is exact,
auditable and effectively instant.

Hardware efficiency changes IT energy.  PUE changes facility energy.  The
selected grid location changes the price and carbon applied to that energy.
The selected start changes both again.  Keeping those layers separate is what
allows a reviewer to reproduce every number on the result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from adapters.base_adapter import GridDataPoint

PERIOD_HOURS = 0.5


@dataclass(frozen=True)
class PlanningCandidate:
    key: str
    hardware: str
    market: str
    location: str
    series: list[GridDataPoint]
    runtime_hours: float
    it_power_kw: float
    pue: float = 1.2
    memory_ok: bool = True
    currency: str = "GBP"
    hardware_provenance: str = "SPEC"
    grid_provenance: str = "MEASURED"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.runtime_hours <= 0 or not math.isfinite(self.runtime_hours):
            raise ValueError("runtime_hours must be finite and positive")
        if self.it_power_kw < 0 or not math.isfinite(self.it_power_kw):
            raise ValueError("it_power_kw must be finite and non-negative")
        if self.pue < 1 or not math.isfinite(self.pue):
            raise ValueError("PUE must be finite and at least 1.0")

    @property
    def facility_power_kw(self) -> float:
        return self.it_power_kw * self.pue

    @property
    def facility_energy_kwh(self) -> float:
        return self.facility_power_kw * self.runtime_hours

    @property
    def duration_periods(self) -> int:
        return max(1, math.ceil(self.runtime_hours / PERIOD_HOURS))


@dataclass(frozen=True)
class PlanningRequest:
    deadline_hours: float
    cost_weight: float = 0.5
    carbon_weight: float = 0.5
    delay_weight: float = 0.0
    max_cost: float | None = None
    max_carbon_kg: float | None = None
    max_delay_hours: float | None = None

    def __post_init__(self) -> None:
        values = (self.deadline_hours, self.cost_weight,
                  self.carbon_weight, self.delay_weight)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               for value in values):
            raise ValueError("deadline and objective weights must be numbers")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("deadline and objective weights must be finite")
        if self.deadline_hours <= 0:
            raise ValueError("deadline_hours must be positive")
        if min(self.cost_weight, self.carbon_weight, self.delay_weight) < 0:
            raise ValueError("objective weights cannot be negative")
        if self.cost_weight + self.carbon_weight + self.delay_weight <= 0:
            raise ValueError("at least one objective weight must be positive")
        for name, limit in (
            ("max_cost", self.max_cost),
            ("max_carbon_kg", self.max_carbon_kg),
            ("max_delay_hours", self.max_delay_hours),
        ):
            if limit is None:
                continue
            if (isinstance(limit, bool) or not isinstance(limit, (int, float))
                    or not math.isfinite(limit) or limit < 0):
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass
class PlanOption:
    candidate: PlanningCandidate
    start_index: int
    start_time: datetime
    finish_time: datetime
    cost: float | None
    carbon_kg: float | None
    facility_energy_kwh: float
    delay_hours: float
    complete_price: bool
    complete_carbon: bool
    score: float = math.inf
    pareto: bool = False


@dataclass
class PlanResult:
    selected: PlanOption
    alternatives: list[PlanOption]
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def frontier(self) -> list[PlanOption]:
        return [x for x in self.alternatives if x.pareto]


def evaluate_window(candidate: PlanningCandidate, start: int) -> PlanOption:
    """Account for one exact candidate/start placement without optimising."""
    if start < 0 or start >= len(candidate.series):
        raise ValueError("start index is outside the grid series")
    remaining = candidate.runtime_hours
    cost = carbon_g = 0.0
    price_ok = carbon_ok = True
    rows = candidate.series[start:start + candidate.duration_periods]
    for point in rows:
        hours = min(PERIOD_HOURS, remaining)
        if hours <= 0:
            break
        kwh = candidate.facility_power_kw * hours
        if point.price is None:
            price_ok = False
        else:
            cost += kwh * point.price / 1000.0
        if point.carbon_intensity is None:
            carbon_ok = False
        else:
            carbon_g += kwh * point.carbon_intensity
        remaining -= hours

    contiguous = all(
        b.timestamp - a.timestamp == timedelta(hours=PERIOD_HOURS)
        for a, b in zip(rows, rows[1:])
    )
    complete_window = (
        remaining <= 1e-9
        and len(rows) == candidate.duration_periods
        and contiguous
    )
    price_ok = price_ok and complete_window
    carbon_ok = carbon_ok and complete_window
    stamp = candidate.series[start].timestamp
    origin = candidate.series[0].timestamp
    return PlanOption(
        candidate=candidate,
        start_index=start,
        start_time=stamp,
        finish_time=stamp + timedelta(hours=candidate.runtime_hours),
        cost=cost if price_ok else None,
        carbon_kg=carbon_g / 1000.0 if carbon_ok else None,
        facility_energy_kwh=candidate.facility_energy_kwh,
        delay_hours=(stamp - origin).total_seconds() / 3600.0,
        complete_price=price_ok,
        complete_carbon=carbon_ok,
    )


def enumerate_options(candidates: list[PlanningCandidate], request: PlanningRequest
                      ) -> tuple[list[PlanOption], dict[str, str]]:
    """Enumerate every legal placement and record why candidates failed."""
    options: list[PlanOption] = []
    rejected: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.memory_ok:
            rejected[candidate.key] = "model does not fit in accelerator memory"
            continue
        if candidate.runtime_hours > request.deadline_hours:
            rejected[candidate.key] = "runtime exceeds deadline"
            continue
        if not candidate.series:
            rejected[candidate.key] = "no grid signal"
            continue
        latest_finish = candidate.series[0].timestamp + timedelta(
            hours=request.deadline_hours
        )
        before = len(options)
        signal_feasible = 0
        policy_rejected = 0
        last = len(candidate.series) - candidate.duration_periods
        for start in range(max(0, last + 1)):
            option = evaluate_window(candidate, start)
            if option.finish_time > latest_finish:
                continue
            if ((request.cost_weight > 0 or request.max_cost is not None)
                    and not option.complete_price):
                continue
            if ((request.carbon_weight > 0 or request.max_carbon_kg is not None)
                    and not option.complete_carbon):
                continue
            signal_feasible += 1
            if request.max_cost is not None and option.cost > request.max_cost:
                policy_rejected += 1
                continue
            if (request.max_carbon_kg is not None
                    and option.carbon_kg > request.max_carbon_kg):
                policy_rejected += 1
                continue
            if (request.max_delay_hours is not None
                    and option.delay_hours > request.max_delay_hours):
                policy_rejected += 1
                continue
            options.append(option)
        if len(options) == before:
            rejected[candidate.key] = (
                "all complete windows violate policy limits"
                if signal_feasible and policy_rejected == signal_feasible
                else "no complete grid window before deadline"
            )
    return options, rejected


def _normalise(value: float, lo: float, hi: float) -> float:
    return 0.0 if hi <= lo else (value - lo) / (hi - lo)


def _mark_pareto(options: list[PlanOption]) -> None:
    """Mark cost/carbon options not dominated on both dimensions.

    Sorting by cost reduces this from a quadratic pairwise scan to
    ``O(n log n)``. Within one cost group, only the minimum-carbon entries can
    be non-dominated. A later group must beat the best carbon already seen at
    a lower cost, not merely equal it.
    """
    comparable = [o for o in options if o.cost is not None and o.carbon_kg is not None]
    for option in options:
        option.pareto = False

    ordered = sorted(comparable, key=lambda o: (o.cost, o.carbon_kg))
    best_carbon = math.inf
    index = 0
    while index < len(ordered):
        end = index + 1
        cost = ordered[index].cost
        while end < len(ordered) and ordered[end].cost == cost:
            end += 1
        group = ordered[index:end]
        group_min = min(o.carbon_kg for o in group)
        if group_min < best_carbon:
            for option in group:
                if option.carbon_kg == group_min:
                    option.pareto = True
        best_carbon = min(best_carbon, group_min)
        index = end


def optimise(candidates: list[PlanningCandidate], request: PlanningRequest) -> PlanResult:
    """Return the exact best option over the supplied discrete candidate set."""
    currencies = {candidate.currency for candidate in candidates}
    if len(currencies) > 1:
        raise ValueError(
            "cannot rank costs in mixed currencies without an explicit conversion"
        )
    options, rejected = enumerate_options(candidates, request)
    if not options:
        detail = "; ".join(f"{k}: {v}" for k, v in rejected.items())
        raise ValueError(f"no feasible plan{': ' + detail if detail else ''}")

    costs = [o.cost for o in options if o.cost is not None]
    carbons = [o.carbon_kg for o in options if o.carbon_kg is not None]
    delays = [o.delay_hours for o in options]
    c_lo, c_hi = (min(costs), max(costs)) if costs else (0.0, 0.0)
    g_lo, g_hi = (min(carbons), max(carbons)) if carbons else (0.0, 0.0)
    d_lo, d_hi = min(delays), max(delays)
    total_weight = request.cost_weight + request.carbon_weight + request.delay_weight

    for option in options:
        cost_term = _normalise(option.cost or 0.0, c_lo, c_hi)
        carbon_term = _normalise(option.carbon_kg or 0.0, g_lo, g_hi)
        delay_term = _normalise(option.delay_hours, d_lo, d_hi)
        option.score = (
            request.cost_weight * cost_term
            + request.carbon_weight * carbon_term
            + request.delay_weight * delay_term
        ) / total_weight

    _mark_pareto(options)
    options.sort(key=lambda o: (
        o.score,
        o.cost if o.cost is not None else math.inf,
        o.carbon_kg if o.carbon_kg is not None else math.inf,
        o.finish_time,
        o.candidate.key,
    ))
    return PlanResult(options[0], options, rejected)
