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

from adapters.base_adapter import GridDataPoint
from adapters.gb import GBAdapter
from app.chart import CHART_CSS, Band, ChartSeries, chart
from core.grid import Job, cheapest_window, cleanest_window, compare, run_immediately

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


def render(series: list[GridDataPoint], job: Job, market: str, currency: str) -> str:
    carbon = [p.carbon_intensity for p in series]
    price = [p.price for p in series]
    live_c = [v for v in carbon if v is not None]
    live_p = [v for v in price if v is not None]

    # The decision must be made over the window a job would actually face —
    # the most recent deadline's worth of data — not over the whole history
    # the charts happen to show. Running it across three weeks produced a
    # "run immediately" baseline dated three weeks ago, which is not a
    # decision anyone could act on, and put the chosen window off the edge of
    # every visible chart range.
    horizon = series[-job.deadline_periods:] if len(series) > job.deadline_periods else series
    cost_cmp = compare(horizon, job, objective="cost")
    clean = cleanest_window(horizon, job)
    baseline = cost_cmp.baseline
    scheduled = cost_cmp.scheduled

    run = timedelta(minutes=30) * job.duration_periods
    scheduled_end = scheduled.start_time + run
    clean_end = clean.start_time + run

    now = series[0].timestamp
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
nav {{ margin-top: 16px; display: flex; gap: 8px; }}
nav a {{ font-size: 13px; font-weight: 550; text-decoration: none; padding: 6px 14px;
  border-radius: 980px; color: var(--text-2);
  background: color-mix(in srgb, var(--text) 5%, transparent); }}
nav a.on {{ background: var(--price); color: #fff; }}
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
.slot h3 {{ margin: 0 0 8px; font-size: 13px; font-weight: 590; color: var(--text-2);
            text-transform: uppercase; letter-spacing: 0.04em; }}
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
.sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }}
.empty {{ color: var(--text-2); }}
/* SVG elements ignore the HTML `hidden` attribute, which left the hover dot
   parked at 0,0 as a stray mark on every chart. */
svg [hidden] {{ display: none; }}
{CHART_CSS}
@media (max-width: 720px) {{
  .decision {{ grid-template-columns: 1fr; }}
  .arrow {{ display: none; }}
  h1 {{ font-size: 32px; }}
}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Grid Signal</h1>
  <p class="sub">{html.escape(market)} electricity market · {html.escape(now.strftime('%a %d %b'))} – {html.escape(series[-1].timestamp.strftime('%a %d %b %Y'))}</p>
  <nav><a href="/" class="on">Grid</a><a href="/simulator">Simulator</a></nav>
  <div class="badge">MEASURED · live API data, {len(live_c)} of {len(series)} half-hours priced</div>
</header>

<section class="card">
  <h2>Right now</h2>
  <p class="note">Half-hourly settlement periods, the grain the market itself settles on.</p>
  <div class="tiles">
    {_tile("Carbon intensity", f"{carbon[0]:,.0f}" if carbon[0] is not None else "—",
           "gCO₂/kWh · forecast", accent="--carbon")}
    {_tile("Price", f"£{price[0]:,.2f}" if price[0] is not None else "—",
           f"per MWh · day-ahead", accent="--price")}
    {_tile("Cleanest ahead", f"{min(live_c):,.0f}", f"gCO₂/kWh · {(max(live_c)/min(live_c)):.1f}× spread")}
    {_tile("Cheapest ahead", f"£{min(live_p):,.2f}", f"per MWh · {(max(live_p)/min(live_p)):.1f}× spread")}
  </div>
</section>

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
      <div class="figs">£{baseline.cost:,.2f} · {baseline.carbon_kg:,.2f} kgCO₂</div>
    </div>
    <div class="arrow">→</div>
    <div class="slot win">
      <h3>Scheduled · cheapest window</h3>
      <div class="when">{html.escape(scheduled.start_time.strftime('%a %d %b, %H:%M'))}</div>
      <div class="figs">£{scheduled.cost:,.2f} · {scheduled.carbon_kg:,.2f} kgCO₂</div>
    </div>
  </div>
  <div class="result">
    <div><span class="k">Cost saved</span><span class="v">£{cost_cmp.cost_saved:,.2f} ({cost_cmp.cost_saved_pct:.1f}%)</span></div>
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
    is not free. Going carbon-optimal costs £{clean.cost:,.2f} against £{scheduled.cost:,.2f},
    a £{cost_penalty:,.2f} premium to save {(scheduled.carbon_g - clean.carbon_g) / 1000:,.2f} kgCO₂.
    This is a real trade-off the scheduler must be told how to make, not one it can quietly
    resolve on its own.
  </p>
  <div class="tiles">
    {_tile("Cost-optimal", html.escape(scheduled.start_time.strftime('%H:%M')), f"£{scheduled.cost:,.2f} · {scheduled.carbon_kg:,.2f} kgCO₂", accent="--price")}
    {_tile("Carbon-optimal", html.escape(clean.start_time.strftime('%H:%M')), f"£{clean.cost:,.2f} · {clean.carbon_kg:,.2f} kgCO₂", accent="--carbon")}
  </div>
</section>'''}

<section class="card">
  <h2>Carbon intensity</h2>
  <p class="note">Forecast gCO₂/kWh. Shaded span is the carbon-optimal window for this job. Pick a range; hover to read any point.</p>
  {chart(ChartSeries("carbon", "Carbon intensity", "gCO₂/kWh",
                     [(p.timestamp, p.carbon_intensity) for p in series],
                     "--carbon", 0,
                     bands=[Band(clean.start_time, clean_end, "carbon-optimal")]),
         height=300, default_range="1W")}
</section>

<section class="card">
  <h2>Price</h2>
  <p class="note">Day-ahead Market Index, £/MWh. Shaded span is the cost-optimal window. Pick a range; hover to read any point.</p>
  {chart(ChartSeries("price", "Day-ahead price", "£/MWh",
                     [(p.timestamp, p.price) for p in series],
                     "--price", 2, prefix="£",
                     bands=[Band(scheduled.start_time, scheduled_end, "cost-optimal")]),
         height=300, default_range="1W")}
</section>

<section class="card">
  <h2>Underlying data</h2>
  <p class="note">Every half-hour behind the charts above.</p>
  <details>
    <summary>Show {len(series)} settlement periods</summary>
    <table>
      <thead><tr><th>Period start (UTC)</th><th>gCO₂/kWh</th><th>£/MWh</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </details>
</section>

<p class="foot">
  Carbon: National Grid ESO Carbon Intensity API (forecast, not settled outturn).
  Price: Elexon Insights Market Index Data, {html.escape(PRICE_PROVIDER_NOTE)}.
  Both forward-looking on purpose — a scheduler deciding now cannot see settled values.
  Generated {html.escape(generated.strftime('%Y-%m-%d %H:%M UTC'))}.
</p>

</div>
<script>
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
    ap.add_argument("--days", type=int, default=21, help="days of data to pull (default 21)")
    ap.add_argument("--end", type=str, default=None, help="end date YYYY-MM-DD (default: latest available)")
    ap.add_argument("--power", type=float, default=6.5, help="job power draw in kW")
    ap.add_argument("--hours", type=float, default=4.0, help="job duration in hours")
    ap.add_argument("--deadline", type=float, default=24.0, help="deadline in hours")
    ap.add_argument("--open", action="store_true", help="open in the browser when done")
    args = ap.parse_args()

    end = (datetime.fromisoformat(args.end) if args.end
           else datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    start = end - timedelta(days=args.days)

    adapter = GBAdapter()
    print(f"Fetching {adapter.market_name} {start.date()} → {end.date()} …")
    series = adapter.get_data(start, end)
    if not series:
        raise SystemExit("No data returned — check network access to the market APIs.")

    job = Job(
        name="fine-tune run",
        power_kw=args.power,
        duration_periods=max(int(args.hours * 2), 1),
        deadline_periods=max(int(args.deadline * 2), 1),
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(series, job, adapter.market_name, adapter.currency), encoding="utf-8")
    print(f"{len(series)} settlement periods → {OUT}")

    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
