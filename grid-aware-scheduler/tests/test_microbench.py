from __future__ import annotations

import pytest

from hardware import microbench


def test_timing_forces_evaluation_once_per_iteration_and_discards_warmup():
    """The laziness guard. Without it a lazy framework reports absurd rates."""
    built, evaluated = [], []

    def operation():
        built.append(1)
        return "graph"

    def evaluate(value):
        assert value == "graph"
        evaluated.append(1)

    samples = microbench._time_iterations(operation, evaluate,
                                          iterations=4, warmup=2)
    assert len(samples) == 4              # only measured iterations returned
    assert len(built) == len(evaluated) == 6   # warm-up ran but was not timed
    assert all(sample >= 0 for sample in samples)


def test_relative_mad_reports_spread_and_tolerates_a_single_sample():
    assert microbench._relative_mad([10.0], 10.0) == 0.0
    assert microbench._relative_mad([10.0, 10.0, 10.0], 10.0) == 0.0
    spread = microbench._relative_mad([9.0, 10.0, 11.0], 10.0)
    assert spread > 0
    # A non-positive centre must not divide by zero.
    assert microbench._relative_mad([1.0, 2.0], 0.0) == 0.0


def test_missing_mlx_is_recorded_as_a_warning_not_a_fabricated_number(monkeypatch):
    def unavailable(*args, **kwargs):
        raise microbench.MLXUnavailable("MLX is not installed")

    for name in ("gemm", "gemv", "quantized_gemv", "memory_bandwidth"):
        monkeypatch.setattr(microbench, name, unavailable)
    report = microbench.run(sizes=(512,), dtypes=("float32",), skip_preflight=True)

    # Every attempted measurement must be accounted for by a warning, and
    # none of them may leave behind a fabricated number.
    assert report.measurements == []
    assert report.warnings
    assert all("not installed" in warning for warning in report.warnings)
    assert report.peak("gemm") is None
    assert report.peak("gemv") is None


def test_report_peak_selects_the_highest_rate():
    report = microbench.MicrobenchReport(device="Apple M2", observed_at="now",
                                         stack="test")
    for size, rate in ((512, 371.6), (2048, 2287.3), (1024, 684.7)):
        report.measurements.append(microbench.Measurement(
            name="gemm", dtype="float32", size=size, iterations=5,
            seconds_median=0.01, seconds_relative_mad=0.01,
            rate=rate, rate_unit="GFLOP/s"))
    assert report.peak("gemm").size == 2048
    assert report.peak("memory_bandwidth") is None


def test_run_is_blocked_by_an_invalid_host(monkeypatch):
    monkeypatch.setattr(microbench.preflight, "check", lambda **kwargs:
                        microbench.preflight.Preflight(
                            valid=False, blockers=["6.79 GB of swap already in use"]))
    with pytest.raises(RuntimeError, match="swap"):
        microbench.run(sizes=(512,), dtypes=("float32",))


def test_gemm_rate_is_derived_from_two_n_cubed_flops():
    mx = pytest.importorskip("mlx.core")
    result = microbench.gemm(256, "float32", iterations=2, warmup=1)
    expected = 2.0 * 256 ** 3 / result.seconds_median / 1e9
    assert result.rate == pytest.approx(expected)
    assert result.rate_unit == "GFLOP/s"
    # A real device cannot exceed a physically absurd rate; catches a
    # regression where evaluation is no longer forced.
    assert result.rate < 100_000
