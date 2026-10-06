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
from app import template
from app.panels import (EXPAND_JS, PANEL_CSS, duration_panel, profile_panel,
                        savings_panel, scatter_panel)
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav
from app.chart import CHART_CSS, Band, ChartSeries, chart
from core.grid import Job, cleanest_window, compare

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
      <option value="NYISO"{" selected" if context.market_key == "NYISO" else ""}>New York ISO</option><option value="MISO"{" selected" if context.market_key == "MISO" else ""}>Midcontinent ISO</option><option value="ERCOT"{" selected" if context.market_key == "ERCOT" else ""}>ERCOT (Texas)</option>
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
  <h2>Carbon by region</h2>
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
    <h3>Savings by deadline <em>cost</em></h3>
    {savings_panel(sav_cost, unit="% cost saved", color="--price")}
    <p class="pnl-note">4&nbsp;h job, 24&nbsp;h deadline: <b>{head(day):.1f}%</b> median saving{f"; 1 week: <b>{sav_cost.median[week]:.0f}%</b>" if week >= 0 else ""}.
      All start times over {len(series)//48:,} days.</p>
  </div>
  <div class="pnl">
    <h3>Savings by deadline <em>carbon</em></h3>
    {savings_panel(sav_carb, unit="% carbon saved", color="--carbon")}
    <p class="pnl-note">4&nbsp;h job, 24&nbsp;h deadline: <b>{carb_day:.1f}%</b> median saving.</p>
  </div>
  <div class="pnl">
    <h3>Time of day <em>price</em></h3>
    {profile_panel(price_prof, color="--price", label="Mean price", unit=f" {symbol}/MWh")}
    <p class="pnl-note">Mean with p10–p90 band, by hour, whole history.</p>
  </div>
  <div class="pnl">
    <h3>Time of day <em>carbon</em></h3>
    {profile_panel(carbon_prof, color="--carbon", label="Mean carbon", unit=" gCO₂/kWh")}
    <p class="pnl-note">Mean with p10–p90 band, by hour, whole history.</p>
  </div>
  <div class="pnl">
    <h3>Duration curve <em>price</em></h3>
    {duration_panel(dur_p, color="--price", label="Price", unit=f" {symbol}/MWh")}
    <p class="pnl-note">Every half-hour, sorted highest to lowest.</p>
  </div>
  <div class="pnl">
    <h3>Duration curve <em>carbon</em></h3>
    {duration_panel(dur_c, color="--carbon", label="Carbon", unit=" gCO₂/kWh")}
    <p class="pnl-note">Every half-hour, sorted highest to lowest.</p>
  </div>
  <div class="pnl span2">
    <h3>Price vs carbon</h3>
    {scatter_panel(corr)}
    <p class="pnl-note">Correlation <b>r&nbsp;=&nbsp;{corr.get('r', 0):.2f}</b>.
      Cheapest 10% of half-hours that are also in the cleanest 10%:
      <b>{corr.get('cheap_and_clean_pct', 0):.0f}%</b>.</p>
  </div>
</section>"""


_PAGE = template.load("dashboard.html")


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
    navigation = product_nav("energy", {
        "overview": operations_href,
        "plan": planner_href,
        "hardware": simulator_href,
        "energy": grid_view_href,
    })

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

    carbon_tile = _tile("Carbon intensity", f"{current.carbon_intensity:,.0f}" if current.carbon_intensity is not None else "—",
                        "gCO₂/kWh · " + html.escape(carbon_label), accent="--carbon")
    price_tile = _tile("Price", f"{symbol}{current.price:,.2f}" if current.price is not None else "—",
                       "per MWh · " + html.escape(price_label), accent="--price")
    cleanest_tile = _tile("Cleanest in range", f"{min(live_c):,.0f}", f"gCO₂/kWh · {_spread(live_c)}")
    cheapest_tile = _tile("Cheapest in range", f"{symbol}{min(live_p):,.2f}", f"per MWh · {_spread(live_p)}")
    disagreement = "" if not disagree else f'''
<section class="card">
  <h2>Cheapest and cleanest windows differ</h2>
  <p class="note">
    Cheapest window starts {html.escape(scheduled.start_time.strftime('%H:%M'))}; cleanest starts
    {html.escape(clean.start_time.strftime('%H:%M'))}. The cleanest window costs {symbol}{clean.cost:,.2f}
    against {symbol}{scheduled.cost:,.2f} ({symbol}{cost_penalty:,.2f} more) and saves
    {(scheduled.carbon_g - clean.carbon_g) / 1000:,.2f} kgCO₂.
  </p>
  <div class="tiles">
    {_tile("Cost-optimal", html.escape(scheduled.start_time.strftime('%H:%M')), f"{symbol}{scheduled.cost:,.2f} · {scheduled.carbon_kg:,.2f} kgCO₂", accent="--price")}
    {_tile("Carbon-optimal", html.escape(clean.start_time.strftime('%H:%M')), f"{symbol}{clean.cost:,.2f} · {clean.carbon_kg:,.2f} kgCO₂", accent="--carbon")}
  </div>
</section>'''
    carbon_chart = chart(ChartSeries("carbon", "Carbon intensity", "gCO₂/kWh",
                                     [(p.timestamp, p.carbon_intensity) for p in series],
                                     "--carbon", 0,
                                     bands=[Band(clean.start_time, clean_end, "carbon-optimal")]),
                         height=300, default_range="1W")
    price_chart = chart(ChartSeries("price", price_label, f"{symbol}/MWh",
                                    [(p.timestamp, p.price) for p in series],
                                    "--price", 2, prefix=symbol,
                                    bands=[Band(scheduled.start_time, scheduled_end, "cost-optimal")]),
                        height=300, default_range="1W")
    return template.fill(_PAGE, {
        "__MARKET_NAME__": html.escape(market),
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__CARBON_LIGHT__": CARBON_LIGHT,
        "__PRICE_LIGHT__": PRICE_LIGHT,
        "__CARBON_DARK__": CARBON_DARK,
        "__PRICE_DARK__": PRICE_DARK,
        "__CHART_CSS__": CHART_CSS,
        "__PANEL_CSS__": PANEL_CSS,
        "__THEME_CSS__": THEME_CSS,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__LOCATION_NAME__": html.escape(location_name),
        "__RANGE_START__": html.escape(range_start.strftime('%a %d %b')),
        "__RANGE_END__": html.escape(series[-1].timestamp.strftime('%a %d %b %Y')),
        "__NAVIGATION__": navigation,
        "__MARKET_CONTROLS__": _market_controls(context),
        "__SIGNAL_MODE__": html.escape(signal_mode),
        "__COMPLETE_COUNT__": complete_count,
        "__SERIES_COUNT__": len(series),
        "__CARBON_TILE__": carbon_tile,
        "__PRICE_TILE__": price_tile,
        "__CLEANEST_TILE__": cleanest_tile,
        "__CHEAPEST_TILE__": cheapest_tile,
        "__ANALYTICS__": _analytics_grid(series, symbol),
        "__REGIONS__": _regions_card() if context is None or context.market_key == "GB" else "",
        "__JOB_NAME__": html.escape(job.name),
        "__JOB_POWER_KW__": f'{job.power_kw:g}',
        "__JOB_HOURS__": f'{job.duration_periods * 0.5:g}',
        "__JOB_ENERGY_KWH__": f'{job.energy_kwh:,.1f}',
        "__DEADLINE_HOURS__": f'{job.deadline_periods * 0.5:g}',
        "__HORIZON_START__": html.escape(horizon[0].timestamp.strftime('%a %d %b %H:%M')),
        "__BASELINE_START__": html.escape(baseline.start_time.strftime('%a %d %b, %H:%M')),
        "__SYMBOL__": symbol,
        "__BASELINE_COST__": f'{baseline.cost:,.2f}',
        "__BASELINE_CARBON_KG__": f'{baseline.carbon_kg:,.2f}',
        "__SCHEDULED_START__": html.escape(scheduled.start_time.strftime('%a %d %b, %H:%M')),
        "__SCHEDULED_COST__": f'{scheduled.cost:,.2f}',
        "__SCHEDULED_CARBON_KG__": f'{scheduled.carbon_kg:,.2f}',
        "__COST_SAVED__": f'{cost_cmp.cost_saved:,.2f}',
        "__COST_SAVED_PCT__": f'{cost_cmp.cost_saved_pct:.1f}',
        "__CARBON_SAVED_KG__": f'{cost_cmp.carbon_saved_g / 1000:,.2f}',
        "__CARBON_SAVED_PCT__": f'{cost_cmp.carbon_saved_pct:.1f}',
        "__DELAY_HOURS__": f'{cost_cmp.delay_hours:g}',
        "__DISAGREEMENT__": disagreement,
        "__CARBON_CHART__": carbon_chart,
        "__PRICE_CHART__": price_chart,
        "__ROWS__": rows,
        "__PROVENANCE__": html.escape(provenance),
        "__GENERATED__": html.escape(generated.strftime('%Y-%m-%d %H:%M UTC')),
        "__EXPAND_JS__": EXPAND_JS,
    })


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
