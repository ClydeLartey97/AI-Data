from __future__ import annotations

from hardware.campaign import Phase, Sample


def _phase(rates: list[float], unit: str = "GFLOP/s") -> Phase:
    phase = Phase(name="p", operation="gemm", dtype="float16", size=2048,
                  started_at="2026-08-11T23:00:00+00:00")
    for index, rate in enumerate(rates):
        phase.samples.append(Sample(elapsed_seconds=index * 10.0, rate=rate,
                                    rate_unit=unit))
    return phase


def test_retention_never_exceeds_one_hundred_percent():
    """A still-warming first sample must not understate peak."""
    summary = _phase([1000, 2500, 2500, 2500, 2450, 2450]).summarise()
    assert summary["peak"] == 2500
    assert summary["retention_percent"] <= 100.0


def test_a_throttling_device_shows_the_drop_and_when_it_happened():
    # Full rate for five samples, then a sustained fall to 60%.
    rates = [1000] * 5 + [600] * 10
    summary = _phase(rates).summarise()
    assert summary["peak"] == 1000
    assert summary["steady_state"] == 600
    assert summary["retention_percent"] == 60.0
    assert summary["time_to_90_percent_seconds"] == 50.0


def test_a_momentary_dip_is_not_reported_as_throttling():
    # One low sample surrounded by full rate must not count as the onset.
    rates = [1000, 1000, 500, 1000, 1000, 1000, 1000, 1000]
    summary = _phase(rates).summarise()
    assert summary["time_to_90_percent_seconds"] is None
    assert summary["retention_percent"] == 100.0


def test_a_device_that_holds_its_rate_reports_no_throttle():
    summary = _phase([900, 905, 898, 902, 899, 901]).summarise()
    assert summary["time_to_90_percent_seconds"] is None
    assert 95 <= summary["retention_percent"] <= 100


def test_empty_phase_summarises_without_raising():
    summary = Phase(name="p", operation="gemm", dtype="float16", size=1,
                    started_at="now").summarise()
    assert summary["samples"] == 0
    assert "peak" not in summary


def test_summary_carries_the_unit_so_rates_are_never_bare_numbers():
    summary = _phase([50.0, 49.0, 48.0], unit="GB/s effective").summarise()
    assert summary["rate_unit"] == "GB/s effective"
    assert summary["operation"] == "gemm"
