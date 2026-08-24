"""Offline tests for the Renewables.ninja adapter and the generation check.

No network. Parsing runs against real captured API payloads; the comparison
maths runs against series built to isolate one failure each.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from adapters import renewables_ninja as ninja
from core.generation_check import compare

FIXTURES = Path(__file__).parent / "fixtures" / "renewables_ninja"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# --- parsing --------------------------------------------------------------

def test_parses_real_pv_payload_onto_the_utc_hourly_spine():
    points = ninja._to_points(_payload("pv"))
    assert len(points) == 8
    assert all(p.timestamp.tzinfo is timezone.utc for p in points)
    # Ordered, hourly, and ascending regardless of dict iteration order.
    gaps = {(b.timestamp - a.timestamp) for a, b in zip(points, points[1:])}
    assert gaps == {timedelta(hours=1)}


def test_electricity_at_unit_capacity_is_read_as_a_capacity_factor():
    wind = ninja._to_points(_payload("wind"))
    assert all(0.0 <= p.capacity_factor <= 1.0 for p in wind)
    # The first wind hour of the real capture.
    assert wind[0].capacity_factor == pytest.approx(0.494)


def test_rows_without_an_electricity_value_are_dropped_not_zeroed():
    """A missing hour is not a dark hour — zeroing it would invent an outage."""
    payload = _payload("pv")
    key = next(iter(payload["data"]))
    payload["data"][key] = {}
    assert len(ninja._to_points(payload)) == 7


def test_unparseable_timestamp_keys_are_skipped():
    payload = _payload("pv")
    payload["data"]["not-an-epoch"] = {"electricity": 0.5}
    assert len(ninja._to_points(payload)) == 8


# --- the refusal that keeps this honest -----------------------------------

def test_a_future_date_is_refused_rather_than_clamped():
    """The whole point: this is reanalysis, and must never look like a forecast."""
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    with pytest.raises(ninja.NinjaDateError, match="historical"):
        ninja._check_dates(tomorrow, tomorrow)


def test_recent_past_beyond_the_reanalysis_lag_is_refused():
    after = ninja.MERRA2_LATEST + timedelta(days=1)
    if after <= datetime.now(timezone.utc).date():
        with pytest.raises(ninja.NinjaDateError, match="arrears"):
            ninja._check_dates(after, after)


def test_reversed_and_oversized_ranges_are_refused():
    with pytest.raises(ninja.NinjaDateError):
        ninja._check_dates(date(2023, 6, 2), date(2023, 6, 1))
    with pytest.raises(ninja.NinjaDateError):
        ninja._check_dates(date(2020, 1, 1), date(2023, 1, 1))


def test_missing_token_fails_closed_and_names_the_variable(monkeypatch):
    monkeypatch.delenv(ninja.TOKEN_ENV_VAR, raising=False)
    with pytest.raises(ninja.NinjaAuthError, match=ninja.TOKEN_ENV_VAR):
        ninja.RenewablesNinjaClient()


# --- comparison: level and shape are separate failures --------------------

def _series(values, start=datetime(2023, 6, 1, tzinfo=timezone.utc)):
    return [(start + timedelta(hours=i), v) for i, v in enumerate(values)]


def test_a_constant_optimism_is_a_level_error_with_a_perfect_shape():
    """The distinction the module exists for: 1.5x high, timing untouched."""
    reference = _series([0.0, 0.2, 0.6, 0.4, 0.1])
    local = _series([v * 1.5 for _, v in reference])
    result = compare(local, reference, durations_hours=(1, 2))

    assert result.bias_ratio == pytest.approx(1.5)
    assert result.correlation == pytest.approx(1.0)
    assert result.peak_offset_hours == 0
    # Scaling every hour by the same factor cannot move an argmax.
    assert result.window_agreement_rate == 1.0
    assert result.mean_regret == pytest.approx(0.0)


def test_a_shifted_peak_is_a_shape_error_even_at_a_perfect_level():
    reference = _series([0.0, 0.1, 0.9, 0.1, 0.0])
    local = _series([0.0, 0.9, 0.1, 0.1, 0.0])
    result = compare(local, reference, durations_hours=(1,))

    assert result.local_mean == pytest.approx(result.reference_mean)
    assert result.bias_ratio == pytest.approx(1.0)  # level says it is perfect
    assert result.peak_offset_hours == -1
    assert result.window_agreement_rate == 0.0
    # And the decision cost is real: 0.9 available, 0.1 taken.
    assert result.mean_regret == pytest.approx(0.8)


def test_disagreement_without_cost_is_not_counted_as_regret():
    """Two windows can carry identical generation. A tie is not an error."""
    reference = _series([0.5, 0.1, 0.5, 0.1])
    local = _series([0.4, 0.1, 0.6, 0.1])
    result = compare(local, reference, durations_hours=(1,))

    assert result.window_agreement_rate == 0.0   # it picked the other peak
    assert result.mean_regret == pytest.approx(0.0)  # which cost nothing


def test_a_dark_reference_yields_no_bias_ratio_rather_than_a_division():
    reference = _series([0.0, 0.0, 0.0])
    local = _series([0.1, 0.2, 0.1])
    result = compare(local, reference)
    assert result.bias_ratio is None
    assert "no bias ratio is defined" in result.summary()


def test_a_flat_series_reports_no_correlation_rather_than_a_number():
    result = compare(_series([0.3, 0.3, 0.3]), _series([0.1, 0.5, 0.3]))
    assert result.correlation is None
    assert "Shape: undefined" in result.summary()


def test_only_overlapping_hours_are_compared():
    """A short reference narrows the comparison; it never pads with zeros."""
    local = _series([0.5] * 10)
    reference = _series([0.5] * 3)
    result = compare(local, reference)
    assert result.hours_compared == 3
    assert result.mean_absolute_error == pytest.approx(0.0)


def test_no_overlap_reports_nothing_compared():
    local = _series([0.5] * 3)
    reference = _series([0.5] * 3,
                        start=datetime(2024, 1, 1, tzinfo=timezone.utc))
    result = compare(local, reference)
    assert result.hours_compared == 0
    assert result.bias_ratio is None


def test_durations_longer_than_the_series_are_skipped_not_truncated():
    result = compare(_series([0.4, 0.5]), _series([0.4, 0.5]),
                     durations_hours=(1, 2, 24))
    assert [w.duration_hours for w in result.windows] == [1, 2]


# --- separating model error from curtailment ------------------------------

def test_negative_metered_output_is_clamped_not_treated_as_negative_supply():
    """A wind farm drawing station load at night meters slightly negative."""
    from core.generation_check import _clamp
    assert _clamp(-0.006) == 0.0
    assert _clamp(1.4) == 1.0
    assert _clamp(0.5) == 0.5


def test_plant_validation_reports_curtailment_as_a_share_of_the_plan():
    from core.generation_check import PlantValidation, compare
    empty = compare([], [])
    result = PlantValidation("T_HOWAO-1", date(2026, 8, 14), empty, empty,
                             planned_mwh=1437.0, curtailed_mwh=823.0)
    assert result.curtailed_share == pytest.approx(823 / 1437)
    assert "57%" in result.summary()


def test_a_plant_that_planned_nothing_has_no_curtailment_share():
    """Dividing by a zero plan would present as 'never curtailed'."""
    from core.generation_check import PlantValidation, compare
    empty = compare([], [])
    result = PlantValidation("T_X", date(2026, 8, 14), empty, empty,
                             planned_mwh=0.0, curtailed_mwh=0.0)
    assert result.curtailed_share is None
    assert "curtailed" not in result.summary()


def test_the_summary_labels_which_half_is_the_model_score():
    """The whole point of the split: only one of the two scores is our fault."""
    from core.generation_check import PlantValidation, compare
    empty = compare([], [])
    text = PlantValidation("T_X", date(2026, 8, 14), empty, empty,
                           planned_mwh=10.0, curtailed_mwh=1.0).summary()
    assert "this is the model score" in text
    assert "curtailment, not model error" in text
