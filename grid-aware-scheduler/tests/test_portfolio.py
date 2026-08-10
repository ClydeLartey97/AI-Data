from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.planner import PlanningCandidate
from core.portfolio import (PortfolioJob, PortfolioPolicy, SiteCapacity,
                            optimise_portfolio)

START = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _candidate(key: str, prices: list[float], carbons: list[float], *,
               power: float = 1.0, location: str = "London") -> PlanningCandidate:
    series = [
        GridDataPoint(
            timestamp=START + timedelta(minutes=30 * index),
            price=price,
            carbon_intensity=carbon,
        )
        for index, (price, carbon) in enumerate(zip(prices, carbons))
    ]
    return PlanningCandidate(
        key=key,
        hardware="measured-device",
        market="GB",
        location=location,
        series=series,
        runtime_hours=0.5,
        it_power_kw=power,
        pue=1.0,
        currency="GBP",
        hardware_provenance="MEASURED",
        grid_provenance="FORECAST",
    )


def _job(job_id: str, candidate: PlanningCandidate, *, utility: float = 1,
         mandatory: bool = True, work_amount: float = 100,
         work_unit: str = "tokens") -> PortfolioJob:
    return PortfolioJob(
        job_id=job_id,
        candidates=(candidate,),
        earliest_start=START,
        deadline=START + timedelta(hours=1),
        work_amount=work_amount,
        work_unit=work_unit,
        utility=utility,
        mandatory=mandatory,
    )


def _policy(**kwargs) -> PortfolioPolicy:
    return PortfolioPolicy(
        capacities=(SiteCapacity("GB", "London", 1.0),),
        **kwargs,
    )


def test_portfolio_spreads_mandatory_jobs_across_capacity_and_grid_time():
    signal = _candidate("device", [40, 50], [300, 50])
    result = optimise_portfolio([
        _job("a", signal),
        _job("b", signal),
    ], _policy())
    assert len(result.assignments) == 2
    assert {assignment.option.start_index for assignment in result.assignments} == {0, 1}
    assert result.total_carbon_kg == pytest.approx(0.175)
    assert result.total_cost == pytest.approx(0.045)
    assert result.completed_work == {"tokens": 200}
    assert result.exact is True


def test_carbon_budget_maximises_explicit_utility_without_equating_modalities():
    signal = _candidate("device", [10, 10], [100, 100])
    result = optimise_portfolio([
        _job("language", signal, utility=10, mandatory=False,
             work_amount=1_000, work_unit="tokens"),
        _job("vision", signal, utility=4, mandatory=False,
             work_amount=500, work_unit="images"),
        _job("speech", signal, utility=2, mandatory=False,
             work_amount=600, work_unit="audio_seconds"),
    ], _policy(max_total_carbon_kg=0.1))
    assert [item.job.job_id for item in result.assignments] == ["language", "vision"]
    assert result.unscheduled_job_ids == ["speech"]
    assert result.completed_utility == 14
    assert result.completed_work == {"tokens": 1_000, "images": 500}


def test_price_is_secondary_to_carbon_when_useful_work_is_equal():
    clean_expensive = _candidate("clean", [100], [50])
    dirty_cheap = _candidate("dirty", [1], [200])
    job = PortfolioJob(
        job_id="job",
        candidates=(dirty_cheap, clean_expensive),
        earliest_start=START,
        deadline=START + timedelta(minutes=30),
        work_amount=100,
        work_unit="tokens",
        utility=1,
    )
    result = optimise_portfolio([job], _policy())
    assert result.assignments[0].option.candidate.key == "clean"
    assert result.total_cost == pytest.approx(0.05)
    assert result.total_carbon_kg == pytest.approx(0.025)


def test_equal_carbon_uses_grid_price_to_break_tie():
    expensive = _candidate("expensive", [100], [50])
    cheap = _candidate("cheap", [10], [50])
    job = PortfolioJob(
        job_id="job",
        candidates=(expensive, cheap),
        earliest_start=START,
        deadline=START + timedelta(minutes=30),
        work_amount=100,
        work_unit="tokens",
        utility=1,
    )
    result = optimise_portfolio([job], _policy())
    assert result.assignments[0].option.candidate.key == "cheap"


def test_portfolio_requires_capacity_for_every_candidate_site():
    job = _job("job", _candidate("ny", [10], [50], location="New York"))
    with pytest.raises(ValueError, match="no facility capacity supplied"):
        optimise_portfolio([job], _policy())


def test_portfolio_fails_closed_above_exact_search_bound():
    signal = _candidate("device", [10, 10], [50, 50])
    jobs = [_job(f"job-{index}", signal, mandatory=False) for index in range(3)]
    with pytest.raises(ValueError, match="upper bound exceeds"):
        optimise_portfolio(jobs, _policy(max_search_combinations=20))


def test_workflow_stages_preserve_dependencies_while_using_grid_windows():
    signal = _candidate("device", [10, 100, 1, 50], [20, 300, 10, 200], power=0.4)
    prepare = PortfolioJob(
        **{**_job("prepare", signal, utility=1).__dict__,
           "deadline": START + timedelta(hours=2)}
    )
    execute = PortfolioJob(
        **{**_job("execute", signal, utility=4).__dict__,
           "depends_on": ("prepare",),
           "deadline": START + timedelta(hours=2)}
    )
    report = PortfolioJob(
        **{**_job("report", signal, utility=1).__dict__,
           "depends_on": ("execute",),
           "deadline": START + timedelta(hours=2)}
    )
    result = optimise_portfolio(
        [report, execute, prepare],
        PortfolioPolicy(capacities=(SiteCapacity("GB", "London", 2.0),)),
    )
    by_job = {item.job.job_id: item.option for item in result.assignments}
    assert by_job["execute"].start_time >= by_job["prepare"].finish_time
    assert by_job["report"].start_time >= by_job["execute"].finish_time


def test_workflow_rejects_unknown_or_cyclic_stage_dependencies():
    signal = _candidate("device", [10, 10], [50, 50])
    unknown = PortfolioJob(
        **{**_job("stage", signal).__dict__, "depends_on": ("missing",)}
    )
    with pytest.raises(ValueError, match="unknown dependencies"):
        optimise_portfolio([unknown], _policy())

    first = PortfolioJob(
        **{**_job("first", signal).__dict__, "depends_on": ("second",)}
    )
    second = PortfolioJob(
        **{**_job("second", signal).__dict__, "depends_on": ("first",)}
    )
    with pytest.raises(ValueError, match="contain a cycle"):
        optimise_portfolio([first, second], _policy())
