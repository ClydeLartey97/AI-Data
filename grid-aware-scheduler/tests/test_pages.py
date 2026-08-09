"""
Page-generation tests — catch broken output before a browser does.

These exist because of a real failure. A patch emitted a raw newline inside a
JavaScript string literal (``join("<newline>")``), which is a syntax error that
kills the entire script block. Both charts on both pages rendered as empty
boxes. Grepping the HTML for the new code found it present and looking correct,
because the *text* was fine — only the browser's parser disagreed.

So the rule these encode: generated JavaScript must be parsed, not grepped.
``node --check`` is the arbiter. If node is unavailable the syntax tests skip
rather than fail, since it is a dev convenience and not a runtime dependency.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adapters.base_adapter import GridDataPoint
from app.chart import Band, ChartSeries, chart
from core.grid import Job

NODE = shutil.which("node")
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _fake_series(n: int = 400):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [
        GridDataPoint(timestamp=start + timedelta(minutes=30 * i),
                      carbon_intensity=100 + (i % 60),
                      price=50 + (i % 90))
        for i in range(n)
    ]


def _chart_html() -> str:
    pts = _fake_series()
    series = ChartSeries(
        "carbon", "Carbon intensity", "gCO₂/kWh",
        [(p.timestamp, p.carbon_intensity) for p in pts], "--carbon", 0,
        bands=[Band(pts[10].timestamp, pts[18].timestamp, "window")],
        now=pts[200].timestamp,
    )
    return chart(series, height=300, default_range="1W")


def _check_js(source: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(source)
        path = fh.name
    try:
        proc = subprocess.run([NODE, "--check", path],
                              capture_output=True, text=True, timeout=30)
        return proc.returncode == 0, (proc.stderr or "").strip()
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_chart_javascript_parses():
    """The regression that shipped: a syntax error blanked every chart."""
    html = _chart_html()
    blocks = SCRIPT_RE.findall(html)
    assert blocks, "chart emitted no script block"
    for i, block in enumerate(blocks):
        ok, err = _check_js(block)
        assert ok, f"script block {i} is not valid JavaScript:\n{err}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_dashboard_javascript_parses():
    from app.dashboard import render
    html = render(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP")
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        ok, err = _check_js(block)
        assert ok, f"dashboard script block {i} invalid:\n{err}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_simulator_javascript_parses():
    from app.simulator import device_specs, model_specs, render
    html = render(device_specs(), model_specs(), {"ok": False}, {})
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        ok, err = _check_js(block)
        assert ok, f"simulator script block {i} invalid:\n{err}"


# --- structural checks that need no browser ------------------------------

def test_chart_ships_native_series_not_prebucketed():
    """Bucketing moved to the client so interval and range are independent.

    Server-side bucketing per range forced them to be the same thing, which is
    why candles came out flat at the native range: the bucket was already one
    reading, so open, high, low and close were identical.
    """
    html = _chart_html()
    assert '"t":' in html and '"v":' in html, "native points missing"
    assert '"o":' not in html, "server should not pre-bucket into OHLC"
    for key in ("1D", "1W", "1M"):
        assert f'data-r="{key}"' in html
    for iv in ("1800", "3600", "86400"):
        assert f'data-iv="{iv}"' in html, f"interval {iv} control missing"


def test_chart_ships_interaction_controls():
    html = _chart_html()
    for token in ('data-act="reset"', 'data-act="csv"', 'data-act="zone"',
                  "ch-sel", "pointerdown", "wheel", "dblclick", "keydown",
                  'data-type="candle"', 'data-ov="ma"', 'data-ov="spread"'):
        assert token in html, f"missing {token}"


def test_chart_handles_an_empty_series():
    empty = ChartSeries("x", "X", "u", [], "--price", 0)
    assert "No data" in chart(empty)


def test_dashboard_renders_without_regional_data():
    """The page must survive the regional API being unreachable."""
    from app.dashboard import render
    html = render(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP")
    assert "<title>" in html and "Grid Signal" in html


def test_no_unsubstituted_placeholders():
    """A missed __TOKEN__ is a valid JS identifier.

    node --check accepts it, so the failure only shows up in a browser as
    "__HSUB__ is not defined" — which silently blanks the chart. This is
    exactly how the second blank-chart regression shipped.
    """
    html = _chart_html()
    assert not re.findall(r"__[A-Z_]+__", html), "placeholder left in output"


def test_candles_are_not_flat_at_native_resolution():
    """Interval must be independent of range.

    When the two were conflated, selecting a short range made each bucket one
    reading, so open == high == low == close and every candle rendered as a
    flat dash. The fix is client-side bucketing, so the page must ship native
    points plus interval controls rather than pre-bucketed OHLC.
    """
    html = _chart_html()
    assert '"o":' not in html and '"h":' not in html
    assert 'data-iv="21600"' in html
