from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.energy import (BatterySpec, EnergySource, FacilityPoint, FacilitySite,
                         SiteEnergyProfile, SupplyPoint, dispatch_energy,
                         haversine_km)

START = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _points(values, *, cost=0, carbon=0, confidence=1):
    return tuple(
        SupplyPoint(
            START + timedelta(minutes=30 * index), value, cost, carbon,
            confidence,
        )
        for index, value in enumerate(values)
    )


def _source(source_id, kind, values, *, cost=0, carbon=0,
            renewable=None, carbon_free=False, delivery="onsite",
            dispatchable=False, confidence=1):
    return EnergySource(
        source_id, source_id.title(), kind,
        _points(values, cost=cost, carbon=carbon, confidence=confidence),
        renewable=renewable,
        carbon_free=carbon_free,
        delivery_type=delivery,
        dispatchable=dispatchable,
        provenance="SIMULATED",
    )


def _profile(sources, *, base=0, battery=None, priority="carbon"):
    count = len(sources[0].points)
    return SiteEnergyProfile(
        "GB", "London",
        tuple(
            FacilityPoint(START + timedelta(minutes=30 * index), base)
            for index in range(count)
        ),
        tuple(sources),
        battery,
        priority,
    )


def test_dispatch_accounts_for_every_physical_generation_type():
    sources = [
        _source("solar", "solar", [2], carbon_free=True),
        _source("wind", "wind", [2], carbon_free=True),
        _source("hydro", "hydro", [2], carbon_free=True),
        _source("nuclear", "nuclear", [2], renewable=False, carbon_free=True),
        _source("geothermal", "geothermal", [2], carbon=20),
        _source("biomass", "biomass", [2], carbon=100),
        _source("gas", "gas", [2], carbon=400, dispatchable=True),
        _source("coal", "coal", [2], carbon=900, dispatchable=True),
        _source("oil", "oil", [2], carbon=700, dispatchable=True),
        _source("grid", "grid", [100], cost=50, carbon=300,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    result = dispatch_energy(
        _profile(sources), {"job": {START: 20}},
    )
    assert result.feasible
    assert set(result.sources) == {
        "solar", "wind", "hydro", "nuclear", "geothermal", "biomass",
        "gas", "coal", "oil", "grid",
    }
    assert result.jobs["job"].energy_kwh == pytest.approx(10)
    assert result.ai_carbon_free_kwh == pytest.approx(4)


def test_base_load_is_served_before_ai_so_only_real_surplus_is_matched():
    sources = [
        _source("solar", "solar", [120], carbon_free=True),
        _source("grid", "grid", [200], cost=100, carbon=500,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    result = dispatch_energy(
        _profile(sources, base=60), {"training": {START: 50}},
    )
    job = result.jobs["training"]
    assert job.energy_kwh == pytest.approx(25)
    assert job.renewable_kwh == pytest.approx(25)
    assert job.grid_kwh == 0
    assert result.sources["solar"].base_kwh == pytest.approx(30)
    assert result.sources["solar"].ai_kwh == pytest.approx(25)
    assert result.curtailed_kwh == pytest.approx(5)


def test_confidence_derates_variable_generation_before_scheduling_claims():
    sources = [
        _source("wind", "wind", [100], carbon_free=True, confidence=0.5),
        _source("grid", "grid", [100], cost=50, carbon=400,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    result = dispatch_energy(
        _profile(sources), {"job": {START: 80}},
    )
    job = result.jobs["job"]
    assert job.renewable_kwh == pytest.approx(25)
    assert job.grid_kwh == pytest.approx(15)


def test_contractual_matching_does_not_masquerade_as_physical_supply():
    sources = [
        _source("ppa", "solar", [100], carbon_free=True,
                delivery="contractual"),
        _source("grid", "grid", [100], cost=50, carbon=400,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    result = dispatch_energy(
        _profile(sources), {"job": {START: 50}},
    )
    assert result.jobs["job"].grid_kwh == pytest.approx(25)
    assert result.jobs["job"].renewable_kwh == 0
    assert result.sources["ppa"].contractual_available_kwh == pytest.approx(50)


def test_battery_stores_surplus_clean_generation_and_serves_later_ai_load():
    later = START + timedelta(minutes=30)
    sources = [
        _source("solar", "solar", [100, 0], carbon_free=True),
        _source("grid", "grid", [100, 100], cost=100, carbon=500,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    battery = BatterySpec(
        capacity_kwh=50,
        max_charge_kw=100,
        max_discharge_kw=100,
        round_trip_efficiency=1,
    )
    result = dispatch_energy(
        _profile(sources, battery=battery), {"job": {later: 40}},
    )
    job = result.jobs["job"]
    assert job.battery_kwh == pytest.approx(20)
    assert job.renewable_kwh == pytest.approx(20)
    assert job.grid_kwh == 0
    assert result.intervals[0].battery_charge_input_kwh == pytest.approx(50)
    assert result.final_battery_kwh == pytest.approx(30)


def test_energy_input_validation_rejects_impossible_values():
    with pytest.raises(ValueError, match="confidence"):
        SupplyPoint(START, 1, 0, 0, 1.1)
    with pytest.raises(ValueError, match="initial battery energy"):
        BatterySpec(10, 1, 1, initial_energy_kwh=11)


def test_exact_site_geometry_and_declared_delivery_losses_are_audited():
    site = FacilitySite(
        "facility-1", "Exact facility", 51.5074, -0.1278,
        "connection-33kv", "Europe/London",
    )
    source = EnergySource(
        "wind", "Remote wind", "wind", _points([100]),
        carbon_free=True, delivery_type="dedicated_wire",
        provenance="OPERATOR_FORECAST", latitude=52.0, longitude=-0.1278,
        grid_connection_id="wind-export-1", delivery_loss_fraction=0.1,
    )
    grid = _source(
        "grid", "grid", [100], carbon=400, renewable=False,
        delivery="grid", dispatchable=True,
    )
    profile = SiteEnergyProfile(
        "GB", "London", (FacilityPoint(START),), (source, grid),
        site=site,
    )
    result = dispatch_energy(profile, {"job": {START: 100}})
    wind = result.sources["wind"]
    assert result.site == site
    assert result.jobs["job"].renewable_kwh == pytest.approx(45)
    assert result.jobs["job"].grid_kwh == pytest.approx(5)
    assert wind.delivery_loss_kwh == pytest.approx(5)
    assert wind.distance_to_site_km == pytest.approx(54.8, abs=0.2)
    assert wind.grid_connection_id == "wind-export-1"


def test_geospatial_validation_requires_truthful_coordinate_pairs():
    with pytest.raises(ValueError, match="supplied together"):
        EnergySource("solar", "Solar", "solar", _points([1]), latitude=51)
    with pytest.raises(ValueError, match="at most 90"):
        FacilitySite("site", "Site", 91, 0)
    with pytest.raises(ValueError, match="delivery_loss_fraction"):
        EnergySource(
            "solar", "Solar", "solar", _points([1]),
            delivery_loss_fraction=1,
        )
    assert haversine_km(51.5074, -0.1278, 51.5074, -0.1278) == 0


@pytest.mark.parametrize(("priority", "expected_source"), [
    ("renewable", "biomass"),
    ("carbon_free", "nuclear"),
    ("carbon", "grid"),
    ("cost", "gas"),
])
def test_dispatchable_source_merit_order_follows_operator_policy(
        priority, expected_source):
    sources = [
        _source("biomass", "biomass", [10], cost=100, carbon=400,
                renewable=True, dispatchable=True),
        _source("nuclear", "nuclear", [10], cost=200, carbon=50,
                renewable=False, carbon_free=True, dispatchable=True),
        _source("gas", "gas", [10], cost=5, carbon=500,
                renewable=False, dispatchable=True),
        _source("grid", "grid", [10], cost=10, carbon=10,
                renewable=False, delivery="grid", dispatchable=True),
    ]
    result = dispatch_energy(
        _profile(sources, priority=priority), {"job": {START: 10}},
    )
    assert result.jobs["job"].source_kwh == {expected_source: pytest.approx(5)}
