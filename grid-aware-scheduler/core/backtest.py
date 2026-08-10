"""Score historical decisions against realised grid signals.

A credible savings claim must preserve the information boundary. The planner
chooses using a forecast snapshot captured before execution. This module then
replays the chosen hardware/start against realised signals and compares it
with both an immediate baseline and a perfect-hindsight oracle. The oracle is
reported only as regret and never feeds the scheduling decision.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from core.planner import (PlanOption, PlanResult, PlanningCandidate,
                          PlanningRequest, evaluate_window, optimise)


@dataclass(frozen=True)
class BacktestCandidate:
    forecast: PlanningCandidate
    realised_series: list


@dataclass
class BacktestResult:
    decided_at: datetime
    forecast_plan: PlanResult
    realised_selected: PlanOption
    realised_immediate: PlanOption
    realised_oracle: PlanOption

    @property
    def cost_saved(self) -> float | None:
        if self.realised_selected.cost is None or self.realised_immediate.cost is None:
            return None
        return self.realised_immediate.cost - self.realised_selected.cost

    @property
    def carbon_saved_kg(self) -> float | None:
        if (self.realised_selected.carbon_kg is None
                or self.realised_immediate.carbon_kg is None):
            return None
        return self.realised_immediate.carbon_kg - self.realised_selected.carbon_kg

    @property
    def cost_forecast_error(self) -> float | None:
        forecast = self.forecast_plan.selected.cost
        realised = self.realised_selected.cost
        return None if forecast is None or realised is None else realised - forecast

    @property
    def carbon_forecast_error_kg(self) -> float | None:
        forecast = self.forecast_plan.selected.carbon_kg
        realised = self.realised_selected.carbon_kg
        return None if forecast is None or realised is None else realised - forecast

    @property
    def cost_regret(self) -> float | None:
        if self.realised_selected.cost is None or self.realised_oracle.cost is None:
            return None
        return self.realised_selected.cost - self.realised_oracle.cost

    @property
    def carbon_regret_kg(self) -> float | None:
        if (self.realised_selected.carbon_kg is None
                or self.realised_oracle.carbon_kg is None):
            return None
        return self.realised_selected.carbon_kg - self.realised_oracle.carbon_kg


def _realised(candidate: BacktestCandidate) -> PlanningCandidate:
    return replace(candidate.forecast, series=candidate.realised_series,
                   grid_provenance="REALISED")


def _index_at(candidate: PlanningCandidate, timestamp: datetime) -> int:
    try:
        return next(
            index for index, point in enumerate(candidate.series)
            if point.timestamp == timestamp
        )
    except StopIteration as exc:
        raise ValueError("realised series does not contain the selected start") from exc


def backtest(candidates: list[BacktestCandidate], request: PlanningRequest,
             *, decided_at: datetime) -> BacktestResult:
    if not candidates:
        raise ValueError("backtest needs at least one candidate")
    forecast_plan = optimise([candidate.forecast for candidate in candidates], request)
    realised_candidates = [_realised(candidate) for candidate in candidates]
    by_key = {candidate.key: candidate for candidate in realised_candidates}
    if len(by_key) != len(realised_candidates):
        raise ValueError("backtest candidate keys must be unique")

    selected_key = forecast_plan.selected.candidate.key
    selected_candidate = by_key[selected_key]
    selected_index = _index_at(selected_candidate, forecast_plan.selected.start_time)
    realised_selected = evaluate_window(selected_candidate, selected_index)
    realised_immediate = evaluate_window(selected_candidate, 0)
    realised_oracle = optimise(realised_candidates, request).selected

    required_cost = request.cost_weight > 0
    required_carbon = request.carbon_weight > 0
    for name, option in (
        ("selected", realised_selected),
        ("immediate", realised_immediate),
        ("oracle", realised_oracle),
    ):
        if required_cost and not option.complete_price:
            raise ValueError(f"{name} realised window has incomplete price")
        if required_carbon and not option.complete_carbon:
            raise ValueError(f"{name} realised window has incomplete carbon")

    return BacktestResult(
        decided_at=decided_at,
        forecast_plan=forecast_plan,
        realised_selected=realised_selected,
        realised_immediate=realised_immediate,
        realised_oracle=realised_oracle,
    )
