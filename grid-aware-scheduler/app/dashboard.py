"""
Grid signal dashboard — renders a self-contained HTML page from live market data.

Deliberately not Streamlit. Streamlit imposes a strong visual identity that
cannot be reshaped into a native-feeling interface without CSS overrides that
break on version bumps. Here the engine stays in Python and the front-end is
plain HTML/CSS/SVG we own outright: no framework, no CDN, no build step. The
output is one file that opens in any browser.

Every figure on the page carries its provenance. ``MEASURED`` means it came off
a live API; ``SIMULATED`` would mean it came from a config file. That
distinction costs nothing to carry and is what separates an honest simulator
from a screenshot that misleads.

Usage:
    ~/venvs/national-grid/bin/python -m app.dashboard [--days 3] [--open]
"""
from __future__ import annotations

import argparse
import html
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from adapters.base_adapter import GridDataPoint
from adapters.gb_regional import GBRegionalAdapter
from core import analytics, feed
from app.panels import (EXPAND_JS, PANEL_CSS, duration_panel, profile_panel,
                        savings_panel, scatter_panel)
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS
from app.chart import CHART_CSS, Band, ChartSeries, chart
from core.grid import Job, cheapest_window, cleanest_window, compare, run_immediately

if TYPE_CHECKING:
    from app.markets import MarketContext

OUT = Path(__file__).resolve().parent / "build" / "grid_dashboard.html"

# Palette — Apple system colors, snapped to steps that pass the data-viz
# validator in BOTH modes (lightness band, chroma floor, CVD separation,
# normal-vision floor, contrast vs surface). Do not hand-edit these without
# re-running the validator: the dark steps are deliberately darker than
# Apple's own dark system colors, which sit above the dark lightness band.
CARBON_LIGHT, CARBON_DARK = "#248A3D", "#2A9D48"   # green
PRICE_LIGHT, PRICE_DARK = "#007AFF", "#0A84FF"     # blue


# --------------------------------------------------------------------------
# chart rendering
# --------------------------------------------------------------------------

def _svg_chart(
    series: list[GridDataPoint],
    values: list[float | None],
    *,
    unit: str,
    label: str,
    var: str,
    highlight: tuple[int, int] | None,
) -> str:
    """One single-series area+line chart, as inline SVG.

    Single series by design: carbon and price are different measures on
    different scales, so they get their own charts rather than a dual axis —
    the single most common way a chart like this goes wrong.
    """
    w, h = 1000.0, 260.0
    pad_l, pad_r, pad_t, pad_b = 8.0, 8.0, 22.0, 26.0
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pts:
        return '<p class="empty">No data for this window.</p>'

    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    span = (hi - lo) or 1.0
    lo_pad, hi_pad = lo - span * 0.12, hi + span * 0.12
    span_pad = hi_pad - lo_pad

    n = len(values)
    def x(i: float) -> float:
        return pad_l + (w - pad_l - pad_r) * (i / max(n - 1, 1))
    def y(v: float) -> float:
        return pad_t + (h - pad_t - pad_b) * (1 - (v - lo_pad) / span_pad)

    line = " ".join(f"{'M' if k == 0 else 'L'}{x(i):.1f},{y(v):.1f}"
                    for k, (i, v) in enumerate(pts))
    area = (f"M{x(pts[0][0]):.1f},{y(lo_pad):.1f} "
            + " ".join(f"L{x(i):.1f},{y(v):.1f}" for i, v in pts)
            + f" L{x(pts[-1][0]):.1f},{y(lo_pad):.1f} Z")

    band = ""
    if highlight:
        a, b = highlight
        band = (f'<rect class="band" x="{x(a):.1f}" y="{pad_t - 6:.1f}" '
                f'width="{max(x(b) - x(a), 2):.1f}" height="{h - pad_t - pad_b + 12:.1f}" '
                f'rx="6"/>')

    # Gridlines + value labels at the REAL min / mid / max of the data — never
    # at the padded bounds. Labelling the padding invents values the series
    # never takes (it put "-16" under a price series whose floor was £2.84).
    grid, ticks = [], []
    for v in (lo, (lo + hi) / 2, hi):
        yy = y(v)
        grid.append(f'<line class="grid" x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}"/>')
        ticks.append(f'<text class="tick" x="{pad_l + 2}" y="{yy - 5:.1f}">{v:,.0f}</text>')

    # Time axis: one label per day boundary, plus first and last.
    time_labels = []
    for i, p in enumerate(series):
        if p.timestamp.hour == 0 and p.timestamp.minute == 0:
            time_labels.append(
                f'<text class="tlab" x="{x(i):.1f}" y="{h - 6:.1f}" text-anchor="middle">'
                f'{html.escape(p.timestamp.strftime("%a %d"))}</text>')

    payload = ";".join(
        f"{x(i):.1f},{y(v):.1f},{html.escape(series[i].timestamp.strftime('%a %d %b %H:%M'))},{v:.2f}"
        for i, v in pts)

    return f"""
<figure class="chart" style="--series: var({var});">
  <figcaption class="sr-only">{html.escape(label)}, {html.escape(unit)}</figcaption>
  <svg viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" role="img"
       aria-label="{html.escape(label)} over time, in {html.escape(unit)}"
       data-points="{payload}">
    {''.join(grid)}
    {band}
    <path class="area" d="{area}"/>
    <path class="line" d="{line}"/>
    <g class="hover" hidden>
      <line class="cross" y1="{pad_t - 6:.1f}" y2="{h - pad_b + 6:.1f}"/>
      <circle class="dot" r="4.5"/>
    </g>
    {''.join(ticks)}
    {''.join(time_labels)}
  </svg>
  <div class="tip" hidden></div>
</figure>"""


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

def _tile(label: str, value: str, sub: str, *, accent: str = "") -> str:
    cls = f' style="color: var({accent})"' if accent else ""
    return f"""<div class="tile">
      <div class="tile-label">{html.escape(label)}</div>
      <div class="tile-value"{cls}>{value}</div>
      <div class="tile-sub">{sub}</div>
    </div>"""


def _spread(values: list[float]) -> str:
    """Range ratio where a ratio is meaningful, including negative prices."""
    lo, hi = min(values), max(values)
    if lo <= 0:
        return f"{hi - lo:,.1f} absolute spread"
    return f"{hi / lo:.1f}x spread"


def _latest_complete_horizon(series: list[GridDataPoint], periods: int
                             ) -> list[GridDataPoint]:
    """Most recent contiguous, fully priced/carbon-scored decision horizon."""
    width = min(periods, len(series))
    for end in range(len(series), width - 1, -1):
        block = series[end - width:end]
        complete = all(p.price is not None and p.carbon_intensity is not None for p in block)
        contiguous = all(
            b.timestamp - a.timestamp == timedelta(minutes=30)
            for a, b in zip(block, block[1:])
        )
        if complete and contiguous:
            return block
    raise ValueError("no complete contiguous decision horizon")


def _market_controls(context: "MarketContext | None") -> str:
    if context is None:
        return ""
    options = "".join(
        f'<option value="{html.escape(choice.key)}"'
        f'{" selected" if choice.key == context.location_key else ""}>'
        f'{html.escape(choice.name)} · {html.escape(choice.detail)}</option>'
        for choice in context.locations
    )
    if context.location_key not in {choice.key for choice in context.locations}:
        options = (
            f'<option value="{html.escape(context.location_key)}" selected>'
            f'Custom PNode · {html.escape(context.location_name)}</option>'
            + options
        )
    custom = "" if not context.allows_custom_node else """
    <label>Custom PNode <span><input name="custom_node" placeholder="Exact CAISO node ID">
      <button type="submit">Load</button></span></label>"""
    return f"""
<form class="market-controls" action="/" method="get">
  <label>Power market <select name="market" id="marketSelect">
    <option value="GB"{" selected" if context.market_key == "GB" else ""}>Great Britain</option>
    <optgroup label="United States">
      <option value="CAISO"{" selected" if context.market_key == "CAISO" else ""}>California ISO</option>
      <option value="NYISO"{" selected" if context.market_key == "NYISO" else ""}>New York ISO</option>
    </optgroup>
  </select></label>
  <label>Grid location <select name="location">{options}</select></label>
  <button type="submit">Apply</button>{custom}
</form>"""


def _regions_card() -> str:
    """All 18 GB grid regions, right now.

    This is the single largest lever in the project and it was missing from
    this page for far too long: at the moment of writing, North Scotland sat
    at 0 gCO2/kWh on 96% wind while East Midlands sat at 131. Same country,
    same instant. Time-shifting a job saved ~54% carbon; moving it can save
    nearly all of it.

    Carbon only. GB settles ONE national wholesale price, so location changes
    emissions and not the bill — see adapters/gb_regional. Locational price
    variation is real but belongs to nodal markets (CAISO, ERCOT), and this
    page must not imply otherwise.
    """
    try:
        regions = GBRegionalAdapter().regions()
    except Exception as exc:
        return f'<section class="card"><h2>Regions</h2><p class="note">Regional data unavailable ({html.escape(str(exc)[:80])}).</p></section>'
    if not regions:
        return ""

    live = [r for r in regions if r.carbon_forecast is not None]
    if not live:
        return ""
    lo, hi = min(live, key=lambda r: r.carbon_forecast), max(live, key=lambda r: r.carbon_forecast)
    spread = (hi.carbon_forecast / lo.carbon_forecast) if lo.carbon_forecast else None
    worst = max(r.carbon_forecast for r in live) or 1

    rows = []
    for r in sorted(live, key=lambda r: r.carbon_forecast):
        mix = ", ".join(f"{f} {p:.0f}%" for f, p in r.top_sources if p > 0.5) or "—"
        width = max(2.0, r.carbon_forecast / worst * 100)
        rows.append(
            f"<tr><td>{html.escape(r.name)}</td>"
            f'<td><b>{r.carbon_forecast:,.0f}</b></td>'
            f'<td class="mixcell">{html.escape(mix)}</td>'
            f'<td class="barcell"><span class="rbar" style="width:{width:.0f}%"></span></td></tr>')

    return f"""
<section class="card">
  <h2>Where, not just when</h2>
  <p class="note">
<b>{html.escape(lo.name)} {lo.carbon_forecast:,.0f}</b> ·
    <b>{html.escape(hi.name)} {hi.carbon_forecast:,.0f}</b> gCO₂/kWh, same instant.
  </p>
  <div class="tiles">
    {_tile("Cleanest region", f"{lo.carbon_forecast:,.0f}", html.escape(lo.name) + " · gCO₂/kWh", accent="--carbon")}
    {_tile("Dirtiest region", f"{hi.carbon_forecast:,.0f}", html.escape(hi.name) + " · gCO₂/kWh")}
    {_tile("Renewable share", f"{lo.renewable_pct:,.0f}%", "wind, solar and hydro where it's cleanest")}
    {_tile("Regions", f"{len(live)}", "each with its own generation mix")}
  </div>
  <div style="overflow-x:auto">
  <table class="regions">
    <thead><tr><th>Region</th><th>gCO₂/kWh</th><th>Leading sources</th><th></th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="note" style="margin:14px 0 0">Carbon only — GB settles one national price.</p>
</section>"""


def _analytics_grid(series: list[GridDataPoint], symbol: str) -> str:
    """The panels a data-centre operator with a carbon target actually needs.

    Each answers a decision, measured over the whole cached history rather
    than one week: what flexibility is worth, when to run, how much of the
    year is expensive, and whether cheap also means clean.
    """
    price_prof = analytics.hour_profile(series, "price")
    carbon_prof = analytics.hour_profile(series, "carbon_intensity")
    sav_cost = analytics.savings_vs_deadline(series, "price", 4.0)
    sav_carb = analytics.savings_vs_deadline(series, "carbon_intensity", 4.0)
    dur_p = analytics.duration_curve(series, "price")
    dur_c = analytics.duration_curve(series, "carbon_intensity")
    corr = analytics.correlation(series)

    def head(i):
        return sav_cost.median[i] if i < len(sav_cost.median) else 0.0
    day = sav_cost.deadlines.index(24) if 24 in sav_cost.deadlines else 0
    week = sav_cost.deadlines.index(168) if 168 in sav_cost.deadlines else -1
    carb_day = (sav_carb.median[sav_carb.deadlines.index(24)]
                if 24 in sav_carb.deadlines else 0.0)

    return f"""
<section class="grid4">
  <div class="pnl">
    <h3>What a deadline is worth <em>cost</em></h3>
    {savings_panel(sav_cost, unit="% cost saved", color="--price")}
    <p class="pnl-note">A 4&nbsp;h job at a <b>24&nbsp;h</b> deadline saves
      <b>{head(day):.1f}%</b> median{f", at a week <b>{sav_cost.median[week]:.0f}%</b>" if week >= 0 else ""}.
      Every start in {len(series)//48:,} days, not one example.</p>
  </div>
  <div class="pnl">
    <h3>What a deadline is worth <em>carbon</em></h3>
    {savings_panel(sav_carb, unit="% carbon saved", color="--carbon")}
    <p class="pnl-note">Same job, carbon objective: <b>{carb_day:.1f}%</b> median at 24&nbsp;h.
      Lower than the cost figure — carbon is less volatile than price.</p>
  </div>
  <div class="pnl">
    <h3>Time of day <em>price</em></h3>
    {profile_panel(price_prof, color="--price", label="Mean price", unit=f" {symbol}/MWh")}
    <p class="pnl-note">Mean with p10–p90 band, by hour, whole history.</p>
  </div>
  <div class="pnl">
    <h3>Time of day <em>carbon</em></h3>
    {profile_panel(carbon_prof, color="--carbon", label="Mean carbon", unit=" gCO₂/kWh")}
    <p class="pnl-note">The daily cycle a deadline longer than a day can exploit.</p>
  </div>
  <div class="pnl">
    <h3>Duration curve <em>price</em></h3>
    {duration_panel(dur_p, color="--price", label="Price", unit=f" {symbol}/MWh")}
    <p class="pnl-note">Sorted worst to best. The steep left tail is where the money is.</p>
  </div>
  <div class="pnl">
    <h3>Duration curve <em>carbon</em></h3>
    {duration_panel(dur_c, color="--carbon", label="Carbon", unit=" gCO₂/kWh")}
    <p class="pnl-note">A flat middle means shifting within it buys little.</p>
  </div>
  <div class="pnl span2">
    <h3>Cheap is not clean <em>price vs carbon</em></h3>
    {scatter_panel(corr)}
    <p class="pnl-note">Correlation <b>r&nbsp;=&nbsp;{corr.get('r', 0):.2f}</b>.
      The cheapest decile is also the cleanest decile only
      <b>{corr.get('cheap_and_clean_pct', 0):.0f}%</b> of the time — so an operator
      optimising purely on price misses the carbon target the rest of it. This is why
      the objective has to be stated, not inferred.</p>
  </div>
</section>"""


def render(series: list[GridDataPoint], job: Job, market: str, currency: str,
           *, context: "MarketContext | None" = None) -> str:
    if not series:
        raise ValueError("dashboard needs at least one grid point")
    carbon = [p.carbon_intensity for p in series]
    price = [p.price for p in series]
    live_c = [v for v in carbon if v is not None]
    live_p = [v for v in price if v is not None]
    if not live_c or not live_p:
        raise ValueError("dashboard needs both price and carbon signals")
    symbol = context.symbol if context else ("£" if currency == "GBP" else "$" if currency == "USD" else "")
    price_label = context.price_label if context else "Day-ahead price"
    carbon_label = context.carbon_label if context else "Carbon intensity forecast"
    location_name = context.location_name if context else market
    signal_mode = context.signal_mode if context else "Live API data"
    provenance = context.provenance if context else (
        f"Carbon: National Grid ESO Carbon Intensity API. Price: Elexon Insights "
        f"Market Index Data, {PRICE_PROVIDER_NOTE}."
    )
    planner_href, simulator_href, operations_href, grid_view_href = (
        "/planner", "/simulator", "/", "/grid"
    )
    if context:
        shared_query = urlencode({
            "market": context.market_key,
            "location": context.location_key,
        })
        planner_href += "?" + shared_query
        simulator_href += "?" + shared_query
        operations_href += "?" + shared_query
        grid_view_href += "?" + shared_query

    # The decision must be made over the window a job would actually face —
    # the most recent deadline's worth of data — not over the whole history
    # the charts happen to show. Running it across three weeks produced a
    # "run immediately" baseline dated three weeks ago, which is not a
    # decision anyone could act on, and put the chosen window off the edge of
    # every visible chart range.
    horizon = _latest_complete_horizon(series, job.deadline_periods)
    cost_cmp = compare(horizon, job, objective="cost")
    clean = cleanest_window(horizon, job)
    baseline = cost_cmp.baseline
    scheduled = cost_cmp.scheduled

    run = timedelta(minutes=30) * job.duration_periods
    scheduled_end = scheduled.start_time + run
    clean_end = clean.start_time + run

    range_start = series[0].timestamp
    current = horizon[-1]
    complete_count = sum(
        p.price is not None and p.carbon_intensity is not None for p in series
    )
    generated = datetime.now(timezone.utc)

    # The two objectives can genuinely disagree — cheapest is not always
    # cleanest — and when they do it is a real trade-off worth surfacing.
    # But a bare index comparison fires on windows one period apart that cost
    # the same to a penny, which reports a "trade-off" of £0.00 and reads as
    # noise. Only call it a disagreement when choosing carbon actually costs
    # something: a >=2% cost penalty against the baseline.
    cost_penalty = clean.cost - scheduled.cost
    disagree = (clean.start_index != scheduled.start_index
                and baseline.cost > 0
                and cost_penalty / baseline.cost >= 0.02)

    rows = "\n".join(
        f"<tr><td>{html.escape(p.timestamp.strftime('%a %d %b %H:%M'))}</td>"
        f"<td>{'—' if p.carbon_intensity is None else f'{p.carbon_intensity:,.0f}'}</td>"
        f"<td>{'—' if p.price is None else f'{p.price:,.2f}'}</td></tr>"
        for p in series)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grid Signal — {html.escape(market)}</title>
{THEME_BOOTSTRAP}
<style>
:root {{
  color-scheme: light dark;
  --bg: #F2F2F7;
  --card: #FFFFFF;
  --text: #000000;
  --text-2: rgba(60,60,67,0.60);
  --text-3: rgba(60,60,67,0.30);
  --sep: rgba(60,60,67,0.18);
  --carbon: {CARBON_LIGHT};
  --price: {PRICE_LIGHT};
  --blue: {PRICE_LIGHT};
  --green: {CARBON_LIGHT};
  --orange: #B35300;
  --red: #C7261B;
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 6px 20px rgba(0,0,0,.06);
}}
/* Dark is a selected set of steps, not an automatic inversion: the series
   colours are darker than Apple's own dark system colours, which sit above
   the lightness band a chart needs. Declared under both scopes so the OS
   setting and an explicit stamp each win. */
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #000000;
    --card: #1C1C1E;
    --text: #FFFFFF;
    --text-2: rgba(235,235,245,0.60);
    --text-3: rgba(235,235,245,0.30);
    --sep: rgba(84,84,88,0.65);
    --carbon: {CARBON_DARK};
    --price: {PRICE_DARK};
    --blue: {PRICE_DARK};
    --green: {CARBON_DARK};
    --orange: #E08A2E;
    --red: #E2554A;
    --shadow: none;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #000000;
  --card: #1C1C1E;
  --text: #FFFFFF;
  --text-2: rgba(235,235,245,0.60);
  --text-3: rgba(235,235,245,0.30);
  --sep: rgba(84,84,88,0.65);
  --carbon: {CARBON_DARK};
  --price: {PRICE_DARK};
  --blue: {PRICE_DARK};
  --green: {CARBON_DARK};
  --orange: #E08A2E;
  --red: #E2554A;
  --shadow: none;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0 24px 72px;
  background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1080px; margin: 0 auto; }}
header {{ padding: 56px 0 28px; }}
h1 {{
  margin: 0 0 6px; font-size: 40px; line-height: 1.08;
  font-weight: 700; letter-spacing: -0.022em;
}}
.sub {{ color: var(--text-2); font-size: 17px; margin: 0; }}
nav {{ margin-top: 16px; display: inline-flex; gap: 2px; padding: 3px; border-radius: 11px;
  background: color-mix(in srgb, var(--text) 5%, transparent); }}
nav a {{ font-size: 13px; font-weight: 550; text-decoration: none; padding: 6px 14px;
  border-radius: 8px; color: var(--text-2); transition: background .15s ease, color .15s ease; }}
nav a:hover {{ color: var(--text); }}
nav a.on {{ background: var(--card); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.10); }}
.market-controls {{ margin-top: 16px; display: flex; flex-wrap: wrap; gap: 9px; align-items: end; }}
.market-controls label {{ display: flex; flex-direction: column; gap: 4px; color: var(--text-2);
  font-size: 11px; font-weight: 550; }}
.market-controls label span {{ display: flex; }}
.market-controls select, .market-controls input {{ min-width: 170px; max-width: 300px; padding: 7px 28px 7px 9px;
  border: 1px solid var(--sep); border-radius: 9px; background: var(--card); color: var(--text); font: inherit; font-size: 12px; }}
.market-controls input {{ min-width: 190px; border-radius: 9px 0 0 9px; padding-right: 9px; }}
.market-controls button {{ padding: 8px 12px; border: 0; border-radius: 9px; background: var(--price);
  color: #fff; font: 650 12px/1 inherit; cursor: pointer; }}
.market-controls label span button {{ border-radius: 0 9px 9px 0; }}
.badge {{
  display: inline-flex; align-items: center; gap: 6px;
  margin-top: 14px; padding: 5px 11px; border-radius: 980px;
  background: color-mix(in srgb, var(--carbon) 12%, transparent);
  color: var(--carbon); font-size: 13px; font-weight: 590;
  letter-spacing: -0.01em;
}}
.badge::before {{
  content: ""; width: 6px; height: 6px; border-radius: 50%;
  background: currentColor;
}}
.card {{
  background: var(--card); border-radius: 18px; padding: 22px 24px;
  box-shadow: var(--shadow); margin-bottom: 18px;
}}
.card > h2 {{
  margin: 0 0 4px; font-size: 20px; font-weight: 640; letter-spacing: -0.015em;
}}
.card > .note {{ margin: 0 0 18px; color: var(--text-2); font-size: 14px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 1px;
         background: var(--sep); border-radius: 14px; overflow: hidden; }}
.tile {{ background: var(--card); padding: 16px 18px; }}
.tile-label {{ font-size: 13px; color: var(--text-2); letter-spacing: -0.005em; }}
.tile-value {{ font-size: 30px; font-weight: 630; letter-spacing: -0.02em; margin: 4px 0 2px; }}
.tile-sub {{ font-size: 13px; color: var(--text-2); }}
.decision {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 18px; align-items: center; }}
.slot {{ padding: 16px 18px; border-radius: 14px; background: color-mix(in srgb, var(--text) 4%, transparent); }}
.slot.win {{ background: color-mix(in srgb, var(--price) 10%, transparent); }}
.slot h3 {{ margin: 0 0 8px; font-size: 12.5px; font-weight: 600; color: var(--text-2);
            letter-spacing: -0.005em; }}
.slot .when {{ font-size: 19px; font-weight: 620; letter-spacing: -0.015em; }}
.slot .figs {{ margin-top: 8px; font-size: 14px; color: var(--text-2); font-variant-numeric: tabular-nums; }}
.arrow {{ color: var(--text-3); font-size: 22px; }}
.result {{ margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--sep);
           display: flex; flex-wrap: wrap; gap: 28px; }}
.result div span {{ display: block; }}
.result .k {{ font-size: 13px; color: var(--text-2); }}
.result .v {{ font-size: 22px; font-weight: 620; letter-spacing: -0.015em; }}
.chart {{ margin: 0; position: relative; }}
.chart svg {{ width: 100%; height: 260px; display: block; overflow: visible; }}
.area {{ fill: var(--series); opacity: .10; }}
.line {{ fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round;
         vector-effect: non-scaling-stroke; }}
.band {{ fill: var(--series); opacity: .13; }}
.grid {{ stroke: var(--sep); stroke-width: 1; vector-effect: non-scaling-stroke; }}
.tick, .tlab {{ fill: var(--text-2); font-size: 11px; font-family: inherit; }}
.cross {{ stroke: var(--text-3); stroke-width: 1; vector-effect: non-scaling-stroke; }}
.dot {{ fill: var(--series); stroke: var(--card); stroke-width: 2; }}
.tip {{
  position: absolute; pointer-events: none; z-index: 5;
  background: var(--card); color: var(--text); border: 1px solid var(--sep);
  border-radius: 10px; padding: 8px 11px; font-size: 13px; white-space: nowrap;
  box-shadow: 0 4px 16px rgba(0,0,0,.14); transform: translate(-50%, -120%);
  font-variant-numeric: tabular-nums;
}}
.tip b {{ font-weight: 620; }}
.legend {{ display: flex; gap: 7px; align-items: center; font-size: 13px; color: var(--text-2); margin-bottom: 12px; }}
.swatch {{ width: 10px; height: 10px; border-radius: 3px; background: var(--series); }}
details {{ margin-top: 6px; }}
summary {{ cursor: pointer; font-size: 14px; color: var(--price); font-weight: 510; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 13px;
         font-variant-numeric: tabular-nums; }}
th, td {{ text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--sep); }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--text-2); font-weight: 510; }}
.foot {{ color: var(--text-2); font-size: 13px; margin-top: 26px; }}
table.regions {{ margin-top: 16px; }}
table.regions td, table.regions th {{ text-align: left; }}
table.regions td:nth-child(2), table.regions th:nth-child(2) {{ text-align: right;
  font-variant-numeric: tabular-nums; width: 90px; }}
.mixcell {{ color: var(--text-2); font-size: 12px; }}
.barcell {{ width: 30%; }}
.rbar {{ display: block; height: 6px; border-radius: 3px; background: var(--carbon);
  opacity: .55; }}
.sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }}
.empty {{ color: var(--text-2); }}
/* SVG elements ignore the HTML `hidden` attribute, which left the hover dot
   parked at 0,0 as a stray mark on every chart. */
svg [hidden] {{ display: none; }}
{CHART_CSS}
{PANEL_CSS}
.grid4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 12px; margin-bottom: 18px; }}
.pnl {{ background: var(--card); border-radius: 12px; padding: 14px 16px 12px;
  box-shadow: var(--shadow); }}
.pnl.span2 {{ grid-column: span 2; }}
.pnl h3 {{ margin: 0 0 10px; font-size: 13px; font-weight: 640; letter-spacing: -0.008em;
  color: var(--text); }}
.pnl h3 em {{ font-style: normal; color: var(--text-2);
  font-weight: 500; letter-spacing: 0; }}
.pnl-note {{ margin: 8px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-2); }}
.pnl-note b {{ color: var(--text); font-weight: 640; font-variant-numeric: tabular-nums; }}
@media (max-width: 760px) {{ .pnl.span2 {{ grid-column: span 1; }} }}
@media (max-width: 720px) {{
  .decision {{ grid-template-columns: 1fr; }}
  .arrow {{ display: none; }}
  h1 {{ font-size: 32px; }}
}}
{THEME_CSS}
</style>
</head>
<body>
{THEME_CONTROL}
<div class="wrap">

<header>
  <h1>Grid Signal</h1>
  <p class="sub">{html.escape(market)} · {html.escape(location_name)} · {html.escape(range_start.strftime('%a %d %b'))} to {html.escape(series[-1].timestamp.strftime('%a %d %b %Y'))}</p>
  <nav><a href="{html.escape(operations_href)}">Operations</a><a href="{html.escape(simulator_href)}">Fleet Lab</a><a href="{html.escape(planner_href)}">Placement Lab</a><a href="{html.escape(grid_view_href)}" class="on">Sites &amp; Grid</a><a href="/decisions">Decisions</a></nav>
  {_market_controls(context)}
  <div class="badge">MEASURED · {html.escape(signal_mode)} · {complete_count} of {len(series)} half-hours fully scored</div>
</header>

<section class="card">
  <h2>Latest fully scored interval</h2>
  <div class="tiles">
    {_tile("Carbon intensity", f"{current.carbon_intensity:,.0f}" if current.carbon_intensity is not None else "—",
           "gCO₂/kWh · " + html.escape(carbon_label), accent="--carbon")}
    {_tile("Price", f"{symbol}{current.price:,.2f}" if current.price is not None else "—",
           "per MWh · " + html.escape(price_label), accent="--price")}
    {_tile("Cleanest in range", f"{min(live_c):,.0f}", f"gCO₂/kWh · {_spread(live_c)}")}
    {_tile("Cheapest in range", f"{symbol}{min(live_p):,.2f}", f"per MWh · {_spread(live_p)}")}
  </div>
</section>

{_analytics_grid(series, symbol)}

{_regions_card() if context is None or context.market_key == "GB" else ""}

<section class="card">
  <h2>Scheduling decision</h2>
  <p class="note">
    {html.escape(job.name)} — {job.power_kw:g} kW for {job.duration_periods * 0.5:g} h
    ({job.energy_kwh:,.1f} kWh), deadline {job.deadline_periods * 0.5:g} h.
    Optimising for cost, decided over the {job.deadline_periods * 0.5:g} h from
    {html.escape(horizon[0].timestamp.strftime('%a %d %b %H:%M'))}.
  </p>
  <div class="decision">
    <div class="slot">
      <h3>Baseline · run immediately</h3>
      <div class="when">{html.escape(baseline.start_time.strftime('%a %d %b, %H:%M'))}</div>
      <div class="figs">{symbol}{baseline.cost:,.2f} · {baseline.carbon_kg:,.2f} kgCO₂</div>
    </div>
    <div class="arrow">→</div>
    <div class="slot win">
      <h3>Scheduled · cheapest window</h3>
      <div class="when">{html.escape(scheduled.start_time.strftime('%a %d %b, %H:%M'))}</div>
      <div class="figs">{symbol}{scheduled.cost:,.2f} · {scheduled.carbon_kg:,.2f} kgCO₂</div>
    </div>
  </div>
  <div class="result">
    <div><span class="k">Cost saved</span><span class="v">{symbol}{cost_cmp.cost_saved:,.2f} ({cost_cmp.cost_saved_pct:.1f}%)</span></div>
    <div><span class="k">Carbon saved</span><span class="v">{cost_cmp.carbon_saved_g / 1000:,.2f} kg ({cost_cmp.carbon_saved_pct:.1f}%)</span></div>
    <div><span class="k">Delayed by</span><span class="v">{cost_cmp.delay_hours:g} h</span></div>
  </div>
</section>

{"" if not disagree else f'''
<section class="card">
  <h2>The two objectives disagree</h2>
  <p class="note">
    The cheapest window starts {html.escape(scheduled.start_time.strftime('%H:%M'))} and the cleanest
    starts {html.escape(clean.start_time.strftime('%H:%M'))} — different half-hours, and the choice
    is not free. Going carbon-optimal costs {symbol}{clean.cost:,.2f} against {symbol}{scheduled.cost:,.2f},
    a {symbol}{cost_penalty:,.2f} premium to save {(scheduled.carbon_g - clean.carbon_g) / 1000:,.2f} kgCO₂.
    This is a real trade-off the scheduler must be told how to make, not one it can quietly
    resolve on its own.
  </p>
  <div class="tiles">
    {_tile("Cost-optimal", html.escape(scheduled.start_time.strftime('%H:%M')), f"{symbol}{scheduled.cost:,.2f} · {scheduled.carbon_kg:,.2f} kgCO₂", accent="--price")}
    {_tile("Carbon-optimal", html.escape(clean.start_time.strftime('%H:%M')), f"{symbol}{clean.cost:,.2f} · {clean.carbon_kg:,.2f} kgCO₂", accent="--carbon")}
  </div>
</section>'''}

<section class="card">
  <h2>Carbon intensity</h2>
  <p class="note">Shaded span = carbon-optimal window. Drag to zoom.</p>
  {chart(ChartSeries("carbon", "Carbon intensity", "gCO₂/kWh",
                     [(p.timestamp, p.carbon_intensity) for p in series],
                     "--carbon", 0,
                     bands=[Band(clean.start_time, clean_end, "carbon-optimal")]),
         height=300, default_range="1W")}
</section>

<section class="card">
  <h2>Price</h2>
  <p class="note">Shaded span = cost-optimal window. Drag to zoom.</p>
  {chart(ChartSeries("price", price_label, f"{symbol}/MWh",
                     [(p.timestamp, p.price) for p in series],
                     "--price", 2, prefix=symbol,
                     bands=[Band(scheduled.start_time, scheduled_end, "cost-optimal")]),
         height=300, default_range="1W")}
</section>

<section class="card">
  <h2>Underlying data</h2>
  <details>
    <summary>Show {len(series)} settlement periods</summary>
    <table>
      <thead><tr><th>Period start (UTC)</th><th>gCO₂/kWh</th><th>{symbol}/MWh</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </details>
</section>

<p class="foot">
  {html.escape(provenance)}
  Signal mode: {html.escape(signal_mode)}.
  Generated {html.escape(generated.strftime('%Y-%m-%d %H:%M UTC'))}.
</p>

</div>
<script>
{EXPAND_JS}

var marketSelect = document.getElementById("marketSelect");
if (marketSelect) marketSelect.addEventListener("change", function () {{
  var defaults = {{GB:"national",CAISO:"sp15",NYISO:"nyc"}};
  var defaultLocation = defaults[marketSelect.value] || "national";
  location.href = "/grid?market=" + encodeURIComponent(marketSelect.value) +
    "&location=" + encodeURIComponent(defaultLocation);
}});

// Crosshair + tooltip. An HTML chart is interactive by default; a static
// picture of a time series makes the reader guess at values.
for (const fig of document.querySelectorAll('.chart')) {{
  const svg = fig.querySelector('svg'), tip = fig.querySelector('.tip');
  const hover = fig.querySelector('.hover'), cross = fig.querySelector('.cross');
  const dot = fig.querySelector('.dot');
  const pts = (svg.dataset.points || '').split(';').filter(Boolean).map(s => {{
    const [x, y, when, v] = s.split(','); return {{x: +x, y: +y, when, v: +v}};
  }});
  if (!pts.length) continue;
  const vb = svg.viewBox.baseVal;

  svg.addEventListener('pointermove', e => {{
    const r = svg.getBoundingClientRect();
    const vx = (e.clientX - r.left) / r.width * vb.width;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.x - vx) < Math.abs(best.x - vx)) best = p;
    hover.hidden = false;
    cross.setAttribute('x1', best.x); cross.setAttribute('x2', best.x);
    dot.setAttribute('cx', best.x); dot.setAttribute('cy', best.y);
    tip.hidden = false;
    tip.innerHTML = '<b>' + best.v.toLocaleString(undefined, {{maximumFractionDigits: 2}}) + '</b> · ' + best.when;
    tip.style.left = (best.x / vb.width * r.width) + 'px';
    tip.style.top = (best.y / vb.height * r.height) + 'px';
  }});
  svg.addEventListener('pointerleave', () => {{ hover.hidden = true; tip.hidden = true; }});
}}
</script>
</body>
</html>"""


PRICE_PROVIDER_NOTE = "APXMIDP only — providers are not averaged, because N2EXMIDP reports structural zeros"


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the grid signal dashboard.")
    ap.add_argument("--days", type=int, default=400, help="days of history (default 400)")
    ap.add_argument("--end", type=str, default=None, help="end date YYYY-MM-DD (default: latest available)")
    ap.add_argument("--power", type=float, default=6.5, help="job power draw in kW")
    ap.add_argument("--hours", type=float, default=4.0, help="job duration in hours")
    ap.add_argument("--deadline", type=float, default=24.0, help="deadline in hours")
    ap.add_argument("--open", action="store_true", help="open in the browser when done")
    args = ap.parse_args()

    end = (datetime.fromisoformat(args.end) if args.end
           else datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    start = end - timedelta(days=args.days)

    print(f"Loading GB {start.date()} → {end.date()} …")
    series = feed.load(start, end)
    if not series:
        raise SystemExit("No data returned — check network access to the market APIs.")

    job = Job(
        name="fine-tune run",
        power_kw=args.power,
        duration_periods=max(int(args.hours * 2), 1),
        deadline_periods=max(int(args.deadline * 2), 1),
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(series, job, "GB", "GBP"), encoding="utf-8")
    print(f"{len(series)} settlement periods → {OUT}")

    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
