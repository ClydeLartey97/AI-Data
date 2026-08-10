from __future__ import annotations

import json
import subprocess

import pytest

from hardware.providers import LocalDetector, SimulatedFleetProvider


def _runner(payload):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")
    return run


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
