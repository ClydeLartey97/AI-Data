"""
Offline tests for time-range bucketing.

The test that matters most is `test_extremes_survive_aggregation`. Downsampling
that keeps only the mean would still produce a chart that looks fine, so this
failure mode is invisible by inspection — it has to be asserted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.resample import (
    PERIOD,
    RANGES,
    auto_bucket,
    bucket_series,
    pick_range,
    prepare,
)

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def half_hours(values):
    return [(START + i * PERIOD, v) for i, v in enumerate(values)]


def test_extremes_survive_aggregation():
    # One hour of half-hours: a cheap one and an expensive one. Bucketed to an
    # hour, the mean alone would report 80 and hide both numbers that matter.
    buckets = bucket_series(half_hours([2.84, 158.94]), timedelta(hours=1))
    assert len(buckets) == 1
    b = buckets[0]
    assert b.low == pytest.approx(2.84)
    assert b.high == pytest.approx(158.94)
    assert b.mean == pytest.approx(80.89)
    assert b.count == 2


def test_missing_values_are_dropped_not_zero_filled():
    # A half-hour with no price is not a half-hour that was free.
    buckets = bucket_series(half_hours([10.0, None, 20.0, None]), timedelta(hours=2))
    assert len(buckets) == 1
    assert buckets[0].count == 2
    assert buckets[0].low == pytest.approx(10.0)


def test_a_fully_empty_series_yields_no_buckets():
    assert bucket_series(half_hours([None, None]), timedelta(hours=1)) == []


def test_buckets_are_ordered_and_aligned_to_boundaries():
    buckets = bucket_series(half_hours([1, 2, 3, 4, 5, 6]), timedelta(hours=1))
    assert [b.start for b in buckets] == sorted(b.start for b in buckets)
    assert all(b.start.minute == 0 for b in buckets)


def test_native_resolution_reports_single_counts():
    # At 30-minute buckets nothing is aggregated, so the chart must not draw
    # a min-max band implying a spread that does not exist.
    buckets = bucket_series(half_hours([5, 9, 4]), PERIOD)
    assert all(b.count == 1 for b in buckets)
    assert all(b.low == b.high == b.mean for b in buckets)


def test_prepare_clips_to_the_requested_range():
    points = half_hours([1.0] * (48 * 10))          # 10 days
    buckets, rng = prepare(points, "1D")
    assert rng.key == "1D"
    span = buckets[-1].start - buckets[0].start
    assert span <= timedelta(days=1)


def test_prepare_on_empty_input_is_not_an_error():
    buckets, rng = prepare([], "1W")
    assert buckets == [] and rng.key == "1W"


def test_unknown_range_key_falls_back_rather_than_raising():
    assert pick_range("nonsense").key == "1W"


# --- the axis/bucket distinction that caused a real bug -------------------

def test_axis_scale_comes_from_span_not_bucket():
    # 1M buckets into 2-hour bars, so the bucket IS intraday — but a
    # month-wide axis labelled "22:00" tells the reader nothing. Span decides.
    month = pick_range("1M")
    assert month.is_intraday is True
    assert month.axis_scale == "day"


def test_short_ranges_label_with_clock_times():
    assert pick_range("1D").axis_scale == "time"


def test_long_ranges_label_with_months():
    assert pick_range("1Y").axis_scale == "month"
    assert pick_range("MAX").axis_scale == "month"


def test_every_range_keeps_the_chart_within_a_sane_point_count():
    # Each range should land near the target rather than shipping a year of
    # half-hours at native resolution.
    for rng in RANGES:
        if rng.span is None:
            continue
        assert rng.span / rng.bucket <= 1500, f"{rng.key} would ship too many points"


def test_auto_bucket_snaps_to_readable_intervals():
    assert auto_bucket(timedelta(days=1)) == PERIOD
    assert auto_bucket(timedelta(days=365)) in (timedelta(days=1), timedelta(hours=12))
    assert auto_bucket(timedelta(days=3650)) >= timedelta(days=7)
