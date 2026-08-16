"""Replaying a real production trace, and refusing to overstate the result.

The fixture here is a handful of records shaped like the real Philly trace,
including its awkward parts: the literal string "None" for a missing time,
failed and killed jobs, and multi-attempt jobs. The trace itself is 37 MB and
lives under the ignored cache, so it is never committed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core import trace_replay

TRACE_START = datetime(2017, 10, 7, 0, 0)


def _record(job_id, submitted, start, end, gpus=1, status="Pass",
            attempts=None):
    return {
        "jobid": job_id, "status": status, "vc": "abc", "user": "u1",
        "submitted_time": submitted,
        "attempts": attempts if attempts is not None else [{
            "start_time": start, "end_time": end,
            "detail": [{"ip": "m1", "gpus": [f"gpu{i}" for i in range(gpus)]}],
        }],
    }


def _trace(tmp_path, records):
    path = tmp_path / "cluster_job_log"
    path.write_text(json.dumps(records))
    return path


def _series(hours=72, start=None, cheap_at=None):
    """A flat market with one cheap, clean half hour.

    The default start is a whole number of weeks after the trace's own start,
    so alignment maps the first job onto index 0 rather than somewhere off
    the end of a short test window.
    """
    start = start or (TRACE_START + timedelta(weeks=440)).replace(
        tzinfo=timezone.utc)
    points = []
    for index in range(hours * 2):
        stamp = start + timedelta(minutes=30 * index)
        cheap = cheap_at is not None and index == cheap_at
        points.append(GridDataPoint(timestamp=stamp,
                                    price=1.0 if cheap else 100.0,
                                    carbon_intensity=1.0 if cheap else 200.0))
    return points


# --- reading the trace ------------------------------------------------------

def test_only_jobs_that_completed_are_replayed(tmp_path):
    """A failed job's runtime measures when it broke, not what work needed."""
    path = _trace(tmp_path, [
        _record("passed", "2017-10-07 01:00:00", "2017-10-07 01:30:00",
                "2017-10-07 02:30:00"),
        _record("failed", "2017-10-07 01:00:00", "2017-10-07 01:30:00",
                "2017-10-07 02:30:00", status="Failed"),
        _record("killed", "2017-10-07 01:00:00", "2017-10-07 01:30:00",
                "2017-10-07 02:30:00", status="Killed"),
    ])
    assert [job.job_id for job in trace_replay.load_jobs(path)] == ["passed"]


def test_the_literal_string_none_is_treated_as_a_missing_time(tmp_path):
    """The trace writes missing times as "None", which parses as a string.

    Reading it as a date raises; reading it as present would invent a job.
    """
    path = _trace(tmp_path, [
        _record("no-end", "2017-10-07 01:00:00", "2017-10-07 01:30:00", "None"),
        _record("fine", "2017-10-07 01:00:00", "2017-10-07 01:30:00",
                "2017-10-07 02:00:00"),
    ])
    assert [job.job_id for job in trace_replay.load_jobs(path)] == ["fine"]


def test_a_job_with_no_recorded_gpus_is_dropped_not_defaulted(tmp_path):
    """Defaulting to one GPU would invent the quantity being measured."""
    path = _trace(tmp_path, [_record("bare", "2017-10-07 01:00:00",
                                     "2017-10-07 01:30:00",
                                     "2017-10-07 02:00:00", gpus=0)])
    assert trace_replay.load_jobs(path) == []


def test_a_multi_attempt_job_spans_its_first_start_to_its_last_end(tmp_path):
    path = _trace(tmp_path, [_record(
        "restarted", "2017-10-07 01:00:00", None, None, attempts=[
            {"start_time": "2017-10-07 01:30:00",
             "end_time": "2017-10-07 02:00:00",
             "detail": [{"ip": "m1", "gpus": ["gpu0"]}]},
            {"start_time": "2017-10-07 03:00:00",
             "end_time": "2017-10-07 05:00:00",
             "detail": [{"ip": "m2", "gpus": ["gpu0", "gpu1"]}]},
        ])])
    job = trace_replay.load_jobs(path)[0]
    assert job.runtime_hours == 3.5          # 01:30 to 05:00
    assert job.observed_delay_hours == 0.5   # submitted 01:00, started 01:30
    assert job.gpus == 3                     # every GPU the job held


def test_a_missing_trace_says_where_to_get_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="philly-traces"):
        trace_replay.load_jobs(tmp_path / "absent")


# --- the replay itself ------------------------------------------------------

def test_the_observed_policy_never_delays_a_job_beyond_its_real_queue(tmp_path):
    """The claim the whole module exists to support.

    Every job already waited in a production queue. Reusing exactly that wait
    means nothing finishes later than it actually did, so the saving cannot be
    dismissed as a delay traded for money.
    """
    path = _trace(tmp_path, [_record(
        "waited", "2017-10-07 00:00:00", "2017-10-07 02:00:00",
        "2017-10-07 02:30:00")])
    jobs = trace_replay.load_jobs(path)
    payload = trace_replay.replay(jobs, _series(cheap_at=3),
                                  policy="observed").as_dict()
    # It waited two hours, so it may move up to two hours and no further.
    assert payload["jobs_moved"] == 1
    assert 0 < payload["added_delay_hours"]["max"] <= 2.0
    assert payload["cost_saved"] > 0


def test_a_job_that_never_queued_cannot_be_moved(tmp_path):
    """No slack means no saving, and the report must say so rather than round."""
    path = _trace(tmp_path, [_record(
        "instant", "2017-10-07 00:00:00", "2017-10-07 00:00:00",
        "2017-10-07 00:30:00")])
    jobs = trace_replay.load_jobs(path)
    payload = trace_replay.replay(jobs, _series(cheap_at=20),
                                  policy="observed").as_dict()
    assert payload["jobs_moved"] == 0
    assert payload["jobs_with_no_slack"] == 1
    assert payload["cost_saved"] == 0


def test_a_declared_deadline_unlocks_what_the_observed_queue_did_not(tmp_path):
    """The counterfactual, and it must be reported as a separate policy."""
    path = _trace(tmp_path, [_record(
        "instant", "2017-10-07 00:00:00", "2017-10-07 00:00:00",
        "2017-10-07 00:30:00")])
    jobs = trace_replay.load_jobs(path)
    declared = trace_replay.replay(jobs, _series(cheap_at=20),
                                   policy="declared",
                                   declared_deadline_hours=24).as_dict()
    assert declared["jobs_moved"] == 1
    assert declared["cost_saved"] > 0
    assert declared["policy"] == "declared"


def test_alignment_preserves_weekday_and_time_of_day(tmp_path):
    """Both prices and job submissions have daily and weekly structure.

    Aligning on whole weeks stops Monday-morning jobs landing in Saturday
    prices, which would replay a real workload into the wrong market shape.
    """
    path = _trace(tmp_path, [_record(
        "job", "2017-10-07 09:00:00", "2017-10-07 09:30:00",
        "2017-10-07 10:00:00")])
    jobs = trace_replay.load_jobs(path)
    series = _series(hours=336,
                     start=datetime(2026, 3, 2, tzinfo=timezone.utc))
    offset = trace_replay.align(jobs, series)
    assert offset.days % 7 == 0
    moved = jobs[0].submitted + offset
    assert moved.weekday() == jobs[0].submitted.weekday()
    assert (moved.hour, moved.minute) == (9, 0)


def test_every_result_carries_the_trace_attribution(tmp_path):
    """The licence is CC BY: attribution travels with the number."""
    path = _trace(tmp_path, [_record(
        "job", "2017-10-07 00:00:00", "2017-10-07 01:00:00",
        "2017-10-07 02:00:00")])
    payload = trace_replay.replay(trace_replay.load_jobs(path),
                                  _series(cheap_at=20)).as_dict()
    assert payload["source"]["licence"] == "CC BY 4.0"
    assert "ATC 2019" in payload["source"]["citation"]


def test_an_unknown_policy_is_refused(tmp_path):
    path = _trace(tmp_path, [_record(
        "job", "2017-10-07 00:00:00", "2017-10-07 01:00:00",
        "2017-10-07 02:00:00")])
    with pytest.raises(ValueError, match="policy must be"):
        trace_replay.replay(trace_replay.load_jobs(path), _series(),
                            policy="optimistic")
