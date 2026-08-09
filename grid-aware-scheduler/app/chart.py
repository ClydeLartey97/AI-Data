"""
Custom time-series chart — built rather than borrowed.

Written to feel like a good consumer finance chart (Trading212, Yahoo) rather
than a trading terminal. Those two things are not the same, and the difference
is why this is hand-built instead of using KLineCharts:

- A terminal gives you free pan/zoom and technical indicators. Useful for
  price action; meaningless here, since an RSI of carbon intensity is not a
  thing anyone wants.
- A consumer chart gives you a **range selector** and a **crosshair that reads
  out in a fixed place**. That is the whole interaction, it is what makes those
  charts feel effortless, and it is about two hundred lines.

Three details do most of the work:

1. **The readout does not float.** Hovered value and timestamp appear in the
   header, in the same position every time. A tooltip that chases the cursor
   makes the reader's eye chase it too. Yahoo and Trading212 both pin it.
2. **Range switching is instant.** Every range is bucketed server-side and
   embedded, so switching is a repaint, not a fetch.
3. **The min-max band is drawn behind the mean line.** Zooming out costs
   resolution but never hides the extremes — which for a scheduler is the
   difference between a useful chart and a misleading one (see
   `core/resample`).
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass

from core.resample import RANGES, Bucket, Range, prepare


@dataclass
class ChartSeries:
    key: str
    label: str
    unit: str
    points: list[tuple]
    color_var: str = "--price"
    precision: int = 0
    prefix: str = ""          # e.g. "£"


def _pack(buckets: list[Bucket], rng: Range) -> dict:
    return {
        "bucket": rng.bucket.total_seconds(),
        "intraday": rng.is_intraday,
        "axis": rng.axis_scale,
        "points": [
            {"t": int(b.start.timestamp() * 1000),
             "m": round(b.mean, 4), "lo": round(b.low, 4), "hi": round(b.high, 4),
             "n": b.count}
            for b in buckets
        ],
    }


def chart(series: ChartSeries, *, height: int = 320, default_range: str = "1W") -> str:
    """One interactive chart. Self-contained: no library, no network."""
    ranges = {}
    for rng in RANGES:
        buckets, resolved = prepare(series.points, rng.key)
        if buckets:
            ranges[rng.key] = _pack(buckets, resolved)

    if not ranges:
        return '<p class="empty">No data.</p>'
    if default_range not in ranges:
        default_range = next(iter(ranges))

    cid = f"ch-{series.key}"
    pills = "".join(
        f'<button type="button" data-r="{r.key}"'
        f'{" class=on" if r.key == default_range else ""}'
        f'{"" if r.key in ranges else " disabled"}>{html.escape(r.label)}</button>'
        for r in RANGES)

    return f"""
<figure class="ch" id="{cid}" style="--series: var({series.color_var});">
  <figcaption class="ch-head">
    <div class="ch-id">
      <span class="ch-label"><span class="ch-dot"></span>{html.escape(series.label)}</span>
      <span class="ch-unit">{html.escape(series.unit)}</span>
    </div>
    <div class="ch-read">
      <span class="ch-val" data-val>—</span>
      <span class="ch-when" data-when></span>
    </div>
    <div class="ch-ranges">{pills}</div>
  </figcaption>
  <div class="ch-plot">
    <svg viewBox="0 0 1000 {height}" preserveAspectRatio="none" role="img"
         aria-label="{html.escape(series.label)} over time in {html.escape(series.unit)}">
      <path class="ch-band" d=""/>
      <path class="ch-line" d=""/>
      <g class="ch-cross" hidden>
        <line class="ch-vline" y1="6" y2="{height - 26}"/>
        <circle class="ch-dot2" r="4.5"/>
      </g>
      <g class="ch-axis"></g>
    </svg>
  </div>
</figure>
<script>
(function () {{
  var RANGES = {json.dumps(ranges)};
  var CFG = {{
    id: {json.dumps(cid)}, height: {height},
    precision: {series.precision}, prefix: {json.dumps(series.prefix)},
    unit: {json.dumps(series.unit)}, start: {json.dumps(default_range)}
  }};

  var root = document.getElementById(CFG.id);
  var svg = root.querySelector("svg");
  var band = root.querySelector(".ch-band"), line = root.querySelector(".ch-line");
  var cross = root.querySelector(".ch-cross"), vline = root.querySelector(".ch-vline");
  var dot = root.querySelector(".ch-dot2"), axis = root.querySelector(".ch-axis");
  var valEl = root.querySelector("[data-val]"), whenEl = root.querySelector("[data-when]");
  var W = 1000, H = CFG.height, PAD_B = 26, PAD_T = 6;
  var state = null;

  var fmtNum = new Intl.NumberFormat("en-GB", {{
    minimumFractionDigits: CFG.precision, maximumFractionDigits: CFG.precision }});

  function fmtWhen(ms, intraday) {{
    var d = new Date(ms);
    var o = intraday
      ? {{ weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }}
      : {{ weekday: "short", day: "numeric", month: "short", year: "numeric" }};
    return new Intl.DateTimeFormat("en-GB", Object.assign(o, {{ timeZone: "UTC" }})).format(d);
  }}

  function show(value, ms, intraday) {{
    valEl.textContent = CFG.prefix + fmtNum.format(value) +
      (CFG.prefix ? "" : " " + CFG.unit);
    whenEl.textContent = fmtWhen(ms, intraday);
  }}

  function draw(key) {{
    var data = RANGES[key];
    if (!data || !data.points.length) return;
    var pts = data.points;

    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) {{ if (p.lo < lo) lo = p.lo; if (p.hi > hi) hi = p.hi; }});
    var span = (hi - lo) || 1;
    lo -= span * 0.10; hi += span * 0.10;

    var n = pts.length;
    function X(i) {{ return n < 2 ? W / 2 : (i / (n - 1)) * W; }}
    function Y(v) {{ return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo)); }}

    // Mean line.
    var d = "";
    pts.forEach(function (p, i) {{ d += (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(p.m).toFixed(1); }});
    line.setAttribute("d", d);

    // Min-max envelope, only where a bucket actually aggregates more than one
    // reading. At native resolution there is no range to draw and a band would
    // imply a spread that does not exist.
    var aggregated = pts.some(function (p) {{ return p.n > 1; }});
    if (aggregated) {{
      var up = "", down = "";
      pts.forEach(function (p, i) {{ up += (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(p.hi).toFixed(1); }});
      for (var i = pts.length - 1; i >= 0; i--) down += "L" + X(i).toFixed(1) + "," + Y(pts[i].lo).toFixed(1);
      band.setAttribute("d", up + down + "Z");
      band.style.display = "";
    }} else {{
      band.setAttribute("d", ""); band.style.display = "none";
    }}

    // Time axis: a handful of evenly spaced labels, never crowded.
    var want = Math.min(6, n), marks = "";
    for (var k = 0; k < want; k++) {{
      var idx = Math.round(k * (n - 1) / Math.max(want - 1, 1));
      var x = X(idx);
      var axisOpts = data.axis === "time"
        ? {{ hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }}
        : (data.axis === "month"
            ? {{ month: "short", year: "numeric", timeZone: "UTC" }}
            : {{ day: "numeric", month: "short", timeZone: "UTC" }});
      var label = new Intl.DateTimeFormat("en-GB", axisOpts).format(new Date(pts[idx].t));
      var anchor = k === 0 ? "start" : (k === want - 1 ? "end" : "middle");
      marks += '<text class="ch-tick" x="' + x.toFixed(1) + '" y="' + (H - 7) +
               '" text-anchor="' + anchor + '">' + label + "</text>";
    }}
    axis.innerHTML = marks;

    state = {{ pts: pts, X: X, Y: Y, intraday: data.intraday }};
    var last = pts[pts.length - 1];
    show(last.m, last.t, data.intraday);
  }}

  function nearest(clientX) {{
    if (!state) return null;
    var r = svg.getBoundingClientRect();
    var vx = (clientX - r.left) / r.width * W;
    var best = 0, bd = Infinity;
    for (var i = 0; i < state.pts.length; i++) {{
      var dd = Math.abs(state.X(i) - vx);
      if (dd < bd) {{ bd = dd; best = i; }}
    }}
    return best;
  }}

  svg.addEventListener("pointermove", function (e) {{
    var i = nearest(e.clientX);
    if (i === null) return;
    var p = state.pts[i];
    cross.hidden = false;
    vline.setAttribute("x1", state.X(i)); vline.setAttribute("x2", state.X(i));
    dot.setAttribute("cx", state.X(i)); dot.setAttribute("cy", state.Y(p.m));
    show(p.m, p.t, state.intraday);
  }});

  // Leaving the chart restores the latest value rather than blanking the
  // readout — an empty header slot reads as broken.
  svg.addEventListener("pointerleave", function () {{
    cross.hidden = true;
    if (!state) return;
    var last = state.pts[state.pts.length - 1];
    show(last.m, last.t, state.intraday);
  }});

  root.querySelectorAll(".ch-ranges button").forEach(function (b) {{
    b.addEventListener("click", function () {{
      if (b.disabled) return;
      root.querySelectorAll(".ch-ranges button").forEach(function (o) {{ o.classList.remove("on"); }});
      b.classList.add("on");
      draw(b.dataset.r);
    }});
  }});

  draw(CFG.start);
}})();
</script>"""


CHART_CSS = """
.ch { margin: 0; }
.ch-head { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap;
  margin-bottom: 14px; }
.ch-id { display: flex; flex-direction: column; gap: 2px; }
.ch-label { display: inline-flex; align-items: center; gap: 7px;
  font-size: 14px; font-weight: 590; letter-spacing: -0.01em; }
.ch-dot { width: 10px; height: 10px; border-radius: 3px; background: var(--series); }
.ch-unit { font-size: 12px; color: var(--text-2); padding-left: 17px; }

/* Fixed readout. Hovering moves the crosshair, never this. */
.ch-read { margin-left: 4px; min-width: 190px; }
.ch-val { display: block; font-size: 26px; font-weight: 630; letter-spacing: -0.02em;
  line-height: 1.1; font-variant-numeric: tabular-nums; }
.ch-when { display: block; font-size: 12px; color: var(--text-2);
  font-variant-numeric: tabular-nums; }

.ch-ranges { margin-left: auto; display: inline-flex; gap: 4px;
  background: color-mix(in srgb, var(--text) 5%, transparent);
  padding: 3px; border-radius: 980px; }
.ch-ranges button { font: inherit; font-size: 12px; font-weight: 550;
  letter-spacing: -0.005em; padding: 5px 12px; border: 0; border-radius: 980px;
  background: transparent; color: var(--text-2); cursor: pointer;
  transition: background .18s ease, color .18s ease; }
.ch-ranges button:hover:not(:disabled) { color: var(--text); }
.ch-ranges button.on { background: var(--card); color: var(--text);
  box-shadow: 0 1px 3px rgba(0,0,0,.10); }
.ch-ranges button:disabled { opacity: .3; cursor: default; }

.ch-plot { position: relative; }
.ch svg { width: 100%; height: auto; display: block; overflow: visible; }
.ch-band { fill: var(--series); opacity: .14; }
.ch-line { fill: none; stroke: var(--series); stroke-width: 2;
  stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.ch-vline { stroke: var(--text-3); stroke-width: 1; vector-effect: non-scaling-stroke; }
.ch-dot2 { fill: var(--series); stroke: var(--card); stroke-width: 2; }
.ch-tick { fill: var(--text-2); font-size: 11px; font-family: inherit; }
svg [hidden] { display: none; }
"""
