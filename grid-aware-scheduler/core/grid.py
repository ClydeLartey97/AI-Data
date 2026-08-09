"""
Market-agnostic scheduling algorithms and energy accounting.

Nothing in this module knows which electricity market it is looking at. It
consumes the common ``GridDataPoint`` series any adapter produces, and that is
deliberate — it is the boundary that lets GB swap for CAISO or ERCOT as a
config change rather than a code change.

The accounting is the whole calculation, and it is small enough to state in
full. For a job drawing ``P`` kW across one settlement period (half an hour):

    energy_kWh  = P x 0.5
    cost        = energy_kWh x price_per_MWh / 1000
    carbon_g    = energy_kWh x carbon_intensity_gCO2_per_kWh

Choosing when to run a deadline-constrained flexible job is then a
sliding-window minimum over the periods the job could legally start in. There
is no solver and no model here on purpose: this is a constrained optimisation
with known structure, and a transparent classical algorithm is both auditable
and, at this size, effectively instant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from adapters.base_adapter import GridDataPoint

#: One settlement period is half an hour. This is the scheduling grain, and it
#: comes from the market data itself rather than being chosen by us.
PERIOD_HOURS = 0.5


@dataclass(frozen=True)
class Job:
    """A unit of work to place on the grid timeline.

    ``duration_periods`` and ``deadline_periods`` are both counted in
    settlement periods, so a 2-hour job that must finish within 12 hours is
    ``duration_periods=4, deadline_periods=24``.
    """

    name: str
    power_kw: float
    duration_periods: int
    deadline_periods: int
    flexible: bool = True

    @property
    def energy_kwh(self) -> float:
        return self.power_kw * PERIOD_HOURS * self.duration_periods


@dataclass(frozen=True)
class Placement:
    """Where an algorithm decided to run a job, and what that cost."""

    job: Job
    algorithm: str
    start_index: int
    start_time: datetime
    cost: float
    carbon_g: float
    complete: bool  # False when the series lacked data to price the whole run

    @property
    def carbon_kg(self) -> float:
        return self.carbon_g / 1000.0


def energy_kwh(power_kw: float, periods: int = 1) -> float:
    return power_kw * PERIOD_HOURS * periods


def period_cost(power_kw: float, price_per_mwh: float) -> float:
    return energy_kwh(power_kw) * price_per_mwh / 1000.0


def period_carbon_g(power_kw: float, intensity_g_per_kwh: float) -> float:
    return energy_kwh(power_kw) * intensity_g_per_kwh


def _window_totals(
    series: list[GridDataPoint], job: Job, start: int
) -> tuple[float, float, bool]:
    """Cost, carbon and completeness for running ``job`` from index ``start``."""
    cost = carbon = 0.0
    complete = True
    for point in series[start : start + job.duration_periods]:
        if point.price is None:
            complete = False
        else:
            cost += period_cost(job.power_kw, point.price)
        if point.carbon_intensity is None:
            complete = False
        else:
            carbon += period_carbon_g(job.power_kw, point.carbon_intensity)
    return cost, carbon, complete


def _latest_legal_start(series: list[GridDataPoint], job: Job) -> int:
    """Last index the job can start at and still finish by its deadline."""
    horizon = min(job.deadline_periods, len(series))
    return horizon - job.duration_periods


def _place(series: list[GridDataPoint], job: Job, algorithm: str, start: int) -> Placement:
    cost, carbon, complete = _window_totals(series, job, start)
    return Placement(
        job=job,
        algorithm=algorithm,
        start_index=start,
        start_time=series[start].timestamp,
        cost=cost,
        carbon_g=carbon,
        complete=complete,
    )


def run_immediately(series: list[GridDataPoint], job: Job) -> Placement:
    """Naive baseline: start now, ignore the grid entirely.

    This is what happens today on essentially every cluster, and it is the
    honest thing to measure against — not a strawman that deliberately picks
    the worst window.
    """
    return _place(series, job, "run immediately", 0)


def cheapest_window(series: list[GridDataPoint], job: Job) -> Placement:
    """Grid-aware on price: the cheapest legal start, by sliding-window minimum."""
    return _best(series, job, "cheapest window", key=lambda c, _: c)


def cleanest_window(series: list[GridDataPoint], job: Job) -> Placement:
    """Grid-aware on carbon: the lowest-emission legal start."""
    return _best(series, job, "cleanest window", key=lambda _, g: g)


def _best(series: list[GridDataPoint], job: Job, algorithm: str, key) -> Placement:
    if not job.flexible:
        return run_immediately(series, job)

    last = _latest_legal_start(series, job)
    if last < 0:
        # Deadline is shorter than the job — it cannot be shifted at all.
        return run_immediately(series, job)

    best_start, best_score = 0, None
    for start in range(last + 1):
        cost, carbon, complete = _window_totals(series, job, start)
        if not complete:
            continue  # never place a job on a window we cannot fully price
        score = key(cost, carbon)
        if best_score is None or score < best_score:
            best_start, best_score = start, score

    return _place(series, job, algorithm, best_start)


@dataclass(frozen=True)
class Comparison:
    """A baseline placement measured against a grid-aware one."""

    baseline: Placement
    scheduled: Placement

    @property
    def cost_saved(self) -> float:
        return self.baseline.cost - self.scheduled.cost

    @property
    def carbon_saved_g(self) -> float:
        return self.baseline.carbon_g - self.scheduled.carbon_g

    @property
    def cost_saved_pct(self) -> float:
        return _pct(self.cost_saved, self.baseline.cost)

    @property
    def carbon_saved_pct(self) -> float:
        return _pct(self.carbon_saved_g, self.baseline.carbon_g)

    @property
    def delay_periods(self) -> int:
        return self.scheduled.start_index - self.baseline.start_index

    @property
    def delay_hours(self) -> float:
        return self.delay_periods * PERIOD_HOURS


def compare(series: list[GridDataPoint], job: Job, objective: str = "cost") -> Comparison:
    """Run the baseline and the grid-aware algorithm over the same series."""
    scheduler = cheapest_window if objective == "cost" else cleanest_window
    return Comparison(baseline=run_immediately(series, job), scheduled=scheduler(series, job))


def _pct(part: float, whole: float) -> float:
    return 0.0 if not whole else part / whole * 100.0
