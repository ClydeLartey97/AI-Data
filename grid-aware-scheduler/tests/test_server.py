from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from adapters.base_adapter import GridDataPoint
from app import serve
from app.markets import MarketContext, market_locations
from core.grid import Job


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
