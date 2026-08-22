"""Per-unit GB plant availability. Offline — no Elexon calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from adapters import gb_plant
from core import site_profile

UTC = timezone.utc


def _frame(rows):
    return pd.DataFrame(
        [{"time_from": stamp, "level_from": level} for stamp, level in rows])


def test_megawatts_become_kilowatts_on_the_half_hour_spine():
    stamp = datetime(2026, 8, 20, 12, tzinfo=UTC)
    out = gb_plant._to_intervals(_frame([(stamp, 588)]), "level_from")
    assert out == {stamp: 588_000.0}


def test_several_ramp_segments_in_one_half_hour_take_the_binding_limit():
    """A limit applying for part of a period constrains the whole period."""
    base = datetime(2026, 8, 20, 12, tzinfo=UTC)
    out = gb_plant._to_intervals(
        _frame([(base, 588), (base + timedelta(minutes=10), 300),
                (base + timedelta(minutes=20), 500)]), "level_from")
    assert out == {base: 300_000.0}


def test_minutes_are_binned_to_the_containing_half_hour():
    base = datetime(2026, 8, 20, 12, tzinfo=UTC)
    out = gb_plant._to_intervals(
        _frame([(base + timedelta(minutes=40), 400)]), "level_from")
    assert list(out) == [base.replace(minute=30)]


def test_naive_timestamps_are_read_as_utc_not_local():
    naive = datetime(2026, 8, 20, 12)
    out = gb_plant._to_intervals(_frame([(naive, 100)]), "level_from")
    assert list(out) == [naive.replace(tzinfo=UTC)]


def test_missing_levels_are_dropped_rather_than_zeroed():
    """A row with no level is a gap in the filing, not a declared outage."""
    stamp = datetime(2026, 8, 20, 12, tzinfo=UTC)
    out = gb_plant._to_intervals(_frame([(stamp, float("nan"))]), "level_from")
    assert out == {}


def test_an_empty_filing_returns_nothing():
    assert gb_plant._to_intervals(None, "level_from") == {}
    assert gb_plant._to_intervals(pd.DataFrame(), "level_from") == {}


def test_a_zero_declaration_is_an_outage_not_a_gap():
    stamp = datetime(2026, 8, 20, 12, tzinfo=UTC)
    interval = gb_plant.PlantInterval(stamp, available_kw=0.0)
    assert interval.offline
    assert not gb_plant.PlantInterval(stamp, available_kw=588_000.0).offline


def test_the_settlement_day_window_is_widened_past_the_utc_day(monkeypatch):
    """GB settlement days start at 23:00 UTC under BST.

    Fetching only the UTC dates left the last two half-hours of a UTC day
    absent, and absent reads downstream as unavailable — which reported a
    running reactor as offline for an hour.
    """
    asked = []

    def fake(bm_unit, dataset, day, **kwargs):
        asked.append(day)
        return pd.DataFrame()

    monkeypatch.setattr(gb_plant.national_grid_tool, "load",
                        lambda *a, **k: (fake,))
    stamps = [datetime(2026, 8, 20, 23, 30, tzinfo=UTC)]
    gb_plant.availability_by_timestamp("T_SIZB-1", stamps)
    assert stamps[0].date() + timedelta(days=1) in asked


def test_no_timestamps_makes_no_request(monkeypatch):
    monkeypatch.setattr(gb_plant.national_grid_tool, "load",
                        lambda *a, **k: pytest.fail("should not fetch"))
    assert gb_plant.availability_by_timestamp("T_SIZB-1", []) == {}


# --- the profile side -----------------------------------------------------

def _plant_source(**over):
    raw = {"source_id": "npp", "name": "Unit 1", "kind": "nuclear",
           "capacity_kw": 600_000, "availability_method": "plant",
           "plant_id": "T_SIZB-1", "evidence": "contracted",
           "delivery_type": "dedicated_wire", "carbon_g_per_kwh": 12}
    raw.update(over)
    return {
        "version": "facility-energy-v1", "declared_by": "t",
        "declared_at": "2026-08-22",
        "site": {"site_id": "s", "name": "S", "latitude": 54.6,
                 "longitude": -1.2},
        "market": {"market": over.pop("_market", "GB"), "location": "national"},
        "facility": {"base_load_kw": 100, "pue": 1.2, "max_import_kw": 1000,
                     "evidence": "contracted"},
        "sources": [raw],
    }


def test_plant_method_requires_a_unit_id_not_just_coordinates():
    doc = _plant_source(plant_id="")
    with pytest.raises(site_profile.ProfileError, match="plant_id"):
        site_profile.parse(doc)


def test_plant_backed_availability_is_reported_not_estimated():
    """It is the plant's own filing, so it is not capped at ESTIMATED."""
    profile = site_profile.parse(_plant_source())
    assert profile.sources[0].provenance == site_profile.REPORTED_PROVENANCE


def test_declared_kilowatts_are_used_directly_not_scaled_by_nameplate():
    profile = site_profile.parse(_plant_source())
    stamps = [datetime(2026, 8, 20, 12, tzinfo=UTC)]
    data = {"npp": {stamps[0]: 588_000.0}}
    assert site_profile.availability_kw(
        profile.sources[0], stamps, None, data) == [588_000.0]


def test_an_unreported_half_hour_is_treated_as_unavailable():
    """For a plant we are physically wired to, silence is not permission."""
    profile = site_profile.parse(_plant_source())
    stamps = [datetime(2026, 8, 20, 12, tzinfo=UTC)]
    assert site_profile.availability_kw(
        profile.sources[0], stamps, None, {"npp": {}}) == [0.0]


def test_a_non_gb_market_refuses_the_plant_method_and_says_so():
    doc = _plant_source()
    doc["market"]["market"] = "CAISO"
    profile = site_profile.parse(doc)
    data, warnings = site_profile.fetch_plant_data(
        profile, [datetime(2026, 8, 20, 12, tzinfo=UTC)])
    assert data == {}
    assert any("only resolves in GB" in w for w in warnings)


def test_a_failed_fetch_contributes_no_power_and_names_the_unit(monkeypatch):
    profile = site_profile.parse(_plant_source())
    monkeypatch.setattr(
        gb_plant, "availability_by_timestamp",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    data, warnings = site_profile.fetch_plant_data(
        profile, [datetime(2026, 8, 20, 12, tzinfo=UTC)])
    assert data == {"npp": {}}
    assert any("T_SIZB-1" in w for w in warnings)
