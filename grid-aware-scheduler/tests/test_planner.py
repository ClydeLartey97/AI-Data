from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from adapters.caiso import _parse_eia_mix, _parse_eia_rate, _parse_oasis_csv
from core.planner import PlanningCandidate, PlanningRequest, enumerate_options, optimise


def _series(prices, carbon):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [
        GridDataPoint(start + timedelta(minutes=30 * i), c, p)
        for i, (p, c) in enumerate(zip(prices, carbon))
    ]


def _candidate(key, prices, carbon, *, power=1.0, runtime=1.0, pue=1.0,
               memory=True, location="A"):
    return PlanningCandidate(
        key, key, "test", location, _series(prices, carbon), runtime, power,
        pue=pue, memory_ok=memory,
    )


def test_planner_jointly_chooses_hardware_location_and_time():
    inefficient_clean = _candidate(
        "slow-clean", [100, 100, 100, 100], [10, 10, 10, 10], power=4
    )
    efficient_dirty = _candidate(
        "fast-dirty", [100, 100, 1, 1], [200, 200, 100, 100], power=1
    )
    result = optimise(
        [inefficient_clean, efficient_dirty],
        PlanningRequest(2, cost_weight=0.8, carbon_weight=0.2),
    )
    assert result.selected.candidate.key == "fast-dirty"
    assert result.selected.start_index == 2
    assert result.selected.cost == pytest.approx(0.001)


def test_planner_enforces_memory_and_deadline():
    no_memory = _candidate("no-memory", [1] * 8, [1] * 8, memory=False)
    too_slow = _candidate("too-slow", [1] * 8, [1] * 8, runtime=5)
    valid = _candidate("valid", [1] * 8, [1] * 8, runtime=1)
    result = optimise([no_memory, too_slow, valid], PlanningRequest(2))
    assert result.selected.candidate.key == "valid"
    assert result.rejected == {
        "no-memory": "model does not fit in accelerator memory",
        "too-slow": "runtime exceeds deadline",
    }


def test_planner_applies_pue_to_energy_cost_and_carbon():
    candidate = _candidate("pue", [100, 100], [200, 200], power=10,
                           runtime=1, pue=1.4)
    option = optimise([candidate], PlanningRequest(1)).selected
    assert option.facility_energy_kwh == pytest.approx(14)
    assert option.cost == pytest.approx(1.4)
    assert option.carbon_kg == pytest.approx(2.8)


def test_missing_carbon_is_allowed_only_when_carbon_has_zero_weight():
    candidate = _candidate("price-only", [10, 1], [None, None], runtime=0.5)
    with pytest.raises(ValueError, match="no feasible plan"):
        optimise([candidate], PlanningRequest(1, cost_weight=1, carbon_weight=1))
    option = optimise(
        [candidate], PlanningRequest(1, cost_weight=1, carbon_weight=0)
    ).selected
    assert option.start_index == 1


def test_oasis_zip_parser_keeps_total_lmp_and_sorts_by_timestamp():
    csv_text = (
        "INTERVALSTARTTIME_GMT,NODE,LMP_TYPE,MW\n"
        "2026-08-01T01:00:00-00:00,TH_SP15_GEN-APND,LMP,22.5\n"
        "2026-08-01T00:00:00-00:00,TH_SP15_GEN-APND,LMP,10.0\n"
        "2026-08-01T00:00:00-00:00,TH_SP15_GEN-APND,MCC,99.0\n"
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("lmp.csv", csv_text)
    out = _parse_oasis_csv(raw.getvalue(), "TH_SP15_GEN-APND")
    assert list(out.values()) == [22.5, 10.0]  # parser preserves source order
    assert out[datetime(2026, 8, 1, tzinfo=timezone.utc)] == 10.0


def test_eia_mix_parser_returns_weighted_operational_intensity():
    payload = [{"data": [
        {"FUEL_TYPE_ID": "NG", "VALUES": {
            "DATES": ["08/01/2026 00:00:00"], "DATA": [100]}},
        {"FUEL_TYPE_ID": "SUN", "VALUES": {
            "DATES": ["08/01/2026 00:00:00"], "DATA": [100]}},
    ]}]
    out = _parse_eia_mix(payload)
    stamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert out[stamp] == pytest.approx(0.96 * 453.59237 / 2)


def test_eia_mix_excludes_unclassified_other_from_denominator():
    payload = [{"data": [
        {"FUEL_TYPE_ID": "NG", "VALUES": {
            "DATES": ["08/01/2026 00:00:00"], "DATA": [100]}},
        {"FUEL_TYPE_ID": "OTH", "VALUES": {
            "DATES": ["08/01/2026 00:00:00"], "DATA": [100]}},
    ]}]
    stamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert _parse_eia_mix(payload)[stamp] == pytest.approx(0.96 * 453.59237)


def test_eia_rate_converts_tonnes_per_mwh_to_grams_per_kwh():
    payload = [{"data": [{
        "TYPE_ID": "CO2.CER",
        "VALUES": {"DATES": ["08/01/2025 00:00:00"], "DATA": [0.2701572]},
    }]}]
    stamp = datetime(2025, 8, 1, tzinfo=timezone.utc)
    assert _parse_eia_rate(payload)[stamp] == pytest.approx(270.1572)


def test_planner_rejects_a_gap_inside_a_window():
    series = _series([50, 50, 50], [100, 100, 100])
    series[1].timestamp += timedelta(minutes=30)
    candidate = PlanningCandidate(
        "gap", "device", "GB", "London", series,
        runtime_hours=1.0, it_power_kw=1.0,
    )
    options, rejected = enumerate_options(
        [candidate], PlanningRequest(deadline_hours=1.5)
    )
    assert options == []
    assert rejected["gap"] == "no complete grid window before deadline"


def test_planner_refuses_to_compare_mixed_currencies():
    series = _series([50, 50], [100, 100])
    candidates = [
        PlanningCandidate("gb", "device", "GB", "London", series,
                          runtime_hours=0.5, it_power_kw=1.0, currency="GBP"),
        PlanningCandidate("us", "device", "CAISO", "SP15", series,
                          runtime_hours=0.5, it_power_kw=1.0, currency="USD"),
    ]
    with pytest.raises(ValueError, match="mixed currencies"):
        optimise(candidates, PlanningRequest(deadline_hours=1.0))


def test_pareto_frontier_excludes_equal_carbon_at_higher_cost():
    series = _series([10, 20], [100, 100])
    candidate = PlanningCandidate(
        "x", "device", "GB", "London", series,
        runtime_hours=0.5, it_power_kw=1.0,
    )
    result = optimise([candidate], PlanningRequest(deadline_hours=1.0))
    assert len(result.frontier) == 1
    assert result.frontier[0].start_index == 0


@pytest.mark.parametrize("field,value", [
    ("deadline_hours", float("nan")),
    ("cost_weight", float("inf")),
    ("carbon_weight", True),
])
def test_planning_request_rejects_non_finite_or_boolean_numbers(field, value):
    values = {"deadline_hours": 24, "cost_weight": 1,
              "carbon_weight": 1, "delay_weight": 0}
    values[field] = value
    with pytest.raises(ValueError, match="must be"):
        PlanningRequest(**values)


def test_hard_policy_caps_filter_before_weighted_ranking():
    series = _series([100, 10, 1], [10, 100, 200])
    candidate = PlanningCandidate(
        "x", "device", "GB", "London", series,
        runtime_hours=0.5, it_power_kw=100.0,
    )
    request = PlanningRequest(
        deadline_hours=1.5, cost_weight=0, carbon_weight=1,
        max_cost=0.6, max_delay_hours=1.0,
    )
    result = optimise([candidate], request)
    assert result.selected.start_index == 1
    assert result.selected.cost == pytest.approx(0.6)


def test_hard_policy_caps_fail_closed_when_no_window_qualifies():
    candidate = _candidate("x", [100, 100], [100, 100], runtime=0.5)
    with pytest.raises(ValueError, match="violate policy limits"):
        optimise([candidate], PlanningRequest(
            deadline_hours=1, cost_weight=1, carbon_weight=0,
            max_cost=0.001,
        ))
