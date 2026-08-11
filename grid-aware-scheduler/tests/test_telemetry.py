from __future__ import annotations

import subprocess

from hardware.telemetry import TelemetryCollector

IOREG_OUTPUT = """
  +-o IOAccelerator  <class AGXAccelerator>
      "PerformanceStatistics" = {"Device Utilization %"=37,"Renderer Utilization %"=35,
      "Tiler Utilization %"=12,"In use system memory"=292372480,
      "Alloc system memory"=4157243392,"recoveryCount"=0}
"""

NVIDIA_OUTPUT = "0, NVIDIA H100 PCIe, 63, 41000, 81559, 310.42, 58\n"


def _runner(outputs: dict):
    def run(args, **kwargs):
        for fragment, payload in outputs.items():
            if fragment in args[0]:
                return subprocess.CompletedProcess(args, 0, stdout=payload, stderr="")
        raise FileNotFoundError(args[0])
    return run


def test_apple_gpu_occupancy_is_parsed_and_converted(monkeypatch):
    collector = TelemetryCollector(runner=_runner({"ioreg": IOREG_OUTPUT}),
                                   platform_name="Darwin")
    live, warnings = collector._apple_gpu()
    assert warnings == []
    assert live["gpu_percent"] == 37
    assert live["gpu_renderer_percent"] == 35
    # Bytes are converted to GB, never reported raw.
    assert live["gpu_memory_in_use_gb"] == 0.27
    assert live["gpu_memory_allocated_gb"] == 3.87
    assert "gpu_memory_in_use_bytes" not in live


def test_missing_gpu_source_warns_instead_of_inventing_a_value():
    def failing(args, **kwargs):
        raise FileNotFoundError("ioreg")

    collector = TelemetryCollector(runner=failing, platform_name="Darwin")
    live, warnings = collector._apple_gpu()
    assert live == {}
    assert warnings and "unavailable" in warnings[0]


def test_snapshot_reports_occupancy_scope_and_never_claims_performance(monkeypatch):
    collector = TelemetryCollector(runner=_runner({"ioreg": IOREG_OUTPUT}),
                                   platform_name="Darwin")
    monkeypatch.setattr(collector, "_identity", None)
    snapshot = collector.snapshot()

    assert snapshot["measurement_scope"] == "occupancy"
    host = snapshot["devices"][0]
    assert host["id"] == "local-host"
    # Live occupancy must never be presented as calibrated performance.
    assert any("uncalibrated" in note for note in host["notes"])
    assert "throughput" not in host["live"]
    assert "tokens_per_second" not in host["live"]


def test_nvidia_live_values_are_split_by_field():
    collector = TelemetryCollector(runner=_runner({"nvidia-smi": NVIDIA_OUTPUT}),
                                   platform_name="Linux")
    devices, warnings = collector._nvidia() if __import__("shutil").which("nvidia-smi") \
        else ([], [])
    if not devices:  # no NVIDIA hardware on the development machine
        assert warnings == []
        return
    device = devices[0]
    assert device["live"]["gpu_percent"] == 63
    assert device["live"]["power_watts"] == 310.42


def test_snapshot_is_json_safe_and_free_of_host_identifiers():
    import json

    payload = json.dumps(TelemetryCollector().snapshot(), allow_nan=False)
    import getpass
    import socket

    assert socket.gethostname() not in payload
    assert getpass.getuser() not in payload
