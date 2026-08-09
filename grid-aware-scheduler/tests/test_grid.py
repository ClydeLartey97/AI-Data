"""
Offline tests for the scheduling algorithms and energy accounting.

No network. The point of these is that the scheduler's correctness can be
checked against hand-computable series — if a test here needs a live API, the
market coupling has leaked out of the adapter layer, which is the one thing
the architecture is supposed to prevent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.grid import (
    Job,
    PERIOD_HOURS,
    cheapest_window,
    cleanest_window,
    compare,
    energy_kwh,
    period_carbon_g,
    period_cost,
    run_immediately,
)

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def series(prices, carbons=None):
    carbons = carbons if carbons is not None else [100.0] * len(prices)
    return [
        GridDataPoint(
            timestamp=START + timedelta(minutes=30 * i),
            carbon_intensity=c,
            price=p,
        )
        for i, (p, c) in enumerate(zip(prices, carbons))
    ]


# --- accounting -----------------------------------------------------------

def test_energy_is_power_times_half_an_hour():
    assert energy_kwh(10.0) == 5.0
    assert energy_kwh(10.0, periods=4) == 20.0


def test_period_cost_converts_mwh_price_to_the_half_hour():
    # 10 kW for 30 min = 5 kWh; at GBP 100/MWh that is GBP 0.50.
    assert period_cost(10.0, 100.0) == pytest.approx(0.50)


def test_period_carbon_scales_with_intensity():
    # 5 kWh at 200 gCO2/kWh = 1000 g.
    assert period_carbon_g(10.0, 200.0) == pytest.approx(1000.0)


def test_job_energy_matches_its_duration():
    job = Job("j", power_kw=6.0, duration_periods=4, deadline_periods=8)
    assert job.energy_kwh == pytest.approx(6.0 * PERIOD_HOURS * 4)


# --- placement ------------------------------------------------------------

def test_baseline_always_starts_at_the_first_period():
    s = series([50, 10, 10, 10])
    p = run_immediately(s, Job("j", 10, 1, 4))
    assert p.start_index == 0
    assert p.start_time == START


def test_cheapest_window_finds_the_trough():
    s = series([100, 100, 5, 5, 100])
    p = cheapest_window(s, Job("j", 10, 2, 5))
    assert p.start_index == 2
    assert p.cost == pytest.approx(2 * period_cost(10, 5))


def test_cleanest_window_optimises_carbon_not_price():
    # Cheapest at index 0; cleanest at index 2. Each objective picks its own.
    s = series([1, 1, 90, 90], carbons=[500, 500, 20, 20])
    assert cheapest_window(s, Job("j", 10, 2, 4)).start_index == 0
    assert cleanest_window(s, Job("j", 10, 2, 4)).start_index == 2


def test_deadline_is_respected_even_when_later_is_cheaper():
    # The cheap window sits outside the deadline and must not be chosen.
    s = series([100, 100, 1, 1])
    p = cheapest_window(s, Job("j", 10, 2, deadline_periods=2))
    assert p.start_index == 0


def test_inflexible_jobs_run_immediately_however_cheap_later_is():
    s = series([100, 1, 1])
    job = Job("urgent", 10, 1, 3, flexible=False)
    assert cheapest_window(s, job).start_index == 0


def test_job_longer_than_its_deadline_falls_back_to_running_now():
    s = series([100, 1, 1, 1])
    p = cheapest_window(s, Job("j", 10, duration_periods=3, deadline_periods=2))
    assert p.start_index == 0


def test_windows_with_missing_prices_are_never_chosen():
    # Index 2-3 look free, but they are unpriced, not cheap.
    s = series([50, 50, None, None, 60, 60])
    p = cheapest_window(s, Job("j", 10, 2, 6))
    assert p.start_index == 0
    assert p.complete


def test_placement_reports_incompleteness_rather_than_hiding_it():
    s = series([None, None])
    assert run_immediately(s, Job("j", 10, 2, 2)).complete is False


# --- comparison -----------------------------------------------------------

def test_comparison_reports_savings_and_delay():
    s = series([100, 100, 10, 10])
    c = compare(s, Job("j", 10, 2, 4), objective="cost")
    assert c.scheduled.start_index == 2
    assert c.cost_saved == pytest.approx(c.baseline.cost - c.scheduled.cost)
    assert c.cost_saved_pct == pytest.approx(90.0)
    assert c.delay_periods == 2
    assert c.delay_hours == pytest.approx(1.0)


def test_no_saving_when_the_baseline_is_already_optimal():
    s = series([5, 100, 100])
    c = compare(s, Job("j", 10, 1, 3))
    assert c.cost_saved == pytest.approx(0.0)
    assert c.delay_periods == 0


def test_percentages_do_not_divide_by_zero_on_a_free_baseline():
    c = compare(series([0.0, 0.0]), Job("j", 10, 1, 2))
    assert c.cost_saved_pct == 0.0
