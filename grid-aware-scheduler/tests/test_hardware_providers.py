from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hardware.providers import (
    LocalDetector,
    RedfishEndpoint,
    RedfishFleetProvider,
    SimulatedFleetProvider,
)

REDFISH_FIXTURES = Path(__file__).parent / "fixtures" / "redfish"
SITE_KEY = b"test-site-key-0123456789abcdef01"


def _runner(payload):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")
    return run


def _mockup_fetcher(counter: list | None = None, *, require_auth: bool = True,
                    fail_hosts: frozenset[str] = frozenset()):
    """Serve the pruned DMTF public-rackmount1 tree the way a BMC would:
    the service root anonymous, everything deeper requiring Authorization."""
    def fetch(endpoint, path, headers):
        if endpoint.host in fail_hosts:
            raise OSError("connection refused")
        if counter is not None:
            counter.append(path)
        relative = path[len("/redfish/v1"):].strip("/")
        if relative and require_auth and "Authorization" not in headers:
            return 401, b'{"error": "authentication required"}'
        target = REDFISH_FIXTURES / relative / "index.json" if relative \
            else REDFISH_FIXTURES / "index.json"
        if not target.exists():
            return 404, b"{}"
        return 200, target.read_bytes()
    return fetch


def _endpoint(**overrides):
    values = dict(host="10.0.10.21", port=443, credential_env="REDFISH_TEST_CRED")
    values.update(overrides)
    return RedfishEndpoint(**values)


def test_apple_detection_keeps_only_non_identifying_hardware_evidence():
    payload = {
        "SPHardwareDataType": [{
            "chip_type": "Apple M2",
            "physical_memory": "8 GB",
            "serial_number": "must-not-leak",
            "platform_UUID": "must-not-leak-either",
        }],
        "SPDisplaysDataType": [{
            "sppci_device_type": "spdisplays_gpu",
            "sppci_cores": "8",
        }],
    }
    result = LocalDetector(runner=_runner(payload), platform_name="Darwin").detect()
    assert len(result.devices) == 1
    device = result.devices[0]
    assert device.catalog_key == "m2"
    assert device.memory_gb == 8
    public = json.dumps(result.public_dict())
    assert "must-not-leak" not in public
    assert device.identity_provenance == "MEASURED"
    assert device.performance_provenance == "ESTIMATED"


def test_simulated_provider_builds_a_catalogue_backed_fleet(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps({
        "devices": [{"catalog_key": "h100-sxm", "count": 8}]
    }), encoding="utf-8")
    result = SimulatedFleetProvider(path).detect()
    assert result.devices[0].identity_provenance == "SIMULATED"
    fleet = result.fleet()
    assert fleet.count == 8
    assert fleet.devices[0].key == "h100-sxm"


def test_simulated_provider_rejects_unknown_hardware(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text('{"devices":[{"catalog_key":"imaginary"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown hardware"):
        SimulatedFleetProvider(path).detect()


def test_redfish_walk_builds_inventory_from_dmtf_mockup(monkeypatch):
    monkeypatch.setenv("REDFISH_TEST_CRED", "reader:secret")
    provider = RedfishFleetProvider(
        [_endpoint()], site_key=SITE_KEY, fetcher=_mockup_fetcher())
    result = provider.detect()

    assert result.provider == "redfish"
    by_name = {device.name: device for device in result.devices}
    system = by_name["Contoso 3500"]
    assert system.memory_gb == 96
    assert system.memory_provenance == "MEASURED"
    assert system.live_power_watts == 344
    assert system.power_provenance == "MEASURED"
    assert system.power_scope == "system"
    assert system.identity_provenance == "MEASURED"
    assert system.performance_provenance == "UNAVAILABLE"
    assert any("GiB" in note for note in system.notes)

    accelerator = by_name["Stratix 10"]
    assert accelerator.catalog_key is None
    assert accelerator.performance_provenance == "UNAVAILABLE"
    assert accelerator.power_provenance == "UNAVAILABLE"

    # CPUs are summarised on the system record, never separate devices.
    assert not any("Xeon" in name for name in by_name)
    assert len(result.devices) == 2


def test_redfish_public_dict_never_contains_raw_identifiers(monkeypatch):
    monkeypatch.setenv("REDFISH_TEST_CRED", "reader:secret")
    provider = RedfishFleetProvider(
        [_endpoint()], site_key=SITE_KEY, fetcher=_mockup_fetcher())
    public = json.dumps(provider.detect().public_dict())

    # Raw values present in the mockup payloads must not survive detection.
    assert "437XR1138R2" not in public          # serial number
    assert "38947555" not in public             # system UUID fragment
    assert "8675309" not in public              # SKU
    assert "Chicago-45Z-2381" not in public     # asset tag
    digests = [device["device_digest"]
               for device in json.loads(public)["devices"]]
    assert all(isinstance(d, str) and len(d) == 64 for d in digests)


def test_redfish_digest_is_stable_per_site_key(monkeypatch):
    monkeypatch.setenv("REDFISH_TEST_CRED", "reader:secret")

    def digests(key):
        provider = RedfishFleetProvider(
            [_endpoint()], site_key=key, fetcher=_mockup_fetcher())
        return [device.device_digest for device in provider.detect().devices]

    assert digests(SITE_KEY) == digests(SITE_KEY)
    assert digests(SITE_KEY) != digests(b"a-different-site-key-entirely!!!")


def test_redfish_without_credentials_stops_at_the_service_root():
    calls: list[str] = []
    provider = RedfishFleetProvider(
        [_endpoint(credential_env=None)], site_key=SITE_KEY,
        fetcher=_mockup_fetcher(calls, require_auth=False))
    result = provider.detect()

    assert calls == ["/redfish/v1/"]
    assert result.devices == []
    assert any("service root" in warning for warning in result.warnings)


def test_redfish_missing_credential_env_fails_closed(monkeypatch):
    monkeypatch.delenv("REDFISH_TEST_CRED", raising=False)
    calls: list[str] = []
    provider = RedfishFleetProvider(
        [_endpoint()], site_key=SITE_KEY,
        fetcher=_mockup_fetcher(calls, require_auth=False))
    result = provider.detect()

    assert calls == ["/redfish/v1/"]
    assert result.devices == []
    assert any("REDFISH_TEST_CRED" in warning for warning in result.warnings)
    assert not any("secret" in warning for warning in result.warnings)


def test_redfish_rejects_plain_http_off_loopback():
    with pytest.raises(ValueError, match="loopback"):
        RedfishEndpoint(host="10.0.10.21", tls=False)
    RedfishEndpoint(host="127.0.0.1", tls=False)  # loopback is allowed


def test_redfish_request_budget_fails_closed_with_partial_result(monkeypatch):
    monkeypatch.setenv("REDFISH_TEST_CRED", "reader:secret")
    calls: list[str] = []
    provider = RedfishFleetProvider(
        [_endpoint()], site_key=SITE_KEY, fetcher=_mockup_fetcher(calls),
        max_requests_per_endpoint=3)
    result = provider.detect()

    assert len(calls) == 3
    assert any("budget" in warning for warning in result.warnings)


def test_redfish_endpoint_failure_yields_partial_inventory(monkeypatch):
    monkeypatch.setenv("REDFISH_TEST_CRED", "reader:secret")
    provider = RedfishFleetProvider(
        [_endpoint(host="10.0.10.20"), _endpoint(host="10.0.10.21")],
        site_key=SITE_KEY,
        fetcher=_mockup_fetcher(fail_hosts=frozenset({"10.0.10.20"})))
    result = provider.detect()

    assert any("10.0.10.20" in warning for warning in result.warnings)
    assert {device.name for device in result.devices} == {"Contoso 3500", "Stratix 10"}


def test_redfish_config_loader_validates_protocols(tmp_path, monkeypatch):
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps({
        "schema": "facility-discovery-v1",
        "endpoints": [
            {"protocol": "redfish", "host": "10.0.10.21",
             "credential_env": "REDFISH_TEST_CRED"},
            {"protocol": "snmp", "host": "10.0.10.30"},
        ],
    }), encoding="utf-8")
    provider = RedfishFleetProvider.from_config(path, site_key=SITE_KEY)
    assert [endpoint.host for endpoint in provider.endpoints] == ["10.0.10.21"]

    path.write_text(json.dumps({
        "schema": "facility-discovery-v1",
        "endpoints": [{"protocol": "redfsh", "host": "10.0.10.21"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown discovery protocol"):
        RedfishFleetProvider.from_config(path, site_key=SITE_KEY)
