from __future__ import annotations

import subprocess

import pytest

from hardware import preflight


class _Collector:
    def __init__(self, live: dict, battery: dict | None = None) -> None:
        self._live = live
        self._battery = battery or {"available": False}

    def snapshot(self) -> dict:
        return {"devices": [{"name": "Apple M2", "static": {"memory_total_gb": 8.0},
                             "live": self._live}]}

    def battery(self) -> dict:
        return self._battery


def _runner(pmset_output: str = "", therm_output: str = "no thermal warning level has been recorded"):
    def run(args, **kwargs):
        text = therm_output if "therm" in args else pmset_output
        return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")
    return run


HEALTHY = {"memory_available_gb": 6.0, "swap_used_gb": 0.0,
           "cpu_percent": 3.0, "gpu_percent": 1.0, "storage_free_gb": 80.0}


def test_healthy_host_is_allowed_to_measure():
    result = preflight.check(collector=_Collector(HEALTHY), runner=_runner())
    assert result.valid
    assert result.blockers == []
    result.raise_if_invalid()  # must not raise


def test_memory_pressure_blocks_the_run():
    live = dict(HEALTHY, memory_available_gb=1.2, swap_used_gb=6.8)
    result = preflight.check(collector=_Collector(live), runner=_runner())
    assert not result.valid
    assert any("memory free" in blocker for blocker in result.blockers)
    assert any("swap" in blocker for blocker in result.blockers)
    with pytest.raises(RuntimeError, match="valid measurement"):
        result.raise_if_invalid()


def test_a_busy_accelerator_invalidates_the_comparison():
    result = preflight.check(collector=_Collector(dict(HEALTHY, gpu_percent=64.0)),
                             runner=_runner())
    assert not result.valid
    assert any("GPU already" in blocker for blocker in result.blockers)


def test_low_power_mode_blocks_because_it_caps_performance():
    result = preflight.check(collector=_Collector(HEALTHY),
                             runner=_runner(pmset_output=" lowpowermode         1"))
    assert not result.valid
    assert any("low power mode" in blocker for blocker in result.blockers)


def test_recorded_thermal_warning_blocks_the_run():
    result = preflight.check(
        collector=_Collector(HEALTHY),
        runner=_runner(therm_output="Thermal pressure warning level 40"))
    assert not result.valid
    assert any("thermal" in blocker for blocker in result.blockers)


def test_energy_integration_requires_a_discharging_battery():
    charging = {"available": True, "on_ac_power": True, "is_charging": True,
                "energy_integration_possible": False}
    result = preflight.check(collector=_Collector(HEALTHY, charging),
                             runner=_runner(), require_discharging=True)
    assert not result.valid
    assert any("discharging" in blocker for blocker in result.blockers)

    discharging = {"available": True, "on_ac_power": False, "is_charging": False,
                   "energy_integration_possible": True}
    ok = preflight.check(collector=_Collector(HEALTHY, discharging),
                         runner=_runner(), require_discharging=True)
    assert ok.valid


def test_battery_power_is_a_caution_for_throughput_runs():
    battery = {"available": True, "on_ac_power": False, "is_charging": False,
               "energy_integration_possible": True}
    result = preflight.check(collector=_Collector(HEALTHY, battery),
                             runner=_runner())
    assert result.valid  # allowed, but the caller is told
    assert any("not comparable" in caution for caution in result.cautions)


def test_context_records_conditions_even_when_blocked():
    live = dict(HEALTHY, memory_available_gb=0.5)
    result = preflight.check(collector=_Collector(live), runner=_runner())
    assert not result.valid
    # A refused run must still be explainable after the fact.
    assert result.context["memory_available_gb"] == 0.5
    assert result.context["power_source"] == "unknown"
    assert result.context["captured_at"]
