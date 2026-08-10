"""Exact capacity-aware scheduling for a portfolio of AI workloads.

Single-job placement is necessary but insufficient for an operator.  This
module schedules multiple quality-qualified jobs without exceeding facility
power capacity.  It maximises explicit operator utility, then minimises
operational carbon, electricity cost and delay in that order.  Cost and carbon
budgets remain hard constraints, so neither can be hidden by a weighted score.

The pilot uses bounded exhaustive search for a reproducible exact answer.  It
fails closed when the unpruned search space exceeds the configured limit rather
than returning an unlabelled heuristic result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from core.evidence import WORK_UNITS
from core.planner import (PERIOD_HOURS, PlanOption, PlanningCandidate,
                          PlanningRequest, enumerate_options)


@dataclass(frozen=True)
class PortfolioJob:
    job_id: str
    candidates: tuple[PlanningCandidate, ...]
    earliest_start: datetime
    deadline: datetime
    work_amount: float
    work_unit: str
    utility: float
    mandatory: bool = True
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")
        if not self.candidates:
            raise ValueError("a portfolio job needs at least one candidate")
        if self.earliest_start.tzinfo is None or self.deadline.tzinfo is None:
            raise ValueError("job times must be timezone-aware")
        if self.deadline <= self.earliest_start:
            raise ValueError("deadline must be after earliest_start")
        if (isinstance(self.work_amount, bool)
                or not isinstance(self.work_amount, (int, float))
                or not math.isfinite(self.work_amount) or self.work_amount <= 0):
            raise ValueError("work_amount must be finite and positive")
        if self.work_unit not in WORK_UNITS:
            raise ValueError(f"work_unit must be one of {sorted(WORK_UNITS)}")
        if (isinstance(self.utility, bool)
                or not isinstance(self.utility, (int, float))
                or not math.isfinite(self.utility) or self.utility <= 0):
            raise ValueError("utility must be finite and positive")
        if any(not isinstance(job_id, str) or not job_id.strip()
               for job_id in self.depends_on):
            raise ValueError("depends_on must contain non-empty job IDs")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on cannot contain duplicate job IDs")
        if self.job_id in self.depends_on:
            raise ValueError("a job cannot depend on itself")


@dataclass(frozen=True)
class SiteCapacity:
    market: str
    location: str
    max_facility_power_kw: float

    def __post_init__(self) -> None:
        if not self.market.strip() or not self.location.strip():
            raise ValueError("capacity market and location are required")
        if (isinstance(self.max_facility_power_kw, bool)
                or not isinstance(self.max_facility_power_kw, (int, float))
                or not math.isfinite(self.max_facility_power_kw)
                or self.max_facility_power_kw <= 0):
            raise ValueError("site capacity must be finite and positive")

    @property
    def key(self) -> tuple[str, str]:
        return self.market, self.location


@dataclass(frozen=True)
class PortfolioPolicy:
    capacities: tuple[SiteCapacity, ...]
    max_total_cost: float | None = None
    max_total_carbon_kg: float | None = None
    max_search_combinations: int = 1_000_000

    def __post_init__(self) -> None:
        if not self.capacities:
            raise ValueError("at least one site capacity is required")
        keys = [capacity.key for capacity in self.capacities]
        if len(keys) != len(set(keys)):
            raise ValueError("site capacities must have unique market/location keys")
        for name, value in (
            ("max_total_cost", self.max_total_cost),
            ("max_total_carbon_kg", self.max_total_carbon_kg),
        ):
            if value is None:
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if (isinstance(self.max_search_combinations, bool)
                or not isinstance(self.max_search_combinations, int)
                or self.max_search_combinations <= 0):
            raise ValueError("max_search_combinations must be a positive integer")


@dataclass(frozen=True)
class PortfolioAssignment:
    job: PortfolioJob
    option: PlanOption


@dataclass
class PortfolioResult:
    assignments: list[PortfolioAssignment]
    unscheduled_job_ids: list[str]
    completed_utility: float
    completed_work: dict[str, float]
    total_energy_kwh: float
    total_cost: float
    total_carbon_kg: float
    total_delay_hours: float
    combinations_considered: int
    search_space_upper_bound: int
    exact: bool = True
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def carbon_productivity(self) -> float:
        if self.total_carbon_kg == 0:
            return math.inf
        return self.completed_utility / self.total_carbon_kg


def _job_options(job: PortfolioJob) -> tuple[list[PlanOption], dict[str, str]]:
    origins = [candidate.series[0].timestamp for candidate in job.candidates
               if candidate.series]
    if not origins:
        return [], {job.job_id: "no grid signal"}
    origin = min(origins)
    deadline_hours = (job.deadline - origin).total_seconds() / 3600
    if deadline_hours <= 0:
        return [], {job.job_id: "deadline is before the grid horizon"}
    options, rejected = enumerate_options(
        list(job.candidates),
        PlanningRequest(
            deadline_hours=deadline_hours,
            cost_weight=1,
            carbon_weight=1,
            delay_weight=0,
        ),
    )
    options = [
        option for option in options
        if option.start_time >= job.earliest_start
        and option.finish_time <= job.deadline
        and option.cost is not None
        and option.carbon_kg is not None
    ]
    options.sort(key=lambda option: (
        option.carbon_kg,
        option.cost,
        option.delay_hours,
        option.start_time,
        option.candidate.key,
    ))
    return options, rejected


def _occupied_slots(option: PlanOption) -> list[tuple[str, str, datetime, float]]:
    candidate = option.candidate
    remaining = candidate.runtime_hours
    slots: list[tuple[str, str, datetime, float]] = []
    for point in candidate.series[
        option.start_index:option.start_index + candidate.duration_periods
    ]:
        if remaining <= 1e-9:
            break
        slots.append((
            candidate.market,
            candidate.location,
            point.timestamp,
            candidate.facility_power_kw,
        ))
        remaining -= min(PERIOD_HOURS, remaining)
    return slots


def _dependency_order(jobs: list[PortfolioJob]) -> list[PortfolioJob]:
    """Return a deterministic topological order or reject an invalid DAG."""
    by_id = {job.job_id: job for job in jobs}
    for job in jobs:
        missing = sorted(set(job.depends_on) - set(by_id))
        if missing:
            raise ValueError(
                f"job {job.job_id!r} has unknown dependencies: {', '.join(missing)}"
            )
    indegree = {job.job_id: len(job.depends_on) for job in jobs}
    children: dict[str, list[str]] = {job.job_id: [] for job in jobs}
    for job in jobs:
        for dependency in job.depends_on:
            children[dependency].append(job.job_id)

    def order_key(job_id: str) -> tuple:
        job = by_id[job_id]
        return (not job.mandatory, -job.utility, job.deadline, job.job_id)

    ready = sorted(
        (job_id for job_id, degree in indegree.items() if degree == 0),
        key=order_key,
    )
    ordered: list[PortfolioJob] = []
    while ready:
        job_id = ready.pop(0)
        ordered.append(by_id[job_id])
        for child in sorted(children[job_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=order_key)
    if len(ordered) != len(jobs):
        raise ValueError("portfolio job dependencies contain a cycle")
    return ordered


def optimise_portfolio(jobs: list[PortfolioJob],
                       policy: PortfolioPolicy) -> PortfolioResult:
    """Return the exact capacity-feasible portfolio schedule."""
    if not jobs:
        raise ValueError("portfolio needs at least one job")
    job_ids = [job.job_id for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError("portfolio job IDs must be unique")
    ordered_jobs = _dependency_order(jobs)
    currencies = {
        candidate.currency for job in jobs for candidate in job.candidates
    }
    if len(currencies) > 1:
        raise ValueError("portfolio cannot rank mixed currencies")

    capacity = {item.key: item.max_facility_power_kw for item in policy.capacities}
    options_by_job: list[tuple[PortfolioJob, list[PlanOption]]] = []
    rejected: dict[str, str] = {}
    for job in ordered_jobs:
        options, option_rejections = _job_options(job)
        unknown_sites = {
            (option.candidate.market, option.candidate.location)
            for option in options
            if (option.candidate.market, option.candidate.location) not in capacity
        }
        if unknown_sites:
            sites = ", ".join(f"{market}/{location}"
                              for market, location in sorted(unknown_sites))
            raise ValueError(f"no facility capacity supplied for {sites}")
        options = [
            option for option in options
            if option.candidate.facility_power_kw
            <= capacity[(option.candidate.market, option.candidate.location)] + 1e-9
        ]
        if not options:
            reason = next(iter(option_rejections.values()),
                          "no complete price/carbon window inside the job window")
            rejected[job.job_id] = reason
            if job.mandatory:
                raise ValueError(f"mandatory job {job.job_id!r} is infeasible: {reason}")
        options_by_job.append((job, options))

    # Jobs are in a deterministic topological order. Within each dependency
    # frontier, mandatory and high-utility work is considered first.
    search_bound = 1
    for job, options in options_by_job:
        search_bound *= len(options) + (0 if job.mandatory else 1)
        if search_bound > policy.max_search_combinations:
            raise ValueError(
                "portfolio exact-search upper bound exceeds max_search_combinations"
            )

    remaining_utility = [0.0] * (len(options_by_job) + 1)
    for index in range(len(options_by_job) - 1, -1, -1):
        remaining_utility[index] = (
            remaining_utility[index + 1] + options_by_job[index][0].utility
        )

    usage: dict[tuple[str, str, datetime], float] = {}
    selected: list[PortfolioAssignment] = []
    selected_by_job: dict[str, PortfolioAssignment] = {}
    best_assignments: list[PortfolioAssignment] | None = None
    best_key: tuple | None = None
    considered = 0

    def recurse(index: int, utility: float, cost: float, carbon: float,
                energy: float, delay: float) -> None:
        nonlocal best_assignments, best_key, considered
        if best_key is not None and utility + remaining_utility[index] < -best_key[0] - 1e-12:
            return
        if index == len(options_by_job):
            considered += 1
            signature = tuple(sorted(
                (assignment.job.job_id, assignment.option.start_time.isoformat(),
                 assignment.option.candidate.key)
                for assignment in selected
            ))
            key = (-utility, carbon, cost, delay, signature)
            if best_key is None or key < best_key:
                best_key = key
                best_assignments = list(selected)
            return

        job, options = options_by_job[index]
        dependencies = [selected_by_job.get(job_id) for job_id in job.depends_on]
        dependencies_ready = all(item is not None for item in dependencies)
        earliest_dependency_finish = (
            max(item.option.finish_time for item in dependencies if item is not None)
            if dependencies else None
        )
        choices: list[PlanOption | None] = [
            option for option in options
            if dependencies_ready
            and (earliest_dependency_finish is None
                 or option.start_time >= earliest_dependency_finish)
        ]
        if not job.mandatory:
            choices.append(None)
        for option in choices:
            if option is None:
                recurse(index + 1, utility, cost, carbon, energy, delay)
                continue
            next_cost = cost + option.cost
            next_carbon = carbon + option.carbon_kg
            if (policy.max_total_cost is not None
                    and next_cost > policy.max_total_cost + 1e-12):
                continue
            if (policy.max_total_carbon_kg is not None
                    and next_carbon > policy.max_total_carbon_kg + 1e-12):
                continue
            slots = _occupied_slots(option)
            if any(
                usage.get((market, location, timestamp), 0.0) + power
                > capacity[(market, location)] + 1e-9
                for market, location, timestamp, power in slots
            ):
                continue
            for market, location, timestamp, power in slots:
                key = market, location, timestamp
                usage[key] = usage.get(key, 0.0) + power
            assignment = PortfolioAssignment(job, option)
            selected.append(assignment)
            selected_by_job[job.job_id] = assignment
            recurse(
                index + 1,
                utility + job.utility,
                next_cost,
                next_carbon,
                energy + option.facility_energy_kwh,
                delay + option.delay_hours,
            )
            selected_by_job.pop(job.job_id)
            selected.pop()
            for market, location, timestamp, power in slots:
                key = market, location, timestamp
                remaining = usage[key] - power
                if remaining <= 1e-12:
                    usage.pop(key)
                else:
                    usage[key] = remaining

    recurse(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    if best_assignments is None:
        raise ValueError("no capacity- and policy-feasible portfolio schedule")

    assigned_ids = {assignment.job.job_id for assignment in best_assignments}
    completed_work: dict[str, float] = {}
    for assignment in best_assignments:
        job = assignment.job
        completed_work[job.work_unit] = (
            completed_work.get(job.work_unit, 0.0) + job.work_amount
        )
    best_assignments.sort(key=lambda assignment: (
        assignment.option.start_time,
        assignment.job.job_id,
    ))
    return PortfolioResult(
        assignments=best_assignments,
        unscheduled_job_ids=sorted(set(job_ids) - assigned_ids),
        completed_utility=sum(item.job.utility for item in best_assignments),
        completed_work=completed_work,
        total_energy_kwh=sum(item.option.facility_energy_kwh
                             for item in best_assignments),
        total_cost=sum(item.option.cost for item in best_assignments),
        total_carbon_kg=sum(item.option.carbon_kg for item in best_assignments),
        total_delay_hours=sum(item.option.delay_hours for item in best_assignments),
        combinations_considered=considered,
        search_space_upper_bound=search_bound,
        rejected=rejected,
    )
