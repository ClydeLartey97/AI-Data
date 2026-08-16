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

from core.energy import (EnergyDispatchResult, SiteEnergyProfile,
                         dispatch_energy)
from core.evidence import WORK_UNITS
from core.planner import (PERIOD_HOURS, PlanOption, PlanningCandidate,
                          PlanningRequest, enumerate_options)

ENERGY_PRIORITIES = frozenset({"renewable", "carbon_free", "carbon", "cost"})


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
    """How much power the site can draw, and when.

    `max_facility_power_kw` is the site's absolute electrical ceiling — the
    interconnection or switchgear limit, which does not move.

    `power_profile_kw` is the ceiling that actually binds in each half hour,
    and it is the reason this class is not a single number. A site with
    on-site generation can run more accelerators at once while that
    generation is producing: at noon under 500 kW of solar the headroom is
    genuinely larger than it is at 03:00, so heavy work placed there finishes
    sooner rather than being throttled or queued behind a flat ceiling. An
    interval with no declared entry falls back to the absolute limit, and no
    entry may exceed it — on-site generation raises the usable ceiling toward
    the electrical limit, never through it.
    """

    market: str
    location: str
    max_facility_power_kw: float
    power_profile_kw: tuple[tuple[datetime, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.market.strip() or not self.location.strip():
            raise ValueError("capacity market and location are required")
        if (isinstance(self.max_facility_power_kw, bool)
                or not isinstance(self.max_facility_power_kw, (int, float))
                or not math.isfinite(self.max_facility_power_kw)
                or self.max_facility_power_kw <= 0):
            raise ValueError("site capacity must be finite and positive")
        stamps = [stamp for stamp, _ in self.power_profile_kw]
        if len(stamps) != len(set(stamps)):
            raise ValueError("site power profile timestamps must be unique")
        for stamp, value in self.power_profile_kw:
            if stamp.tzinfo is None:
                raise ValueError("site power profile needs aware timestamps")
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("site power profile values must be >= 0")
            if value > self.max_facility_power_kw + 1e-9:
                raise ValueError(
                    "a site power profile cannot exceed the facility's "
                    "absolute electrical limit")
        # The search consults this once per job per interval, so it is a dict
        # rather than a scan over the declared pairs.
        object.__setattr__(self, "_limits", dict(self.power_profile_kw))

    @property
    def key(self) -> tuple[str, str]:
        return self.market, self.location

    @property
    def peak_available_kw(self) -> float:
        """The most this site can ever draw across the declared horizon.

        Used to reject work that cannot run at any hour. Rejecting against
        the lowest interval instead would discard exactly the heavy jobs a
        varying ceiling exists to accommodate.
        """
        if not self.power_profile_kw:
            return self.max_facility_power_kw
        return max(value for _, value in self.power_profile_kw)

    def limit_at(self, timestamp: datetime) -> float:
        return getattr(self, "_limits", {}).get(
            timestamp, self.max_facility_power_kw)


@dataclass(frozen=True)
class PortfolioPolicy:
    capacities: tuple[SiteCapacity, ...]
    max_total_cost: float | None = None
    max_total_carbon_kg: float | None = None
    max_search_combinations: int = 1_000_000
    energy_profiles: tuple[SiteEnergyProfile, ...] = ()
    energy_priority: str = "renewable"

    def __post_init__(self) -> None:
        if not self.capacities:
            raise ValueError("at least one site capacity is required")
        keys = [capacity.key for capacity in self.capacities]
        if len(keys) != len(set(keys)):
            raise ValueError("site capacities must have unique market/location keys")
        profile_keys = [profile.key for profile in self.energy_profiles]
        if len(profile_keys) != len(set(profile_keys)):
            raise ValueError("site energy profiles must have unique market/location keys")
        if self.energy_profiles and set(profile_keys) != set(keys):
            raise ValueError(
                "energy profiles must cover every configured facility site"
            )
        if self.energy_priority not in ENERGY_PRIORITIES:
            raise ValueError(
                f"energy_priority must be one of {sorted(ENERGY_PRIORITIES)}"
            )
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
    energy_dispatches: tuple[EnergyDispatchResult, ...] = ()

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


def _occupied_slots(
    option: PlanOption,
    energy_profile: SiteEnergyProfile | None = None,
) -> list[tuple[str, str, datetime, float, float]]:
    candidate = option.candidate
    remaining = candidate.runtime_hours
    slots: list[tuple[str, str, datetime, float, float]] = []
    for point in candidate.series[
        option.start_index:option.start_index + candidate.duration_periods
    ]:
        if remaining <= 1e-9:
            break
        pue = candidate.pue
        if energy_profile is not None:
            interval = energy_profile.facility_at(point.timestamp)
            if interval.pue is not None:
                pue = interval.pue
        hours = min(PERIOD_HOURS, remaining)
        slots.append((
            candidate.market,
            candidate.location,
            point.timestamp,
            candidate.it_power_kw * pue,
            hours,
        ))
        remaining -= hours
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

    capacity = {item.key: item for item in policy.capacities}
    energy_profiles = {profile.key: profile for profile in policy.energy_profiles}
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
        # Filter against the site's best interval, not a flat number: a job
        # that only fits while on-site generation is running is precisely
        # what a varying ceiling is for, and must survive to be placed there.
        windowed = options
        options = [option for option in options if (
            (option.candidate.market, option.candidate.location) in energy_profiles
            or option.candidate.facility_power_kw
            <= capacity[(option.candidate.market,
                         option.candidate.location)].peak_available_kw + 1e-9
        )]
        if not options:
            if windowed:
                # Windows existed and every one drew more than the site can
                # supply even at its best interval. Reporting a data gap here
                # would send an operator to look at the wrong thing.
                headroom = max(
                    capacity[(option.candidate.market,
                              option.candidate.location)].peak_available_kw
                    for option in windowed)
                drawn = min(option.candidate.facility_power_kw
                            for option in windowed)
                reason = (
                    f"needs {drawn:g} kW but the site supplies at most "
                    f"{headroom:g} kW in any interval")
            else:
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
    best_dispatches: tuple[EnergyDispatchResult, ...] = ()
    best_key: tuple | None = None
    considered = 0

    def recurse(index: int, utility: float, cost: float, carbon: float,
                energy: float, delay: float) -> None:
        nonlocal best_assignments, best_dispatches, best_key, considered
        if best_key is not None and utility + remaining_utility[index] < -best_key[0] - 1e-12:
            return
        if index == len(options_by_job):
            considered += 1
            evaluated_cost = cost
            evaluated_carbon = carbon
            evaluated_carbon_free = 0.0
            evaluated_renewable = 0.0
            evaluated_curtailment = 0.0
            evaluated_energy = energy
            evaluated_dispatches: tuple[EnergyDispatchResult, ...] = ()
            if energy_profiles:
                loads_by_site: dict[
                    tuple[str, str], dict[str, dict[datetime, float]]
                ] = {key: {} for key in energy_profiles}
                for assignment in selected:
                    option = assignment.option
                    site_key = (
                        option.candidate.market, option.candidate.location,
                    )
                    loads = loads_by_site[site_key].setdefault(
                        assignment.job.job_id, {}
                    )
                    for _, _, timestamp, power, hours in _occupied_slots(
                        option, energy_profiles[site_key],
                    ):
                        average_power = power * hours / PERIOD_HOURS
                        loads[timestamp] = (
                            loads.get(timestamp, 0.0) + average_power
                        )
                dispatches = tuple(
                    dispatch_energy(profile, loads_by_site[site_key])
                    for site_key, profile in sorted(energy_profiles.items())
                )
                if not all(dispatch.feasible for dispatch in dispatches):
                    return
                evaluated_cost = sum(
                    dispatch.ai_cost for dispatch in dispatches
                )
                evaluated_carbon = sum(
                    dispatch.ai_carbon_kg for dispatch in dispatches
                )
                evaluated_carbon_free = sum(
                    dispatch.ai_carbon_free_kwh for dispatch in dispatches
                )
                evaluated_renewable = sum(
                    dispatch.ai_renewable_kwh for dispatch in dispatches
                )
                evaluated_curtailment = sum(
                    dispatch.curtailed_kwh for dispatch in dispatches
                )
                evaluated_energy = sum(
                    dispatch.ai_energy_kwh for dispatch in dispatches
                )
                if (policy.max_total_cost is not None
                        and evaluated_cost > policy.max_total_cost + 1e-12):
                    return
                if (policy.max_total_carbon_kg is not None
                        and evaluated_carbon
                        > policy.max_total_carbon_kg + 1e-12):
                    return
                evaluated_dispatches = dispatches
            signature = tuple(sorted(
                (assignment.job.job_id, assignment.option.start_time.isoformat(),
                 assignment.option.candidate.key)
                for assignment in selected
            ))
            renewable_match = (
                evaluated_renewable / evaluated_energy
                if evaluated_energy > 1e-12 else 0.0
            )
            carbon_free_match = (
                evaluated_carbon_free / evaluated_energy
                if evaluated_energy > 1e-12 else 0.0
            )
            energy_orders = {
                "renewable": (
                    -renewable_match, -carbon_free_match,
                    evaluated_carbon, evaluated_cost,
                ),
                "carbon_free": (
                    -carbon_free_match, -renewable_match,
                    evaluated_carbon, evaluated_cost,
                ),
                "carbon": (
                    evaluated_carbon, -renewable_match,
                    -carbon_free_match, evaluated_cost,
                ),
                "cost": (
                    evaluated_cost, evaluated_carbon,
                    -renewable_match, -carbon_free_match,
                ),
            }
            key = (
                (
                    -utility,
                    *energy_orders[policy.energy_priority],
                    evaluated_energy,
                    evaluated_curtailment,
                    delay,
                    signature,
                )
                if energy_profiles else (
                    -utility, evaluated_carbon, evaluated_cost,
                    delay, signature,
                )
            )
            if best_key is None or key < best_key:
                best_key = key
                best_assignments = list(selected)
                best_dispatches = evaluated_dispatches
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
            next_cost = cost if energy_profiles else cost + option.cost
            next_carbon = carbon if energy_profiles else carbon + option.carbon_kg
            if not energy_profiles:
                if (policy.max_total_cost is not None
                        and next_cost > policy.max_total_cost + 1e-12):
                    continue
                if (policy.max_total_carbon_kg is not None
                        and next_carbon > policy.max_total_carbon_kg + 1e-12):
                    continue
            site_key = (option.candidate.market, option.candidate.location)
            energy_profile = energy_profiles.get(site_key)
            slots = _occupied_slots(option, energy_profile)
            if any(
                (energy_profile.facility_at(timestamp).base_load_kw
                 if energy_profile is not None else 0.0)
                + usage.get((market, location, timestamp), 0.0) + power
                > capacity[(market, location)].limit_at(timestamp) + 1e-9
                for market, location, timestamp, power, _ in slots
            ):
                continue
            for market, location, timestamp, power, _ in slots:
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
                energy + sum(
                    power * hours
                    for _, _, _, power, hours in slots
                ),
                delay + option.delay_hours,
            )
            selected_by_job.pop(job.job_id)
            selected.pop()
            for market, location, timestamp, power, _ in slots:
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
    if best_dispatches:
        total_energy = sum(
            dispatch.ai_energy_kwh for dispatch in best_dispatches
        )
        total_cost = sum(dispatch.ai_cost for dispatch in best_dispatches)
        total_carbon = sum(
            dispatch.ai_carbon_kg for dispatch in best_dispatches
        )
    else:
        total_energy = sum(
            item.option.facility_energy_kwh for item in best_assignments
        )
        total_cost = sum(item.option.cost for item in best_assignments)
        total_carbon = sum(item.option.carbon_kg for item in best_assignments)
    return PortfolioResult(
        assignments=best_assignments,
        unscheduled_job_ids=sorted(set(job_ids) - assigned_ids),
        completed_utility=sum(item.job.utility for item in best_assignments),
        completed_work=completed_work,
        total_energy_kwh=total_energy,
        total_cost=total_cost,
        total_carbon_kg=total_carbon,
        total_delay_hours=sum(item.option.delay_hours for item in best_assignments),
        combinations_considered=considered,
        search_space_upper_bound=search_bound,
        rejected=rejected,
        energy_dispatches=best_dispatches,
    )


def schedule_earliest(jobs: list[PortfolioJob],
                      policy: PortfolioPolicy) -> PortfolioResult:
    """Build a deterministic earliest-feasible operational counterfactual.

    This baseline respects quality-qualified variants, workflow dependencies,
    deadlines, fixed facility demand and power capacity. It deliberately does
    not optimise energy outcomes or apply cost/carbon caps. Its purpose is to
    answer what would have happened if the same admitted work were started as
    soon as the declared constraints allowed.
    """
    if not jobs:
        raise ValueError("portfolio needs at least one job")
    job_ids = [job.job_id for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError("portfolio job IDs must be unique")
    ordered_jobs = _dependency_order(jobs)
    capacity = {item.key: item for item in policy.capacities}
    energy_profiles = {profile.key: profile for profile in policy.energy_profiles}
    usage: dict[tuple[str, str, datetime], float] = {}
    assignments: list[PortfolioAssignment] = []
    by_job: dict[str, PortfolioAssignment] = {}
    rejected: dict[str, str] = {}

    for job in ordered_jobs:
        options, option_rejections = _job_options(job)
        missing_dependencies = [
            dependency for dependency in job.depends_on
            if dependency not in by_job
        ]
        if missing_dependencies:
            reason = (
                "dependency was not scheduled: "
                + ", ".join(missing_dependencies)
            )
            rejected[job.job_id] = reason
            if job.mandatory:
                raise ValueError(
                    f"mandatory job {job.job_id!r} is infeasible in earliest baseline: "
                    f"{reason}"
                )
            continue
        dependency_finish = (
            max(by_job[dependency].option.finish_time
                for dependency in job.depends_on)
            if job.depends_on else None
        )
        options = sorted(options, key=lambda option: (
            option.start_time, option.finish_time,
            option.candidate.key, option.carbon_kg, option.cost,
        ))
        selected_option = None
        for option in options:
            if (dependency_finish is not None
                    and option.start_time < dependency_finish):
                continue
            site_key = option.candidate.market, option.candidate.location
            if site_key not in capacity:
                raise ValueError(
                    f"no facility capacity supplied for {site_key[0]}/{site_key[1]}"
                )
            profile = energy_profiles.get(site_key)
            slots = _occupied_slots(option, profile)
            if any(
                (profile.facility_at(timestamp).base_load_kw if profile else 0.0)
                + usage.get((market, location, timestamp), 0.0) + power
                > capacity[(market, location)].limit_at(timestamp) + 1e-9
                for market, location, timestamp, power, _ in slots
            ):
                continue
            selected_option = option
            for market, location, timestamp, power, _ in slots:
                key = market, location, timestamp
                usage[key] = usage.get(key, 0.0) + power
            break
        if selected_option is None:
            reason = next(iter(option_rejections.values()),
                          "no earliest capacity-feasible placement")
            rejected[job.job_id] = reason
            if job.mandatory:
                raise ValueError(
                    f"mandatory job {job.job_id!r} is infeasible in earliest baseline"
                )
            continue
        assignment = PortfolioAssignment(job, selected_option)
        assignments.append(assignment)
        by_job[job.job_id] = assignment

    dispatches: tuple[EnergyDispatchResult, ...] = ()
    if energy_profiles:
        loads_by_site: dict[
            tuple[str, str], dict[str, dict[datetime, float]]
        ] = {key: {} for key in energy_profiles}
        for assignment in assignments:
            option = assignment.option
            site_key = option.candidate.market, option.candidate.location
            loads = loads_by_site[site_key].setdefault(
                assignment.job.job_id, {}
            )
            for _, _, timestamp, power, hours in _occupied_slots(
                option, energy_profiles[site_key],
            ):
                loads[timestamp] = (
                    loads.get(timestamp, 0.0)
                    + power * hours / PERIOD_HOURS
                )
        dispatches = tuple(
            dispatch_energy(profile, loads_by_site[site_key])
            for site_key, profile in sorted(energy_profiles.items())
        )
        if not all(dispatch.feasible for dispatch in dispatches):
            raise ValueError("earliest baseline has unmet physical energy demand")

    assigned_ids = {assignment.job.job_id for assignment in assignments}
    completed_work: dict[str, float] = {}
    for assignment in assignments:
        completed_work[assignment.job.work_unit] = (
            completed_work.get(assignment.job.work_unit, 0.0)
            + assignment.job.work_amount
        )
    if dispatches:
        total_energy = sum(item.ai_energy_kwh for item in dispatches)
        total_cost = sum(item.ai_cost for item in dispatches)
        total_carbon = sum(item.ai_carbon_kg for item in dispatches)
    else:
        total_energy = sum(item.option.facility_energy_kwh for item in assignments)
        total_cost = sum(item.option.cost for item in assignments)
        total_carbon = sum(item.option.carbon_kg for item in assignments)
    assignments.sort(key=lambda assignment: (
        assignment.option.start_time, assignment.job.job_id,
    ))
    return PortfolioResult(
        assignments=assignments,
        unscheduled_job_ids=sorted(set(job_ids) - assigned_ids),
        completed_utility=sum(item.job.utility for item in assignments),
        completed_work=completed_work,
        total_energy_kwh=total_energy,
        total_cost=total_cost,
        total_carbon_kg=total_carbon,
        total_delay_hours=sum(item.option.delay_hours for item in assignments),
        combinations_considered=1,
        search_space_upper_bound=1,
        exact=False,
        rejected=rejected,
        energy_dispatches=dispatches,
    )
