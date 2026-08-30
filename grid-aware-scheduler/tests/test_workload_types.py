"""Workload types, and the refusals that keep the scheduler honest.

The scheduler was already workload-agnostic — `PortfolioJob` carries
`work_amount` and `work_unit`, not tokens. These tests lock the new type layer
that compiles down to it, and lock the places it declines to invent an answer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import workload_types as wt

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _mining(**overrides):
    attributes = {"hash_rate_th_s": 100.0, "efficiency_j_per_th": 21.5,
                  "revenue_per_th_day": 0.05}
    attributes.update(overrides.pop("attributes", {}))
    return wt.build("m1", "Rig", wt.WorkloadType.MINING, NOW,
                    duration_hours=24, attributes=attributes, **overrides)


# --- The registry itself ---

def test_every_requested_workload_type_exists():
    expected = {"ai_training", "ai_inference", "mining", "rendering", "hpc",
                "data_processing", "batch", "custom"}
    assert {t.value for t in wt.WorkloadType} == expected


def test_every_type_has_a_definition_and_a_valid_work_unit():
    for workload_type in wt.WorkloadType:
        entry = wt.DEFINITIONS[workload_type]
        assert entry.label and entry.description
        assert entry.default_work_unit in wt.WORK_UNITS


def test_the_catalogue_is_shaped_for_a_selector():
    entries = wt.catalogue()
    assert len(entries) == len(wt.WorkloadType)
    for entry in entries:
        assert {"type", "label", "description", "work_unit",
                "continuous", "fields"} <= set(entry)


def test_an_unknown_type_is_refused_with_the_known_ones_named():
    with pytest.raises(ValueError, match="unknown workload type"):
        wt.definition("quantum_annealing")


def test_only_mining_is_continuous():
    continuous = {t for t in wt.WorkloadType if wt.DEFINITIONS[t].continuous}
    assert continuous == {wt.WorkloadType.MINING}


# --- Type-specific fields are validated, not trusted ---

def test_an_unknown_field_is_rejected_rather_than_ignored():
    """Silently dropping a field means a typo becomes a wrong schedule."""
    with pytest.raises(wt.WorkloadRefused, match="does not accept"):
        wt.build("r", "R", wt.WorkloadType.RENDERING, NOW, power_kw=1.0,
                 attributes={"frame_count": 10, "seconds_per_frame": 1.0,
                             "frames_per_second": 24})


def test_a_missing_required_field_is_refused():
    with pytest.raises(wt.WorkloadRefused, match="required"):
        wt.build("h", "H", wt.WorkloadType.HPC, NOW, power_kw=1.0)


def test_a_field_below_its_minimum_is_refused():
    with pytest.raises(wt.WorkloadRefused):
        wt.build("r", "R", wt.WorkloadType.RENDERING, NOW, power_kw=1.0,
                 attributes={"frame_count": 0, "seconds_per_frame": 1.0})


# --- Derivation: duration and power come from the type's own fields ---

def test_mining_power_is_physics_not_an_estimate():
    """TH/s x J/TH = watts, by definition. 100 x 21.5 = 2150 W."""
    spec = _mining()
    assert spec.power_kw == pytest.approx(2.15)
    assert spec.provenance == "SPEC"


def test_a_typed_in_power_cannot_override_the_miner_datasheet():
    """Anywhere else the caller wins. Here the physics is strictly better."""
    spec = _mining(power_kw=99.0)
    assert spec.power_kw == pytest.approx(2.15)


def test_rendering_duration_divides_by_the_parallel_workers():
    """1200 frames x 90 s / 10 workers = 3 hours."""
    spec = wt.build("r", "R", wt.WorkloadType.RENDERING, NOW,
                    deadline=NOW + timedelta(hours=48), power_kw=4.0,
                    attributes={"frame_count": 1200, "seconds_per_frame": 90,
                                "max_parallel_frames": 10})
    assert spec.duration_hours == pytest.approx(3.0)
    assert spec.work_amount == 1200
    assert spec.work_unit == "frames"


def test_hpc_duration_divides_core_hours_by_cores():
    spec = wt.build("h", "H", wt.WorkloadType.HPC, NOW, power_kw=12.0,
                    resources=wt.ResourceRequest(cpu_cores=64),
                    attributes={"core_hours": 1280})
    assert spec.duration_hours == pytest.approx(20.0)


def test_data_processing_duration_is_dataset_over_throughput():
    spec = wt.build("d", "D", wt.WorkloadType.DATA_PROCESSING, NOW,
                    power_kw=5.0,
                    attributes={"dataset_gb": 400, "throughput_gb_per_hour": 50})
    assert spec.duration_hours == pytest.approx(8.0)


def test_inference_work_amount_is_requests_times_tokens():
    spec = wt.build("i", "I", wt.WorkloadType.AI_INFERENCE, NOW,
                    duration_hours=2.0, power_kw=8.0,
                    attributes={"request_count": 1000,
                                "tokens_per_request": 512})
    assert spec.work_amount == 512_000
    assert spec.work_unit == "tokens"


# --- Refusing rather than defaulting ---

def test_a_workload_with_no_derivable_duration_is_refused():
    """A default duration would produce a confident schedule for imaginary
    work, which is worse than no schedule."""
    with pytest.raises(wt.WorkloadRefused, match="needs a duration"):
        wt.build("b", "B", wt.WorkloadType.BATCH, NOW, power_kw=1.0)


def test_a_workload_with_no_power_is_refused():
    with pytest.raises(wt.WorkloadRefused, match="power"):
        wt.build("b", "B", wt.WorkloadType.BATCH, NOW, duration_hours=1.0)


def test_a_deadline_before_the_start_is_refused():
    with pytest.raises(ValueError, match="deadline must be after"):
        wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                 deadline=NOW - timedelta(hours=1),
                 duration_hours=1.0, power_kw=1.0)


# --- Slack, which is the whole scheduling lever ---

def test_slack_is_the_window_minus_the_run():
    spec = wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                    deadline=NOW + timedelta(hours=24),
                    duration_hours=4.0, power_kw=10.0)
    assert spec.slack_hours() == pytest.approx(20.0)
    assert spec.fits_window()


def test_a_job_longer_than_its_window_has_no_slack_and_does_not_fit():
    """The trace-replay finding in code: work that outlasts its deadline
    cannot be shifted at all, which is where most energy actually sits."""
    spec = wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                    deadline=NOW + timedelta(hours=4),
                    duration_hours=30.0, power_kw=10.0)
    assert spec.slack_hours() == 0.0
    assert not spec.fits_window()


# --- Flexibility is several capabilities, not one flag ---

def test_pausable_and_checkpointable_are_not_the_same_capability():
    flexible = wt.Flexibility(pausable=True, checkpointable=False)
    assert flexible.pausable and not flexible.checkpointable
    assert not flexible.splittable


def test_parallel_units_require_declaring_parallelisable():
    with pytest.raises(ValueError, match="requires parallelisable"):
        wt.Flexibility(max_parallel_units=8)


def test_resource_headroom_is_what_makes_work_finish_sooner():
    """The only honest route to a faster finish: more hardware, not cheaper
    electricity."""
    fixed = wt.ResourceRequest(gpu_count=4, min_gpu_count=4, max_gpu_count=4)
    elastic = wt.ResourceRequest(gpu_count=4, min_gpu_count=2, max_gpu_count=16)
    assert not fixed.scalable
    assert elastic.scalable


def test_a_resource_range_that_inverts_is_refused():
    with pytest.raises(ValueError, match="cannot exceed"):
        wt.ResourceRequest(min_gpu_count=8, max_gpu_count=2)


# --- Compiling down to the existing scheduler ---

def test_mining_is_refused_by_the_deadline_scheduler_and_told_where_to_go():
    """Continuous work through a deadline scheduler produces a valid-looking
    answer to a question nobody asked."""
    with pytest.raises(wt.WorkloadRefused, match="core.mining"):
        wt.to_portfolio_job(_mining(), ())


def test_work_that_cannot_finish_in_time_is_refused_before_scheduling():
    spec = wt.build("r", "R", wt.WorkloadType.RENDERING, NOW,
                    deadline=NOW + timedelta(hours=2), power_kw=4.0,
                    attributes={"frame_count": 1200, "seconds_per_frame": 90,
                                "max_parallel_frames": 1})
    with pytest.raises(wt.WorkloadRefused, match="cannot finish in time"):
        wt.to_portfolio_job(spec, ())


def test_price_never_changes_how_long_the_work_takes():
    """Cheap electricity does not make hardware faster. Every candidate for
    one workload carries the same runtime; only hardware may change it."""
    series = []
    spec = wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                    deadline=NOW + timedelta(hours=24),
                    duration_hours=4.0, power_kw=10.0)
    placements = [
        {"key": "cheap", "series": series},
        {"key": "expensive", "series": series},
    ]
    candidates = wt.to_planning_candidates(spec, placements)
    assert len({c.runtime_hours for c in candidates}) == 1
    assert candidates[0].runtime_hours == pytest.approx(4.0)


def test_a_spec_serialises_with_its_provenance_and_slack():
    payload = wt.build("b", "B", wt.WorkloadType.BATCH, NOW,
                       deadline=NOW + timedelta(hours=10),
                       duration_hours=2.0, power_kw=3.0).public_dict()
    assert payload["type"] == "batch"
    assert payload["provenance"] == "ESTIMATED"
    assert payload["slack_hours"] == pytest.approx(8.0)
    assert payload["energy_kwh"] == pytest.approx(6.0)
