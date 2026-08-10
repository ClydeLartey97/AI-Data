from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.backtest import BacktestCandidate, backtest
from core.planner import PlanningCandidate, PlanningRequest


START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _series(prices, carbon=None):
    carbon = carbon or [100] * len(prices)
    return [
        GridDataPoint(START + timedelta(minutes=30 * i), c, p)
        for i, (p, c) in enumerate(zip(prices, carbon))
    ]


def _candidate(series):
    return PlanningCandidate(
        "device:site", "device", "GB", "London", series,
        runtime_hours=0.5, it_power_kw=10,
    )


def test_backtest_preserves_forecast_decision_and_scores_realised_regret():
    forecast = _candidate(_series([100, 10]))
    realised = _series([10, 100])
    result = backtest(
        [BacktestCandidate(forecast, realised)],
        PlanningRequest(1, cost_weight=1, carbon_weight=0),
        decided_at=START - timedelta(hours=1),
    )
    assert result.forecast_plan.selected.start_index == 1
    assert result.realised_selected.start_index == 1
    assert result.realised_oracle.start_index == 0
    assert result.cost_saved == pytest.approx(-0.54)
    assert result.cost_regret == pytest.approx(0.54)
    assert result.cost_forecast_error == pytest.approx(0.54)


def test_backtest_reports_carbon_forecast_error_and_regret():
    forecast = _candidate(_series([50, 50], [200, 50]))
    realised = _series([50, 50], [20, 100])
    result = backtest(
        [BacktestCandidate(forecast, realised)],
        PlanningRequest(1, cost_weight=0, carbon_weight=1),
        decided_at=START - timedelta(hours=1),
    )
    assert result.carbon_saved_kg == pytest.approx(-0.48)
    assert result.carbon_regret_kg == pytest.approx(0.48)


def test_backtest_fails_when_selected_realised_start_is_missing():
    forecast = _candidate(_series([100, 10]))
    realised = _series([10])
    with pytest.raises(ValueError, match="does not contain the selected start"):
        backtest(
            [BacktestCandidate(forecast, realised)],
            PlanningRequest(1, cost_weight=1, carbon_weight=0),
            decided_at=START,
        )
