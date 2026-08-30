"""Mining dispatch: whether to run, not when to run.

Every other workload here is "fixed work, fixed deadline, choose a window".
Mining is "no work, no deadline, choose whether this hour pays". These tests
lock that difference and the term most naive models omit — the opportunity
cost of self-consuming power that could have been sold.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.mining import (MarketInterval, MinerFleet, DispatchResult, dispatch)

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _fleet(**overrides) -> MinerFleet:
    defaults = dict(hash_rate_th_s=1000.0, efficiency_j_per_th=21.5,
                    revenue_per_th_day=0.055)
    defaults.update(overrides)
    return MinerFleet(**defaults)


def _interval(price, hours=1.0, offset=0, **overrides) -> MarketInterval:
    return MarketInterval(timestamp=NOW + timedelta(hours=offset), hours=hours,
                          import_price_per_mwh=price, **overrides)


# --- Power is derived, not declared ---

def test_fleet_power_is_hash_rate_times_efficiency():
    """1000 TH/s x 21.5 J/TH = 21,500 W = 21.5 kW. Physics, not an estimate."""
    assert _fleet().power_kw == pytest.approx(21.5)


def test_revenue_per_hour_is_the_daily_rate_over_twenty_four():
    assert _fleet().revenue_per_hour == pytest.approx(1000 * 0.055 / 24)


def test_a_zero_hash_rate_is_refused():
    with pytest.raises(ValueError):
        _fleet(hash_rate_th_s=0.0)


# --- The core decision ---

def test_a_cheap_hour_runs():
    result = dispatch(_fleet(), [_interval(20.0)])
    assert result.decisions[0].action == "run_full"
    assert result.total_margin > 0


def test_an_expensive_hour_pauses():
    result = dispatch(_fleet(), [_interval(500.0)])
    assert result.decisions[0].action == "pause"
    assert result.decisions[0].margin == 0.0
    assert result.decisions[0].power_kw == 0.0


def test_the_breakeven_is_where_revenue_meets_cost():
    """Revenue 2.2917/h against 21.5 kWh, so breakeven is ~106.6 GBP/MWh."""
    assert dispatch(_fleet(), [_interval(100.0)]).decisions[0].action == "run_full"
    assert dispatch(_fleet(), [_interval(115.0)]).decisions[0].action == "pause"


def test_operating_cost_moves_the_breakeven_down():
    """Opex is a real cost and a model that omits it over-mines."""
    bare = dispatch(_fleet(), [_interval(100.0)]).decisions[0].action
    with_opex = dispatch(_fleet(opex_per_hour=1.0),
                         [_interval(100.0)]).decisions[0].action
    assert bare == "run_full"
    assert with_opex == "pause"


# --- The opportunity cost most models omit ---

def test_free_onsite_power_runs_when_there_is_nowhere_to_sell_it():
    result = dispatch(_fleet(), [_interval(0.0, onsite_kw=25.0)])
    assert result.decisions[0].action == "run_full"


def test_self_consuming_sellable_power_is_charged_the_export_price():
    """The correction that changes answers: on-site power is only free if it
    had nowhere else to go. At 200/MWh the forgone sale (4.30) beats the
    mining revenue (2.29), so selling wins."""
    result = dispatch(_fleet(), [_interval(0.0, onsite_kw=25.0,
                                           export_price_per_mwh=200.0)])
    assert result.decisions[0].action == "pause"
    assert "forgone export" in result.decisions[0].reason


def test_a_low_export_price_does_not_stop_mining():
    result = dispatch(_fleet(), [_interval(0.0, onsite_kw=25.0,
                                           export_price_per_mwh=10.0)])
    assert result.decisions[0].action == "run_full"
    assert result.decisions[0].opportunity_cost > 0


def test_onsite_generation_offsets_imported_energy():
    """Half the load from on-site means half the import bill."""
    full_import = dispatch(_fleet(), [_interval(100.0)]).decisions[0]
    half = dispatch(_fleet(), [_interval(100.0, onsite_kw=10.75)]).decisions[0]
    assert half.energy_cost == pytest.approx(full_import.energy_cost / 2, rel=0.02)


# --- Curtailment capability, and what it is worth ---

def test_a_non_curtailable_fleet_runs_at_a_loss_and_is_told_what_it_cost():
    result = dispatch(_fleet(curtailable=False), [_interval(500.0)])
    decision = result.decisions[0]
    assert decision.action == "run_full"
    assert decision.margin < 0
    assert "non-curtailable" in decision.reason


def test_curtailment_uplift_is_measured_against_always_on_not_against_idle():
    """A miner's default is to run constantly, so the counterfactual that
    matters is always-on, not a stopped rig."""
    prices = [20, 15, 12, 18, 35, 60, 95, 140, 210, 90, 45, 25]
    intervals = [_interval(price, offset=index)
                 for index, price in enumerate(prices)]
    result = dispatch(_fleet(opex_per_hour=0.5), intervals)
    assert result.always_on_margin < result.total_margin
    assert result.uplift == pytest.approx(
        result.total_margin - result.always_on_margin)
    assert any(d.action == "pause" for d in result.decisions)


def test_an_all_cheap_day_never_pauses_and_gains_nothing_from_curtailment():
    """Honest null result: with no expensive hours there is nothing to avoid."""
    intervals = [_interval(10.0, offset=i) for i in range(6)]
    result = dispatch(_fleet(), intervals)
    assert all(d.action == "run_full" for d in result.decisions)
    assert result.uplift == pytest.approx(0.0)


# --- Carbon as a price, folded into the same comparison ---

def test_an_internal_carbon_price_can_change_the_decision():
    interval = _interval(100.0, carbon_g_per_kwh=600)
    assert dispatch(_fleet(), [interval]).decisions[0].action == "run_full"
    priced = dispatch(_fleet(), [interval], carbon_price_per_tonne=200.0)
    assert priced.decisions[0].action == "pause"


def test_a_zero_carbon_price_changes_nothing():
    interval = _interval(50.0, carbon_g_per_kwh=600)
    plain = dispatch(_fleet(), [interval]).total_margin
    priced = dispatch(_fleet(), [interval], carbon_price_per_tonne=0.0)
    assert priced.total_margin == pytest.approx(plain)


# --- Contract ---

def test_dispatch_needs_at_least_one_interval():
    with pytest.raises(ValueError, match="at least one interval"):
        dispatch(_fleet(), [])


def test_every_decision_explains_itself():
    prices = [20, 500, 60]
    result = dispatch(_fleet(), [_interval(p, offset=i)
                                 for i, p in enumerate(prices)])
    assert all(d.reason for d in result.decisions)
    assert all(str(round(d.revenue, 2)) in d.reason or d.action == "pause"
               for d in result.decisions)


def test_the_result_serialises_with_its_counterfactual():
    payload = dispatch(_fleet(), [_interval(20.0)]).public_dict()
    assert {"total_margin", "always_on_margin", "uplift_from_curtailment",
            "intervals_paused", "decisions"} <= set(payload)


def test_a_naive_timestamp_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketInterval(timestamp=datetime(2026, 9, 1), hours=1.0,
                       import_price_per_mwh=20.0)
