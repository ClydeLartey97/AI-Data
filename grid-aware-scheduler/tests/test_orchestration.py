"""The seven-step flow, and the claims it refuses to make."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core import objectives as obj
from core import orchestration as orch
from core import workload_types as wt

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
PRICES = [30, 25, 20, 18, 22, 40, 80, 150, 220, 120, 60, 35] * 2
CARBON = [120, 110, 100, 95, 105, 160, 240, 320, 380, 300, 200, 140] * 2


def _series(prices=None, carbon=None):
    prices = prices or PRICES
    carbon = carbon or CARBON
    return [GridDataPoint(timestamp=NOW + timedelta(hours=index),
                          price=price, carbon_intensity=intensity)
            for index, (price, intensity) in enumerate(zip(prices, carbon))]


def _facility(**overrides):
    defaults = dict(key="site-a", name="Site A", series=_series(),
                    grid_provenance="SIMULATED")
    defaults.update(overrides)
    return orch.FacilityOption(**defaults)


def _batch(hours=4.0, kw=40.0, window=24):
    return wt.build("b1", "Batch", wt.WorkloadType.BATCH, NOW,
                    deadline=NOW + timedelta(hours=window),
                    duration_hours=hours, power_kw=kw)


def test_the_flow_places_work_in_a_cheaper_window_than_running_immediately():
    result = orch.recommend(_batch(), [_facility()], "min_cost")
    assert result.chosen is not None
    assert result.chosen.cost <= result.immediate.cost
    assert result.cost_saved >= 0


def test_a_carbon_objective_can_choose_a_different_window_than_cost():
    """Measured on real GB data, cost-optimal and carbon-optimal disagree
    often. If they never did, one of the objectives would be pointless."""
    cheap = orch.recommend(_batch(), [_facility()], "min_cost").chosen
    clean = orch.recommend(_batch(), [_facility()], "min_carbon").chosen
    assert cheap.cost <= clean.cost
    assert clean.carbon_kg <= cheap.carbon_kg


def test_every_workload_type_flows_through_the_same_scheduler():
    """The point of the type layer: the scheduler never learns what the work
    is. Any non-continuous type reaches a placement."""
    for workload_type, attributes, extra in (
            (wt.WorkloadType.RENDERING,
             {"frame_count": 400, "seconds_per_frame": 36,
              "max_parallel_frames": 4}, {}),
            (wt.WorkloadType.HPC, {"core_hours": 128},
             {"resources": wt.ResourceRequest(cpu_cores=32)}),
            (wt.WorkloadType.DATA_PROCESSING,
             {"dataset_gb": 200, "throughput_gb_per_hour": 100}, {}),
            (wt.WorkloadType.AI_TRAINING, {"model_size_b": 7.0}, {}),
            (wt.WorkloadType.BATCH, {}, {}),
    ):
        spec = wt.build("w", "W", workload_type, NOW,
                        deadline=NOW + timedelta(hours=20),
                        duration_hours=extra.pop("duration_hours", 2.0),
                        power_kw=20.0, attributes=attributes, **extra)
        result = orch.recommend(spec, [_facility()], "balanced")
        assert result.chosen is not None, workload_type


def test_mining_is_routed_away_from_the_deadline_flow():
    spec = wt.build("m1", "Rig", wt.WorkloadType.MINING, NOW,
                    duration_hours=24,
                    attributes={"hash_rate_th_s": 100.0,
                                "efficiency_j_per_th": 21.5,
                                "revenue_per_th_day": 0.05})
    with pytest.raises(wt.WorkloadRefused, match="core.mining"):
        orch.recommend(spec, [_facility()])


def test_the_renewable_objective_refuses_a_site_with_no_generation():
    """Nothing to maximise. Falling back to grid carbon would answer a
    different question with a confident number."""
    with pytest.raises(obj.ObjectiveUnavailable, match="on-site generation"):
        orch.recommend(_batch(), [_facility()], "max_renewable")


def test_the_renewable_objective_is_accepted_once_a_source_is_declared():
    facility = _facility(energy_sources=("solar",))
    assert facility.has_onsite_generation
    with pytest.raises(obj.ObjectiveUnavailable, match="core.energy"):
        orch.recommend(_batch(), [facility], "max_renewable")


def test_a_window_with_a_missing_price_is_never_chosen():
    """A window with no data is not a cheap window."""
    prices = list(PRICES)
    prices[3] = None                       # would otherwise be the cheapest
    result = orch.recommend(_batch(hours=1.0), [_facility(series=_series(prices))],
                            "min_cost")
    assert result.chosen.start != NOW + timedelta(hours=3)


def test_work_that_cannot_fit_before_its_deadline_schedules_nothing():
    spec = _batch(hours=30.0, window=4)
    result = orch.recommend(spec, [_facility()], "balanced")
    assert result.chosen is None
    assert any("no complete window" in w for w in result.warnings)


def test_the_recommendation_always_carries_a_counterfactual():
    """A start time alone is unfalsifiable. The saving against running
    immediately is the checkable claim."""
    payload = orch.recommend(_batch(), [_facility()], "min_cost").public_dict()
    assert payload["immediate"] is not None
    assert payload["cost_saved"] is not None
    assert payload["cost_saved_percent"] is not None


def test_every_recommendation_explains_itself():
    result = orch.recommend(_batch(), [_facility()], "balanced")
    assert len(result.explanation) >= 4
    assert any("Objective" in line for line in result.explanation)
    assert any("Against running immediately" in line
               for line in result.explanation)


def test_deferral_never_claims_the_run_got_shorter():
    """Cheap electricity does not make hardware faster."""
    result = orch.recommend(_batch(), [_facility()], "min_cost")
    if result.chosen.delay_hours > 0:
        assert any("not shortened" in line or "does not make hardware faster"
                   in line for line in result.explanation)
    duration = (result.chosen.end - result.chosen.start).total_seconds() / 3600
    assert duration == pytest.approx(4.0)


def test_resource_headroom_is_reported_separately_from_price():
    """The only honest route to finishing sooner."""
    elastic = wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                       deadline=NOW + timedelta(hours=20),
                       duration_hours=2.0, power_kw=10.0,
                       resources=wt.ResourceRequest(
                           gpu_count=4, min_gpu_count=2, max_gpu_count=16))
    result = orch.recommend(elastic, [_facility()], "balanced")
    assert result.headroom_available is True
    assert any("capacity decision" in line for line in result.explanation)


def test_unmeasured_inputs_are_warned_about_not_hidden():
    result = orch.recommend(_batch(), [_facility()], "balanced")
    assert any("ESTIMATED" in w for w in result.warnings)
    assert any("not measured market data" in w for w in result.warnings)


def test_mixed_currencies_are_refused_rather_than_ranked():
    pounds = _facility(key="gb", currency="GBP")
    dollars = _facility(key="us", currency="USD")
    with pytest.raises(ValueError, match="unlike money"):
        orch.recommend(_batch(), [pounds, dollars], "min_cost")


def test_a_facility_with_no_market_data_is_skipped_with_a_warning():
    result = orch.recommend(_batch(),
                            [_facility(), _facility(key="empty", series=[])],
                            "balanced")
    assert result.chosen is not None
    assert any("no market data" in w for w in result.warnings)


def test_at_least_one_facility_is_required():
    with pytest.raises(ValueError, match="at least one facility"):
        orch.recommend(_batch(), [], "balanced")
