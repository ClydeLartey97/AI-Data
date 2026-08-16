from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from adapters.base_adapter import GridDataPoint
from app import serve
from app.markets import MarketContext, market_locations
from core.grid import Job
from hardware import providers

REDFISH_FIXTURES = Path(__file__).parent / "fixtures" / "redfish"


def _context() -> MarketContext:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return MarketContext(
        "GB", "Great Britain", "london", "London",
        [GridDataPoint(start + timedelta(minutes=30 * i), 100, 50 - i)
         for i in range(48)],
        "GBP", "£", "National price", "London carbon", "test provenance",
        "Historical replay", market_locations("GB"),
    )


@pytest.fixture
def local_server(monkeypatch, tmp_path):
    monkeypatch.setattr(serve, "load_market", lambda *args, **kwargs: _context())
    monkeypatch.setattr(serve.audit_store, "DB_PATH", tmp_path / "audit.sqlite")
    monkeypatch.setattr(serve.evidence_store, "DB_PATH", tmp_path / "evidence.sqlite")
    monkeypatch.setattr(serve.inventory_store, "DB_PATH", tmp_path / "inventory.sqlite")
    monkeypatch.setattr(providers, "DEFAULT_SITE_KEY_PATH", tmp_path / "site-key")
    monkeypatch.setenv("DISCOVERY_CONFIG", str(tmp_path / "discovery.json"))
    monkeypatch.setattr(serve.apple_benchmark, "run_mlx_probe", lambda *args: {
        "operations_per_second": 123456,
        "performance_provenance": "MEASURED",
        "energy_provenance": "UNAVAILABLE",
        "scheduler_profile_created": False,
    })
    handler = serve.make_handler(
        2, Job("test", 1, 1, 4),
        serve._Cache(), serve._Cache(), serve._Cache(), serve._Cache(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _json(request):
    with urllib.request.urlopen(request, timeout=10) as response:
        return response, json.loads(response.read())


def _payload():
    return {
        "workload": {
            "model_key": "llama31-8b", "task": "training",
            "precision": "bf16", "tokens": 1e6, "accelerator_count": 8,
        },
        "planning": {
            "deadline_hours": 12, "cost_weight": 1, "carbon_weight": 0,
        },
        "device_keys": ["h100-sxm"],
    }


def test_health_endpoint_has_security_headers(local_server):
    response, payload = _json(f"{local_server}/api/v1/health")
    assert payload["status"] == "ok"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_mlx_probe_endpoint_is_performance_only(local_server):
    request = urllib.request.Request(
        f"{local_server}/api/v1/evidence/probe",
        data=json.dumps({"matrix_size": 256, "iterations": 5}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    _, payload = _json(request)
    assert payload["probe"]["operations_per_second"] == 123456
    assert payload["probe"]["scheduler_profile_created"] is False


def test_decision_journal_is_served_without_market_fetch(local_server):
    with urllib.request.urlopen(f"{local_server}/decisions", timeout=10) as response:
        page = response.read().decode()
    assert response.status == 200
    assert "Decision journal" in page
    assert "/api/v1/decisions?limit=200" in page


def test_simulator_does_not_wait_for_optional_site_weather(local_server,
                                                            monkeypatch):
    release = threading.Event()

    def slow_sites():
        release.wait(3)
        return {}

    monkeypatch.setattr(serve.simulator, "build_sites", slow_sites)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(f"{local_server}/simulator", timeout=5) as response:
            page = response.read().decode()
        elapsed = time.monotonic() - started
        assert response.status == 200
        assert elapsed < 1.5
        assert "Loading site forecasts" in page
        assert 'fetch("/api/v1/sites")' in page
        _, payload = _json(f"{local_server}/api/v1/sites")
        assert payload["refreshing"] is True
    finally:
        release.set()


def test_plan_endpoint_persists_retrievable_decision(local_server):
    request = urllib.request.Request(
        f"{local_server}/api/v1/plan?market=GB&location=london",
        data=json.dumps(_payload()).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    _, planned = _json(request)
    decision_id = planned["decision_id"]
    _, stored = _json(f"{local_server}/api/v1/decisions/{decision_id}")
    assert stored["decision"]["id"] == decision_id
    assert len(stored["decision"]["signals"]) == 48


def test_plan_endpoint_rejects_wrong_content_type(local_server):
    request = urllib.request.Request(
        f"{local_server}/api/v1/plan",
        data=b"{}", headers={"Content-Type": "text/plain"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 415


def test_portfolio_endpoint_uses_selected_market_context(local_server):
    payload = {
        "facility": {"max_power_kw": 1},
        "jobs": [{
            "job_id": "batch-1",
            "deadline_hours": 2,
            "work_amount": 100,
            "work_unit": "tokens",
            "utility": 1,
            "minimum_quality": 0.8,
            "variants": [{
                "candidate_key": "batch-1-m2",
                "hardware": "Apple M2 GPU",
                "runtime_hours": 0.5,
                "it_power_kw": 1,
                "pue": 1,
                "quality_score": 0.9,
                "quality_provenance": "MEASURED",
                "evaluation_suite": "operator-eval",
                "evaluation_version": "1.0",
                "hardware_provenance": "MEASURED",
            }],
        }],
    }
    request = urllib.request.Request(
        f"{local_server}/api/v1/portfolio?market=GB&location=london",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    _, response = _json(request)
    assert response["market"]["key"] == "GB"
    assert response["market"]["location_name"] == "London"
    assert response["assignments"][0]["job_id"] == "batch-1"


def test_workload_queue_page_is_linked_to_portfolio_api(local_server):
    with urllib.request.urlopen(
        f"{local_server}/workloads?market=GB&location=london", timeout=10
    ) as response:
        page = response.read().decode()
    assert response.status == 200
    assert "AI Data Centre Operations" in page
    assert "Demand, evidence and service state" in page
    assert "/api/v1/portfolio?market=" in page
    assert 'href="/decisions"' in page


def test_home_is_ai_operations_and_grid_is_specialist_view(local_server):
    with urllib.request.urlopen(
        f"{local_server}/?market=GB&location=london", timeout=10
    ) as response:
        home = response.read().decode()
    with urllib.request.urlopen(
        f"{local_server}/grid?market=GB&location=london", timeout=10
    ) as response:
        grid = response.read().decode()
    assert "AI Data Centre Operations" in home
    assert "Operator control plane" in home
    assert "Grid Signal" in grid
    assert ">Sites &amp; Grid</a>" in grid


def _evidence_payload(index: int) -> dict:
    return {"observation": {
        "run_id": f"server-evidence-{index}",
        "workload_class": "language_generation",
        "run_mode": "inference",
        "model_id": "public-reference-model",
        "model_version": "1.0",
        "precision": "int4",
        "device_key": "m2",
        "compute_unit": "gpu",
        "stack_fingerprint": "mlx-0.32.0",
        "shape_fingerprint": "context-128_output-64_batch-1",
        "work_amount": 64,
        "work_unit": "tokens",
        "duration_seconds": 10 + index,
        "it_energy_wh": 0.05 + index * 0.001,
        "peak_memory_mb": 900,
        "thermal_start": "nominal",
        "thermal_end": "nominal",
        "observed_at": (
            datetime(2026, 8, 10, tzinfo=timezone.utc)
            + timedelta(minutes=index)
        ).isoformat(),
        "quality": {
            "metric": "exact_match",
            "value": 0.9,
            "score": 0.9,
            "higher_is_better": True,
            "suite": "operator-eval",
            "suite_version": "1.0",
            "provenance": "MEASURED",
        },
        "energy_method": "apple_powermetrics",
        "energy_scope": "apple_soc_subsystems",
        "energy_provenance": "MEASURED_ESTIMATE",
        "schema_version": "workload-evidence-v1",
        "metadata_only": True,
    }}


def test_evidence_endpoints_build_an_immutable_measured_profile(local_server):
    for index in range(3):
        request = urllib.request.Request(
            f"{local_server}/api/v1/evidence/observations",
            data=json.dumps(_evidence_payload(index)).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response, result = _json(request)
        assert response.status == 201
        assert result["result"]["fingerprint_sample_count"] == index + 1
    _, registry = _json(f"{local_server}/api/v1/evidence/profiles")
    assert registry["summary"]["observation_count"] == 3
    assert registry["summary"]["profile_count"] == 1
    assert registry["profiles"][0]["energy_method"] == "apple_powermetrics"
    assert registry["profiles"][0]["cross_device_comparable"] is False
    assert registry["collectors"][0]["benchmark_id"] == "mlx-language-mcq-v1"
    assert registry["collectors"][0]["status"] == "runner_ready"


def test_telemetry_snapshot_endpoint_reports_occupancy(local_server):
    _, payload = _json(f"{local_server}/api/v1/telemetry")
    telemetry = payload["telemetry"]
    assert telemetry["measurement_scope"] == "occupancy"
    assert telemetry["devices"][0]["id"] == "local-host"


def test_telemetry_stream_pushes_repeated_readings_without_a_reload(local_server):
    """The point of the feature: two readings arrive on one connection."""
    request = urllib.request.Request(
        f"{local_server}/api/v1/telemetry/stream?interval=0.5")
    events, deadline = [], time.monotonic() + 15
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert response.headers["Cache-Control"] == "no-store"
        while len(events) < 2 and time.monotonic() < deadline:
            line = response.readline().decode()
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))

    assert len(events) == 2
    assert events[0]["observed_at"] != events[1]["observed_at"]
    assert all(event["devices"][0]["id"] == "local-host" for event in events)


def test_telemetry_stream_interval_is_clamped(local_server):
    # An absurd interval must not spin the server or hang the client.
    request = urllib.request.Request(
        f"{local_server}/api/v1/telemetry/stream?interval=-99")
    with urllib.request.urlopen(request, timeout=10) as response:
        started = time.monotonic()
        first = second = None
        while second is None and time.monotonic() - started < 8:
            line = response.readline().decode()
            if line.startswith("data: "):
                if first is None:
                    first = time.monotonic()
                else:
                    second = time.monotonic()
    assert second is not None
    assert second - first >= 0.4  # clamped up to the 0.5s floor


class _RedfishFixtureHandler(BaseHTTPRequestHandler):
    """Serve the pruned DMTF mockup tree the way a BMC would: the service
    root anonymous, everything deeper requiring an Authorization header."""

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?")[0]
        if not path.startswith("/redfish/v1"):
            return self._reply(404, b"{}")
        relative = path[len("/redfish/v1"):].strip("/")
        if relative and "Authorization" not in self.headers:
            return self._reply(401, b'{"error": "authentication required"}')
        target = (REDFISH_FIXTURES / relative / "index.json") if relative \
            else (REDFISH_FIXTURES / "index.json")
        if not target.is_file():
            return self._reply(404, b"{}")
        self._reply(200, target.read_bytes())

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def redfish_fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedfishFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_inventory_endpoint_is_honest_when_unconfigured(local_server):
    _, payload = _json(f"{local_server}/api/v1/inventory")
    assert payload["configured"] is False
    assert payload["snapshot"] is None
    assert payload["summary"]["snapshot_count"] == 0


def test_inventory_refresh_without_config_conflicts(local_server):
    request = urllib.request.Request(
        f"{local_server}/api/v1/inventory/refresh",
        data=b"{}", headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _json(request)
    assert excinfo.value.code == 409
    assert "no discovery configuration" in json.loads(excinfo.value.read())["error"]


def test_inventory_refresh_walks_real_loopback_redfish(
        local_server, redfish_fixture_server, tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_SERVER_TEST_CRED", "reader:secret")
    (tmp_path / "discovery.json").write_text(json.dumps({
        "schema": "facility-discovery-v1",
        "endpoints": [{
            "protocol": "redfish", "host": "127.0.0.1",
            "port": redfish_fixture_server, "tls": False,
            "credential_env": "REDFISH_SERVER_TEST_CRED",
        }],
    }), encoding="utf-8")

    request = urllib.request.Request(
        f"{local_server}/api/v1/inventory/refresh",
        data=b"{}", headers={"Content-Type": "application/json"},
        method="POST",
    )
    response, created = _json(request)
    assert response.status == 201
    names = {device["name"] for device in created["snapshot"]["devices"]}
    assert names == {"Contoso 3500", "Stratix 10"}
    assert created["snapshot"]["warnings"] == []

    inventory_response, payload = _json(f"{local_server}/api/v1/inventory")
    assert payload["configured"] is True
    assert payload["snapshot"]["snapshot_id"] == created["snapshot_id"]
    assert payload["summary"]["latest_device_count"] == 2

    # The provider's identifier hygiene must survive the whole HTTP pipe:
    # no raw serial, UUID fragment, SKU, asset tag or credential.
    raw = json.dumps(payload)
    for leak in ("437XR1138R2", "38947555", "8675309",
                 "Chicago-45Z-2381", "secret"):
        assert leak not in raw


def test_pilot_report_endpoint_reports_an_empty_journal_honestly(local_server):
    """A pilot with nothing scored must not serve a saving of zero."""
    _, payload = _json(f"{local_server}/api/v1/pilot-report")
    report = payload["report"]
    assert report["mode"] == "shadow"
    assert report["claimable"]["state"] == "no_measured_result"
    assert report["coverage"]["decisions_recorded"] == 0
    assert report["disclosures"]


def test_pilot_report_aggregates_a_scored_decision_end_to_end(local_server):
    """Plan through the API, score it, then read the aggregate back out."""
    request = urllib.request.Request(
        f"{local_server}/api/v1/plan",
        data=json.dumps(_payload()).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    _, planned = _json(request)
    decision_id = planned["decision_id"]

    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    realised = {"realised_points": [
        {"timestamp": (start + timedelta(minutes=30 * i)).isoformat(),
         "price": 100 + i, "carbon_intensity_g_per_kwh": 50 - i}
        for i in range(48)]}
    score_request = urllib.request.Request(
        f"{local_server}/api/v1/decisions/{decision_id}/score",
        data=json.dumps(realised).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    _, scored = _json(score_request)
    assert scored["score"]["decision_id"] == decision_id

    _, payload = _json(f"{local_server}/api/v1/pilot-report")
    report = payload["report"]
    assert report["coverage"]["decisions_recorded"] == 1
    assert report["coverage"]["decisions_scored"] == 1
    assert report["claimable"]["state"] == "indicative"

    with urllib.request.urlopen(
            f"{local_server}/api/v1/pilot-report?format=text", timeout=10) as response:
        text = response.read().decode()
    assert response.headers["Content-Type"].startswith("text/plain")
    assert "Shadow-mode pilot report" in text
    assert "Disclosures" in text


def test_site_profile_endpoint_is_honest_when_nothing_is_declared(local_server,
                                                                  monkeypatch,
                                                                  tmp_path):
    """Most sites have not declared one. That is a state, not a fault."""
    monkeypatch.setenv("SITE_PROFILE", str(tmp_path / "absent.json"))
    _, payload = _json(f"{local_server}/api/v1/site-profile")
    assert payload["configured"] is False
    assert payload["profile"] is None


def test_site_profile_endpoint_reports_an_invalid_declaration(local_server,
                                                              monkeypatch,
                                                              tmp_path):
    """A broken document must say what is wrong, not fall back to defaults."""
    broken = tmp_path / "site-profile.json"
    broken.write_text(json.dumps({"version": "facility-energy-v9"}))
    monkeypatch.setenv("SITE_PROFILE", str(broken))
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"{local_server}/api/v1/site-profile", timeout=10)
    assert caught.value.code == 422
    assert "unsupported site profile version" in caught.value.read().decode()


def _declaration(**overrides):
    document = {
        "version": "facility-energy-v1",
        "declared_by": "Site engineering",
        "declared_at": "2026-08-16",
        "site": {"site_id": "dc-1", "name": "Site One",
                 "latitude": 51.5074, "longitude": -0.1278,
                 "time_zone": "Europe/London"},
        "market": {"market": "GB", "location": "national"},
        "facility": {"base_load_kw": 0, "pue": 1.0, "max_import_kw": 1},
        "sources": [{
            "source_id": "solar-a", "name": "Rooftop array", "kind": "solar",
            "capacity_kw": 4, "availability_method": "diurnal",
            "peak_hour": 1, "evidence": "nameplate"}],
        "dispatch_priority": "renewable",
    }
    document.update(overrides)
    return document


def _post(url, body):
    return urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")


def test_a_company_can_declare_its_site_and_read_it_back(local_server,
                                                         monkeypatch, tmp_path):
    """The input path: specifications in, validated document out."""
    monkeypatch.setenv("SITE_PROFILE", str(tmp_path / "site-profile.json"))
    response, created = _json(_post(f"{local_server}/api/v1/site-profile",
                                    _declaration()))
    assert response.status == 201
    assert created["profile"]["site"]["site_id"] == "dc-1"

    _, stored = _json(f"{local_server}/api/v1/site-profile")
    assert stored["configured"] is True
    assert stored["profile"]["sources"][0]["capacity_kw"] == 4


def test_an_invalid_declaration_leaves_the_previous_one_in_place(local_server,
                                                                 monkeypatch,
                                                                 tmp_path):
    """A half-applied site declaration would be worse than a rejected one."""
    path = tmp_path / "site-profile.json"
    monkeypatch.setenv("SITE_PROFILE", str(path))
    _json(_post(f"{local_server}/api/v1/site-profile", _declaration()))
    broken = _declaration()
    broken["sources"][0]["capacity_kw"] = "lots"
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(
            _post(f"{local_server}/api/v1/site-profile", broken), timeout=10)
    assert caught.value.code == 400
    _, stored = _json(f"{local_server}/api/v1/site-profile")
    assert stored["profile"]["sources"][0]["capacity_kw"] == 4


def test_a_declared_site_schedules_against_its_own_generation(local_server,
                                                              monkeypatch,
                                                              tmp_path):
    """The whole loop: declaration -> available power -> a placed workload.

    The site imports at most 1 kW, so a 2 kW job can only run while its own
    array is producing. The scheduler has to find that window from the
    declaration alone — nothing about power was sent with the request.
    """
    monkeypatch.setenv("SITE_PROFILE", str(tmp_path / "site-profile.json"))
    _json(_post(f"{local_server}/api/v1/site-profile", _declaration()))

    payload = {
        "use_site_profile": True,
        "jobs": [{
            "job_id": "train", "earliest_delay_hours": 0,
            "deadline_hours": 6, "work_amount": 256, "work_unit": "tokens",
            "workload_class": "language_generation", "run_mode": "inference",
            "utility": 1, "minimum_quality": 0.8, "mandatory": True,
            "variants": [{
                "candidate_key": "train-gpu", "hardware": "Apple M2 GPU",
                "model_id": "reference-language-model", "model_version": "1.0",
                "precision": "int4", "compute_unit": "gpu",
                "memory_required_gb": 2, "memory_available_gb": 8,
                "runtime_hours": 0.5, "it_power_kw": 2, "pue": 1,
                "quality_score": 0.9, "quality_provenance": "MEASURED",
                "evaluation_suite": "operator-eval", "evaluation_version": "1.0",
                "hardware_provenance": "MEASURED"}],
        }],
    }
    _, response = _json(_post(f"{local_server}/api/v1/portfolio", payload))
    assert response["site_profile"]["site_id"] == "dc-1"
    assert response["site_profile"]["peak_available_kw"] > 1
    assert len(response["assignments"]) == 1


def test_a_typed_facility_cannot_silently_override_the_declared_site(
        local_server, monkeypatch, tmp_path):
    """The declaration is authoritative, so a stale browser field must fail."""
    monkeypatch.setenv("SITE_PROFILE", str(tmp_path / "site-profile.json"))
    _json(_post(f"{local_server}/api/v1/site-profile", _declaration()))
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(_post(f"{local_server}/api/v1/portfolio", {
            "use_site_profile": True,
            "facility": {"max_power_kw": 500},
            "jobs": [],
        }), timeout=10)
    assert caught.value.code == 400
    assert "not both" in caught.value.read().decode()
