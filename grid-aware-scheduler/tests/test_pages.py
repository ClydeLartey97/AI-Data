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
from app.chart import CHART_CSS, Band, ChartSeries, chart
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


def _market_context(market: str = "GB"):
    from app.markets import MarketContext, market_locations
    is_us = market in ("CAISO", "NYISO")
    is_ny = market == "NYISO"
    return MarketContext(
        market_key=market,
        market_name=("New York ISO" if is_ny else
                     "California ISO" if is_us else "Great Britain"),
        location_key="nyc" if is_ny else "sp15" if is_us else "national",
        location_name="New York City zone" if is_ny else "SP15 trading hub" if is_us else "GB national",
        series=_fake_series(96),
        currency="USD" if is_us else "GBP",
        symbol="$" if is_us else "£",
        price_label="Day-ahead zonal LBMP" if is_ny else "Day-ahead LMP" if is_us else "National day-ahead price",
        carbon_label="NYISO balancing-area estimate" if is_ny else "CAISO balancing-area estimate" if is_us else "National carbon",
        provenance="test provenance",
        signal_mode="Historical replay",
        locations=market_locations(market),
        allows_custom_node=is_us,
    )


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


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_planner_javascript_parses():
    from app.planner import render
    html = render(_market_context())
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        ok, err = _check_js(block)
        assert ok, f"planner script block {i} invalid:\n{err}"
    for token in ("Save audited decision", "Copy review link", "Plan JSON",
                  "Alternatives CSV", "grid-aware-plan-v1", "placement-plan.json",
                  'fetch("/api/v1/plan?market="', "Hard cost cap",
                  "Hard carbon cap", "Latest start delay"):
        assert token in html


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_decision_journal_javascript_parses():
    from app.decisions import render
    html = render()
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        ok, err = _check_js(block)
        assert ok, f"decision journal script block {i} invalid:\n{err}"
    for token in ("Decision journal", "/api/v1/decisions?limit=200",
                  "Awaiting outturn", "Download JSON"):
        assert token in html


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_workload_queue_javascript_parses():
    from app.workloads import render
    html = render(_market_context())
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        ok, err = _check_js(block)
        assert ok, f"workload queue script block {i} invalid:\n{err}"
    for token in (
        "AI Data Centre Operations", "Operator control plane",
        "Demand, evidence and service state", "/api/v1/portfolio?market=",
        "Total facility capacity", "Operator utility", "Minimum quality",
        "Estimated scenario", "Plan JSON", "Schedule CSV", "Sites &amp; Grid",
        "Generation-aware AI training", "Physical energy supply",
        "Solar", "Wind", "Hydro", "Nuclear", "Geothermal", "Biomass",
        "Gas", "Coal", "Oil", "Battery capacity", "Renewable match",
        "data-inspector", "Click to expand", "Exact physical facility",
        "Origin latitude", "Origin longitude", "Delivery loss", "Connection ID",
        "Price: GB national", "Carbon: GB national", "30 / 30 min",
        "Governed evidence profile", "Governed execution profiles",
        "Compare every compatible governed profile",
        "/api/v1/evidence/profiles", "/api/v1/evidence/probe",
        "Immutable observations", "Runner ready", "Verify local MLX",
    ):
        assert token in html
    assert "__MARKET" not in html


def test_operations_accepts_an_exact_caiso_pricing_node():
    from app.workloads import render
    html = render(_market_context("CAISO"))
    assert "Custom CAISO pricing node" in html
    assert 'id="customNode"' in html
    assert 'id="loadNode"' in html
    assert "Price: Pricing node" in html
    assert "Carbon: CAISO balancing area" in html
    assert "30 / 60 min" in html


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
                  'data-type="candle"', 'data-ov="ma"', 'data-ov="spread"',
                  'data-act="expand"', "ch-full-close", "ch-full",
                  'data-act="grid"', 'data-act="cross"', 'data-act="y-in"',
                  'data-act="y-out"', "ch-latest-line", "ch-ybadge",
                  "ch-xbadge", "Shift-wheel zooms Y"):
        assert token in html, f"missing {token}"
    assert ".ch svg[hidden]" in CHART_CSS


def test_chart_handles_an_empty_series():
    empty = ChartSeries("x", "X", "u", [], "--price", 0)
    assert "No data" in chart(empty)


def test_dashboard_renders_without_regional_data():
    """The page must survive the regional API being unreachable."""
    from app.dashboard import render
    html = render(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP")
    assert "<title>" in html and "Grid Signal" in html


def test_pages_are_linked_and_compact_panels_are_expandable():
    from app.dashboard import render as render_dashboard
    from app.decisions import render as render_decisions
    from app.planner import render as render_planner
    from app.simulator import device_specs, model_specs, render as render_simulator
    from app.workloads import render as render_workloads

    pages = [
        render_dashboard(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP"),
        render_simulator(device_specs(), model_specs(), {"ok": False}, {}),
        render_planner(_market_context()),
        render_workloads(_market_context()),
        render_decisions(),
    ]
    for page in pages:
        assert ">Operations</a>" in page
        assert 'href="/simulator"' in page or 'href="/simulator?' in page
        assert 'href="/planner"' in page or 'href="/planner?' in page
        assert 'href="/grid"' in page or 'href="/grid?' in page
        assert 'href="/decisions"' in page

    for page in (pages[0], pages[1], pages[2]):
        for token in ("pnl-close", "aria-expanded", 'event.key === "Tab"',
                      'event.key === "Escape"', "aria-haspopup", "pnl-open",
                      'document.body.appendChild(panel)',
                      'placeholder.parentNode.insertBefore(closing,placeholder)'):
            assert token in page


def test_compact_charts_open_as_manipulable_workbenches():
    from app.dashboard import render
    page = render(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP")
    assert page.count('data-inspector="') == 7
    for token in ("pnl-workbench", "Zoom X in", "Zoom Y in", "Toggle grid",
                  "Toggle crosshair", "logarithmic Y axis", "Show exact values",
                  "Download data", "Download current chart view", "shiftKey",
                  "setPointerCapture", "ResizeObserver", "pnl-tooltip",
                  ".pnl:not(.pnl-open) > .pnl-workbench"):
        assert token in page, f"compact inspector missing {token}"


def test_planner_and_simulator_charts_use_the_same_inspection_contract():
    from app.planner import render as render_planner
    from app.simulator import device_specs, model_specs, render as render_simulator
    planner = render_planner(_market_context())
    simulator = render_simulator(device_specs(), model_specs(), {"ok": False}, {})
    for token in ('svg.dataset.inspector=JSON.stringify', 'pointLabel:"Placement"',
                  'name:"Pareto frontier"'):
        assert token in planner
    for token in ('data-inspector=\'{"kind":"diagram"}\'',
                  'name:"Renewable generation"', "pnl-diagram-stage", "Allocation path"):
        assert token in simulator


def test_every_page_has_the_shared_persistent_theme_switch():
    from app.dashboard import render as render_dashboard
    from app.decisions import render as render_decisions
    from app.planner import render as render_planner
    from app.simulator import device_specs, model_specs, render as render_simulator
    from app.workloads import render as render_workloads
    pages = [
        render_dashboard(_fake_series(), Job("t", 6.5, 8, 48), "GB", "GBP"),
        render_simulator(device_specs(), model_specs(), {"ok": False}, {}),
        render_planner(_market_context()),
        render_workloads(_market_context()),
        render_decisions(),
    ]
    for page in pages:
        assert 'id="themeToggle"' in page
        assert 'role="switch"' in page
        assert "grid-aware-theme" in page
        assert 'dataset.theme' in page


def test_us_dashboard_uses_us_currency_and_truthful_carbon_scope():
    from app.dashboard import render
    context = _market_context("CAISO")
    page = render(context.series, Job("t", 6.5, 8, 48), context.market_name,
                  context.currency, context=context)
    assert "$50.00" in page
    assert "CAISO balancing-area estimate" in page
    assert "Custom PNode" in page


def test_simulator_exposes_facility_and_memory_controls():
    from app.simulator import device_specs, model_specs, render
    page = render(device_specs(), model_specs(), {"ok": False}, {})
    for token in ("Facility PUE", "Measured system efficiency", "Context length",
                  "Concurrent sequences", "FSDP / ZeRO-3", "function kvGB",
                  "Training state bytes / parameter", "KV cache precision",
                  'fetch("/api/v1/sites")', "Loading site forecasts"):
        assert token in page


def test_simulator_accepts_us_market_context():
    from app.markets import summarise_market
    from app.simulator import device_specs, model_specs, render
    grid = summarise_market(_market_context("CAISO"))
    page = render(device_specs(), model_specs(), grid, {})
    for token in ("California ISO", "SP15 trading hub", "Custom CAISO PNode",
                  'var SYMBOL = GRID.symbol || "£"'):
        assert token in page


def test_new_york_is_a_distinct_us_market_with_zonal_controls():
    from app.dashboard import render as render_dashboard
    from app.markets import summarise_market
    from app.planner import render as render_planner
    from app.simulator import device_specs, model_specs, render as render_simulator
    context = _market_context("NYISO")
    pages = [
        render_dashboard(context.series, Job("t", 6.5, 8, 48),
                         context.market_name, context.currency, context=context),
        render_simulator(device_specs(), model_specs(), summarise_market(context), {}),
        render_planner(context),
    ]
    for page in pages:
        assert "New York ISO" in page
        assert 'value="NYISO" selected' in page
        assert "New York City" in page


def test_market_summary_requires_contiguous_windows():
    from app.markets import summarise_market
    context = _market_context()
    context.series[2].timestamp += timedelta(hours=5)
    context.series = context.series[:8]
    summary = summarise_market(context, window_hours=4)
    assert summary["ok"] is False


def test_simulator_keeps_detected_memory_separate_from_estimated_performance():
    from app.simulator import device_specs, model_specs, render
    detected = {"devices": [{
        "name": "Apple M2", "catalog_key": "m2", "memory_gb": 8,
        "memory_provenance": "MEASURED",
        "performance_provenance": "ESTIMATED",
    }]}
    page = render(device_specs(), model_specs(), {"ok": False}, {}, detected)
    assert "Detected locally" in page
    assert "8 GB memory measured" in page
    assert 'device:"m2"' in page
    assert '"mem": 8' in page


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
