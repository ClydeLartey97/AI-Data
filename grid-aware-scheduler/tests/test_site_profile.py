"""The operator-declared site document, and the ceiling it derives.

The document's job is to be refusable. Most of these tests are about what a
declaration is not allowed to claim — because the whole value of asking an
operator to declare their site is lost if the software accepts anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import site_profile
from core.site_profile import ProfileError

START = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _document(**overrides):
    document = {
        "version": "facility-energy-v1",
        "declared_by": "Site engineering",
        "declared_at": "2026-08-16",
        "site": {"site_id": "dc-1", "name": "Site One",
                 "latitude": 51.5074, "longitude": -0.1278,
                 "time_zone": "Europe/London"},
        "market": {"market": "GB", "location": "national"},
        "facility": {"base_load_kw": 60, "pue": 1.25, "max_import_kw": 400},
        "sources": [{
            "source_id": "solar-a", "name": "Rooftop array", "kind": "solar",
            "capacity_kw": 500, "availability_method": "diurnal",
            "peak_hour": 12, "evidence": "nameplate",
        }],
        "dispatch_priority": "renewable",
    }
    document.update(overrides)
    return document


def _half_hours(count=48, start=START):
    return [start + timedelta(minutes=30 * i) for i in range(count)]


# --- what the document refuses ---------------------------------------------

def test_an_unknown_version_is_refused_rather_than_guessed():
    with pytest.raises(ProfileError, match="unsupported site profile version"):
        site_profile.parse(_document(version="facility-energy-v2"))


def test_claiming_a_meter_reading_requires_naming_the_meter():
    """"Metered" is the strongest tier. It has to point at something."""
    document = _document()
    document["facility"] = {"base_load_kw": 60, "pue": 1.25,
                            "max_import_kw": 400, "evidence": "metered"}
    with pytest.raises(ProfileError, match="requires site.grid_connection_id"):
        site_profile.parse(document)


def test_a_site_of_contracts_alone_cannot_serve_load():
    """A virtual PPA moves no electrons. Something physical must exist."""
    document = _document(sources=[{
        "source_id": "vppa", "name": "Virtual PPA", "kind": "wind",
        "capacity_kw": 5000, "delivery_type": "contractual",
        "evidence": "contracted",
    }])
    with pytest.raises(ProfileError, match="physically delivered"):
        site_profile.parse(document)


def test_a_weather_backed_source_must_say_where_it_is():
    document = _document(sources=[{
        "source_id": "solar-a", "name": "Array", "kind": "solar",
        "capacity_kw": 500, "availability_method": "weather",
        "evidence": "nameplate",
    }])
    with pytest.raises(ProfileError, match="needs the source's own coordinates"):
        site_profile.parse(document)


def test_a_reserved_source_id_cannot_be_reused():
    """`grid` names the residual supply the dispatcher adds itself."""
    document = _document(sources=[{
        "source_id": "grid", "name": "Mine", "kind": "solar",
        "capacity_kw": 10, "evidence": "nameplate"}])
    with pytest.raises(ProfileError, match="duplicate source_id"):
        site_profile.parse(document)


def test_an_onsite_source_evidenced_by_contract_is_flagged_not_rejected():
    """It may be a dedicated wire. It may be a market instrument mislabelled.

    The software cannot tell, so it says so rather than silently choosing.
    """
    document = _document()
    document["sources"][0]["evidence"] = "contracted"
    profile = site_profile.parse(document)
    assert any("dedicated wire" in warning for warning in profile.warnings)


# --- provenance -------------------------------------------------------------

def test_a_modelled_shape_is_estimated_however_well_the_capacity_is_known():
    """Someone may have metered the array. Nobody has metered tomorrow."""
    document = _document()
    document["site"]["grid_connection_id"] = "MPAN-0001"
    document["sources"][0]["evidence"] = "metered"
    document["sources"][0]["grid_connection_id"] = "MPAN-0002"
    profile = site_profile.parse(document)
    assert profile.sources[0].evidence == "metered"
    assert profile.sources[0].provenance == "ESTIMATED"


def test_an_observed_series_keeps_the_evidence_tier_it_was_given():
    document = _document()
    document["site"]["grid_connection_id"] = "MPAN-0001"
    document["sources"][0].update({
        "availability_method": "series", "evidence": "metered",
        "grid_connection_id": "MPAN-0002",
        "capacity_factors": [0.0, 0.5, 1.0, 0.5],
    })
    profile = site_profile.parse(document)
    assert profile.sources[0].provenance == "MEASURED"


# --- the power ceiling ------------------------------------------------------

def test_onsite_generation_raises_the_ceiling_when_it_is_producing():
    """The whole point: more power available at noon than at 03:00."""
    profile = site_profile.parse(_document())
    stamps = _half_hours()
    envelope = dict(site_profile.power_envelope(profile, stamps))
    noon = envelope[datetime(2026, 8, 12, 12, tzinfo=timezone.utc)]
    night = envelope[datetime(2026, 8, 12, 3, tzinfo=timezone.utc)]
    assert noon > night
    # At night only the import limit is available.
    assert night == pytest.approx(400)


def test_the_ceiling_never_exceeds_the_sites_electrical_limit():
    """On-site generation raises the ceiling toward the wire, not through it."""
    profile = site_profile.parse(_document())
    stamps = _half_hours()
    envelope = site_profile.power_envelope(
        profile, stamps, electrical_limit_kw=450)
    assert max(value for _, value in envelope) <= 450


def test_a_contractual_instrument_does_not_raise_the_ceiling():
    """It cannot power an accelerator, so it cannot licence one to run."""
    physical = site_profile.parse(_document())
    with_vppa = _document()
    with_vppa["sources"] = with_vppa["sources"] + [{
        "source_id": "vppa", "name": "Virtual PPA", "kind": "wind",
        "capacity_kw": 10000, "delivery_type": "contractual",
        "evidence": "contracted"}]
    contracted = site_profile.parse(with_vppa)
    stamps = _half_hours()
    assert (dict(site_profile.power_envelope(contracted, stamps))
            == dict(site_profile.power_envelope(physical, stamps)))


def test_low_confidence_supply_does_not_licence_a_schedule_that_needs_it():
    document = _document()
    document["sources"][0]["confidence"] = 0.5
    stamps = _half_hours()
    derated = dict(site_profile.power_envelope(
        site_profile.parse(document), stamps))
    full = dict(site_profile.power_envelope(
        site_profile.parse(_document()), stamps))
    noon = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    assert derated[noon] < full[noon]


# --- compiling into the existing contract -----------------------------------

def test_the_document_compiles_into_the_planner_s_existing_request():
    """One declared file becomes the facility object the API already takes."""
    profile = site_profile.parse(_document())
    payload = site_profile.to_facility_payload(profile, _half_hours(4))
    assert payload["base_load_kw"] == 60
    # The API's own name for the setting — emitting the document's wording
    # would silently fall back to the default and change the dispatch order.
    assert payload["energy_priority"] == "renewable"
    assert "dispatch_priority" not in payload
    assert len(payload["pue_profile"]) == 4
    source = payload["energy_sources"][0]
    assert source["source_id"] == "solar-a"
    assert len(source["availability_kw"]) == 4
    assert source["provenance"] == "ESTIMATED"
    assert source["delivery_loss_fraction"] == 0.0


def test_a_declared_daily_shape_repeats_instead_of_reading_as_an_outage():
    """A 24-hour shape is what an operator has; zero-padding would lie."""
    document = _document()
    document["sources"][0].update({"availability_method": "series",
                                   "capacity_factors": [1.0, 0.0]})
    profile = site_profile.parse(document)
    values = site_profile.availability_kw(profile.sources[0], _half_hours(6))
    assert values == [500.0, 0.0, 500.0, 0.0, 500.0, 0.0]


def test_a_missing_document_is_not_an_error(tmp_path):
    """Most sites have not declared one, and that is a state, not a fault."""
    assert site_profile.load(tmp_path / "absent.json") is None


def test_a_loaded_document_reports_its_own_boundary(tmp_path):
    import json
    path = tmp_path / "site-profile.json"
    path.write_text(json.dumps(_document()))
    profile = site_profile.load(path)
    published = profile.public_dict()
    assert published["declared_by"] == "Site engineering"
    assert "not verified here" in published["boundary"]
    assert published["sources"][0]["physical"] is True


# --- weather, fetched per plant ---------------------------------------------

class _FakeAdapter:
    """Stands in for Open-Meteo. Records what it was asked for."""

    def __init__(self, radiation=800.0, wind=11.0, fail=False):
        self.radiation, self.wind, self.fail = radiation, wind, fail
        self.requests = []

    def forecast(self, location, days=2):
        self.requests.append((round(location.latitude, 4),
                              round(location.longitude, 4), days))
        if self.fail:
            raise RuntimeError("forecast host unreachable")

        class _Point:
            def __init__(self, stamp, radiation, wind):
                self.timestamp = stamp
                self.solar_radiation_wm2 = radiation
                self.temperature_c = 15.0
                self.wind_speed_100m_ms = wind
                self.cloud_cover_pct = 0.0

        return [_Point(START + timedelta(hours=i), self.radiation, self.wind)
                for i in range(48)]


def _weather_document():
    document = _document()
    document["sources"] = [
        {"source_id": "solar-roof", "name": "Roof array", "kind": "solar",
         "capacity_kw": 500, "availability_method": "weather",
         "latitude": 51.5074, "longitude": -0.1278, "evidence": "nameplate"},
        {"source_id": "wind-farm", "name": "Wind farm", "kind": "wind",
         "capacity_kw": 900, "availability_method": "weather",
         "latitude": 52.9000, "longitude": -1.1500, "evidence": "contracted",
         "delivery_type": "dedicated_wire"},
    ]
    return document


def test_each_plant_is_forecast_at_its_own_coordinates():
    """A wind farm fifty kilometres away has its own wind.

    Using the data centre's weather for it would be a quietly wrong answer
    rather than a missing one.
    """
    profile = site_profile.parse(_weather_document())
    adapter = _FakeAdapter()
    weather, warnings = site_profile.fetch_weather(
        profile, _half_hours(), adapter=adapter)
    assert warnings == []
    assert set(weather) == {"solar-roof", "wind-farm"}
    assert {(lat, lon) for lat, lon, _ in adapter.requests} == {
        (51.5074, -0.1278), (52.9, -1.15)}


def test_two_arrays_on_one_roof_cost_one_request():
    document = _weather_document()
    document["sources"][1].update({"source_id": "solar-roof-b",
                                   "kind": "solar", "latitude": 51.5074,
                                   "longitude": -0.1278,
                                   "delivery_type": "onsite"})
    adapter = _FakeAdapter()
    site_profile.fetch_weather(site_profile.parse(document),
                               _half_hours(), adapter=adapter)
    assert len(adapter.requests) == 1


def test_a_forecast_that_cannot_be_fetched_contributes_no_power():
    """Fail closed: an unavailable forecast must not licence a schedule."""
    profile = site_profile.parse(_weather_document())
    adapter = _FakeAdapter(fail=True)
    weather, warnings = site_profile.fetch_weather(
        profile, _half_hours(), adapter=adapter)
    assert weather == {}
    assert any("contribute no power" in warning for warning in warnings)
    envelope = dict(site_profile.power_envelope(profile, _half_hours(), weather))
    assert set(envelope.values()) == {400.0}   # the import limit alone


def test_real_weather_raises_the_ceiling_above_the_import_limit():
    """The end of the loop: forecast becomes available power becomes headroom."""
    profile = site_profile.parse(_weather_document())
    weather, _ = site_profile.fetch_weather(
        profile, _half_hours(), adapter=_FakeAdapter())
    stamps = _half_hours()
    envelope = dict(site_profile.power_envelope(profile, stamps, weather))
    # 800 W/m2 of irradiance and 11 m/s of wind is a strong hour: the site
    # can draw well beyond its 400 kW import limit.
    assert max(envelope.values()) > 400.0


def test_the_site_ceiling_is_not_the_import_limit():
    """The bug this test exists to prevent, found by running the loop.

    A site importing 1 kW while its own array produces 4 kW can draw 5 kW.
    Compiling the import limit into `max_power_kw` capped the facility at its
    grid connection and silently discarded every kilowatt of on-site
    generation — the whole feature, defeated by one wrong field.
    """
    document = _document()
    document["facility"]["max_import_kw"] = 1
    document["sources"][0]["capacity_kw"] = 4
    profile = site_profile.parse(document)
    assert profile.electrical_limit_kw == 5
    payload = site_profile.to_facility_payload(profile, _half_hours())
    assert payload["max_power_kw"] == 5
    assert max(payload["power_profile_kw"]) > 1


def test_a_declared_switchgear_rating_wins_over_the_derived_one():
    """An operator who knows their rating is a better source than arithmetic."""
    document = _document()
    document["facility"]["electrical_limit_kw"] = 250
    profile = site_profile.parse(document)
    assert profile.electrical_limit_kw == 250
    payload = site_profile.to_facility_payload(profile, _half_hours())
    assert max(payload["power_profile_kw"]) <= 250
