"""
Time-series chart — a trading terminal built for grid data.

Feature parity with a financial charting library, then past it where the
domain differs:

**Parity.** Area / line / candle / bar / baseline rendering, wheel zoom
centred on the cursor, drag to pan, shift-drag box zoom, moving averages,
Bollinger bands, log scale, percent-change rebasing, a crosshair reading
full OHLC with a floating cursor tooltip, timezone switching, keyboard
control, CSV export.

**Past it, because a scheduler asks different questions than a trader.**

- **Candles carry real information here.** Each bucket keeps open, high, low
  and close, so a long body is a half-day where carbon climbed steadily and a
  long wick is a spike that did not hold. A mean line shows neither.
- **A spread sub-pane replaces the volume pane.** Volume is meaningless for
  carbon intensity; the high-low range within each bucket is not — it is
  literally the size of the prize for shifting a job into the right half-hour.
- **Percentile bands instead of RSI.** An oscillator on carbon intensity tells
  you nothing. "Is right now in the cheapest decile of the last month" is the
  question a scheduler actually asks, so p10/p90 bands are drawn instead.
- **Histogram and heatmap read the same series two other ways.** The
  histogram is the distribution behind the line — how often is it actually
  this cheap. The heatmap is hour-of-day x day-of-week seasonality — when,
  not just how much, which a continuous time axis cannot show at a glance.
- **Zooming never invents detail.** Zoom is a window into the resolution
  already held; going finer requires a shorter range, which re-buckets. A
  chart that interpolates its way to a smoother line at high zoom is lying.

**On implementation:** the JavaScript lives in a plain string with ``__TOKEN__``
placeholders, not an f-string. Brace-escaping inside an f-string previously
emitted a raw newline into a JS string literal — a syntax error that silently
blanked every chart. Removing the f-string removes that whole class of bug.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from core.resample import RANGES, Bucket, Range, prepare


@dataclass
class Band:
    """A highlighted span — e.g. the window a job was scheduled into."""

    start: datetime
    end: datetime
    label: str = ""


@dataclass
class ChartSeries:
    key: str
    label: str
    unit: str
    points: list[tuple]
    color_var: str = "--price"
    precision: int = 0
    prefix: str = ""
    bands: list = field(default_factory=list)
    now: datetime | None = None


def _native(series: ChartSeries) -> list[dict]:
    """The raw half-hourly series. Bucketing happens in the page.

    Interval and range are independent controls — 2-hour candles over a month
    is a different question from 2-hour candles over a week, and every trading
    platform separates them. Bucketing server-side per range forced them to be
    the same thing, which is why candles at the 1D range came out flat: the
    bucket was already native resolution, so open, high, low and close were
    the same number.

    Shipping native also means zooming can genuinely re-bucket finer rather
    than just magnifying, and the payload is small — a month of half-hours is
    about 1,400 points.
    """
    return [{"t": int(ts.timestamp() * 1000), "v": round(v, 4)}
            for ts, v in series.points if v is not None]


def chart(series: ChartSeries, *, height: int = 340, default_range: str = "1W") -> str:
    points = _native(series)
    if not points:
        return '<p class="empty">No data.</p>'

    cid = f"ch-{series.key}"
    pills = "".join(
        f'<button type="button" data-r="{r.key}"'
        f'{" class=on" if r.key == default_range else ""}>{html.escape(r.label)}</button>'
        for r in RANGES)
    intervals = "".join(
        f'<button type="button" data-iv="{sec}"'
        f'{" class=on" if sec == 0 else ""}>{html.escape(lbl)}</button>'
        for sec, lbl in INTERVALS)

    cfg = {
        "id": cid, "height": height, "precision": series.precision,
        "prefix": series.prefix, "unit": series.unit, "key": series.key,
        "label": series.label, "start": default_range,
        "ranges": {r.key: (int(r.span.total_seconds() * 1000) if r.span else None)
                   for r in RANGES},
        "bands": [{"a": int(b.start.timestamp() * 1000),
                   "b": int(b.end.timestamp() * 1000), "label": b.label}
                  for b in series.bands],
        "now": int(series.now.timestamp() * 1000) if series.now else None,
    }

    body = _MARKUP.replace("__ID__", cid) \
                  .replace("__LABEL__", html.escape(series.label)) \
                  .replace("__UNIT__", html.escape(series.unit)) \
                  .replace("__PILLS__", pills) \
                  .replace("__INTERVALS__", intervals) \
                  .replace("__COLOR__", series.color_var) \
                  .replace("__H__", str(height)) \
                  .replace("__HSUB__", str(_SUB_H))
    script = _JS.replace("__POINTS__", json.dumps(points)) \
                .replace("__CFG__", json.dumps(cfg)) \
                .replace("__HSUB__", str(_SUB_H))
    # A missed placeholder is a valid JS identifier, so `node --check` accepts
    # it and the failure only appears at runtime as "__X__ is not defined".
    # Fail loudly at build time instead.
    leftover = re.findall(r"__[A-Z_]+__", script + body)
    if leftover:
        raise AssertionError(f"unsubstituted placeholders: {sorted(set(leftover))}")
    return body + "\n<script>\n" + script + "\n</script>"


#: Candle intervals, in seconds. 0 = automatic, chosen so the visible window
#: lands near a readable number of candles.
INTERVALS = [(0, "Auto"), (1800, "30m"), (3600, "1h"), (7200, "2h"),
             (21600, "6h"), (86400, "1D"), (604800, "1W")]


#: Height of the spread sub-pane, in viewBox units.
_SUB_H = 64


_MARKUP = """
<figure class="ch" id="__ID__" style="--series: var(__COLOR__);" tabindex="0">
  <figcaption class="ch-head">
    <div class="ch-id">
      <span class="ch-label"><span class="ch-dot"></span>__LABEL__</span>
      <span class="ch-unit">__UNIT__</span>
    </div>
    <div class="ch-read">
      <span class="ch-val" data-val>—</span>
      <span class="ch-when" data-when></span>
    </div>
    <div class="ch-ohlc" data-ohlc></div>
    <span class="ch-ivtag" data-iv></span>
    <div class="ch-stats" data-stats></div>
  </figcaption>

  <div class="ch-bar">
    <span class="ch-grp" role="group" aria-label="Time range">
      <span class="ch-lbl">Range</span>
      <span class="ch-seg ch-ranges">__PILLS__</span>
    </span>
    <span class="ch-grp" role="group" aria-label="Candle interval">
      <span class="ch-lbl">Interval</span>
      <span class="ch-seg ch-intervals">__INTERVALS__</span>
    </span>
    <span class="ch-grp" role="group" aria-label="Chart type">
      <span class="ch-lbl">Type</span>
      <span class="ch-seg ch-types">
        <button type="button" data-type="area" class="on">Area</button>
        <button type="button" data-type="line">Line</button>
        <button type="button" data-type="candle">Candles</button>
        <button type="button" data-type="bar">Bars</button>
        <button type="button" data-type="baseline">Baseline</button>
        <button type="button" data-type="histogram">Histogram</button>
        <button type="button" data-type="heatmap">Heatmap</button>
      </span>
    </span>
    <span class="ch-grp" role="group" aria-label="Overlays">
      <span class="ch-lbl">Overlay</span>
      <span class="ch-seg ch-overlays">
        <button type="button" data-ov="ma">MA</button>
        <button type="button" data-ov="ema">EMA</button>
        <button type="button" data-ov="pct">Percentiles</button>
        <button type="button" data-ov="boll">Bollinger</button>
        <button type="button" data-ov="spread">Spread</button>
      </span>
    </span>
    <span class="ch-grp" role="group" aria-label="Value scale">
      <span class="ch-lbl">Scale</span>
      <button type="button" class="ch-btn" data-act="log" aria-pressed="false" title="Logarithmic Y axis">Log</button>
      <button type="button" class="ch-btn" data-act="pctchg" aria-pressed="false" title="Show as percent change from range start">Δ%</button>
    </span>
    <span class="ch-grp ch-right">
      <button type="button" class="ch-btn" data-act="zone">UTC</button>
      <button type="button" class="ch-btn on" data-act="grid" aria-pressed="true">Grid</button>
      <button type="button" class="ch-btn on" data-act="cross" aria-pressed="true">Crosshair</button>
      <button type="button" class="ch-btn" data-act="y-in" title="Zoom Y axis in">Y +</button>
      <button type="button" class="ch-btn" data-act="y-out" title="Zoom Y axis out">Y −</button>
      <button type="button" class="ch-btn" data-act="reset">Reset</button>
      <button type="button" class="ch-btn" data-act="csv">CSV</button>
      <button type="button" class="ch-btn" data-act="expand" aria-expanded="false">Full screen</button>
    </span>
  </div>

  <button type="button" class="ch-full-close" aria-label="Close full-screen chart">Close</button>

  <div class="ch-plot">
    <div class="ch-legend" data-legend hidden></div>
    <div class="ch-tip" data-tip hidden></div>
    <svg viewBox="0 0 1000 __H__" preserveAspectRatio="none" role="img"
         aria-label="__LABEL__ over time in __UNIT__">
      <g class="ch-grid"></g>
      <g class="ch-spans"></g>
      <path class="ch-envelope" d=""/>
      <g class="ch-pct" hidden><path class="ch-pctband" d=""/></g>
      <g class="ch-boll" hidden><path class="ch-bollband" d=""/><path class="ch-bollmid" d=""/></g>
      <g class="ch-marks"></g>
      <path class="ch-ma" d="" hidden/>
      <path class="ch-ema" d="" hidden/>
      <g class="ch-now" hidden><line class="ch-nowline"/></g>
      <rect class="ch-sel" hidden/>
      <g class="ch-cross" hidden>
        <line class="ch-vline"/><line class="ch-hline"/>
        <circle class="ch-dot2" r="4"/>
        <rect class="ch-ybadge" width="82" height="20" rx="5"/>
        <text class="ch-badge-text ch-ybadge-text" text-anchor="middle"></text>
        <rect class="ch-xbadge" width="138" height="20" rx="5"/>
        <text class="ch-badge-text ch-xbadge-text" text-anchor="middle"></text>
      </g>
      <g class="ch-latest"><line class="ch-latest-line"/><rect class="ch-latest-badge"
        width="82" height="20" rx="5"/><text class="ch-badge-text ch-latest-text"
        text-anchor="middle"></text></g>
      <g class="ch-yaxis"></g>
      <g class="ch-axis"></g>
    </svg>
    <svg class="ch-sub" viewBox="0 0 1000 __HSUB__" preserveAspectRatio="none"
         hidden aria-label="High-low spread per interval">
      <g class="ch-subbars"></g>
      <text class="ch-sublabel" x="4" y="11">spread (high − low)</text>
    </svg>
  </div>
  <p class="ch-hint">Wheel zooms X · Shift-wheel zooms Y · drag pans X · Alt-drag pans Y · shift-drag selects · double-click resets</p>
</figure>"""


_JS = r"""
(function () {
  var P = __POINTS__;                 // native half-hourly series
  var CFG = __CFG__;

  var root = document.getElementById(CFG.id);
  var svg = root.querySelector("svg");
  var sub = root.querySelector(".ch-sub");
  var q = function (s) { return root.querySelector(s); };
  var W = 1000, H = CFG.height, PAD_T = 8, PAD_B = 30, PAD_L = 60;
  var SUB_H = __HSUB__;

  var LAST = P.length ? P[P.length - 1].t : 0;
  var FIRST = P.length ? P[0].t : 0;

  // Zoom is a TIME WINDOW, not an index pair, so it survives changes to the
  // candle interval. Switching from 1h to 6h candles keeps you looking at the
  // same stretch of time, which is what every trading platform does.
  var S = { range: CFG.start, interval: 0, type: "area", win: null, utc: true,
            ma: false, ema: false, pct: false, boll: false, spread: false, grid: true,
            cross: true, yZoom: 1, yOffset: 0, logY: false, pctChange: false };
  var view = null, histView = null, heatView = null;

  // SVG elements do not reliably reflect the `.hidden` IDL property back to
  // the content attribute (unlike HTMLElement), so a naive `el.hidden = x`
  // can silently fail to hide a group. Every toggle goes through the real
  // attribute instead, which every renderer respects consistently.
  function setHidden(el, flag) { if (!el) return; if (flag) el.setAttribute("hidden", ""); else el.removeAttribute("hidden"); }

  var fmt = new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: CFG.precision, maximumFractionDigits: CFG.precision });
  var pctFmt = new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" });
  function num(v) {
    if (S.pctChange && S.type !== "histogram" && S.type !== "heatmap") return pctFmt.format(v) + "%";
    // -0.00 is not a price. Rounding can land a tick just below zero and the
    // formatter faithfully prints the sign.
    if (Object.is(v, -0) || (v < 0 && v > -Math.pow(10, -CFG.precision) / 2)) v = 0;
    return CFG.prefix + fmt.format(v);
  }

  function when(ms, fine) {
    var o = fine
      ? { weekday: "short", day: "numeric", month: "short",
          hour: "2-digit", minute: "2-digit", hour12: false }
      : { weekday: "short", day: "numeric", month: "short", year: "numeric" };
    if (S.utc) o.timeZone = "UTC";
    return new Intl.DateTimeFormat("en-GB", o).format(new Date(ms));
  }

  function windowMs() {
    if (S.win) return S.win;
    var span = CFG.ranges[S.range];
    return span === null || span === undefined ? [FIRST, LAST] : [LAST - span, LAST];
  }

  // Auto interval targets ~200 candles across the visible window, snapped to
  // intervals a person reads naturally.
  var STEPS = [1800000, 3600000, 7200000, 10800000, 21600000, 43200000,
               86400000, 172800000, 604800000, 2592000000];
  function interval(win) {
    if (S.interval) return S.interval * 1000;
    var ideal = (win[1] - win[0]) / 200;
    for (var i = 0; i < STEPS.length; i++) if (STEPS[i] >= ideal) return STEPS[i];
    return STEPS[STEPS.length - 1];
  }

  function bucket(win, iv) {
    var out = [], cur = null;
    for (var i = 0; i < P.length; i++) {
      var p = P[i];
      if (p.t < win[0] || p.t > win[1]) continue;
      var slot = Math.floor(p.t / iv) * iv;
      if (!cur || cur.t !== slot) {
        cur = { t: slot, o: p.v, h: p.v, l: p.v, c: p.v, m: p.v, n: 1, _s: p.v };
        out.push(cur);
      } else {
        cur.h = Math.max(cur.h, p.v); cur.l = Math.min(cur.l, p.v);
        cur.c = p.v; cur.n++; cur._s += p.v; cur.m = cur._s / cur.n;
      }
    }
    return out;
  }

  // Rebasing to percent change is a unit transform, applied after bucketing
  // so OHLC relationships within a candle still hold in the new unit.
  function rebase(pts) {
    if (!S.pctChange || !pts.length) return pts;
    var base = pts[0].o;
    if (!base) { for (var i = 0; i < pts.length; i++) if (pts[i].o) { base = pts[i].o; break; } }
    if (!base) return pts;
    return pts.map(function (p) {
      return { t: p.t, n: p.n,
        o: (p.o / base - 1) * 100, h: (p.h / base - 1) * 100,
        l: (p.l / base - 1) * 100, c: (p.c / base - 1) * 100, m: (p.m / base - 1) * 100 };
    });
  }

  function sma(v, n) { var o = [], s = 0; for (var i = 0; i < v.length; i++) {
    s += v[i]; if (i >= n) s -= v[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
  function ema(v, n) { var o = [], k = 2 / (n + 1), pr = null;
    for (var i = 0; i < v.length; i++) { pr = pr === null ? v[i] : v[i] * k + pr * (1 - k); o.push(pr); }
    return o; }
  function stddev(v, n) { var o = [];
    for (var i = 0; i < v.length; i++) {
      if (i < n - 1) { o.push(null); continue; }
      var s = 0; for (var j = i - n + 1; j <= i; j++) s += v[j];
      var mean = s / n, sq = 0;
      for (var k2 = i - n + 1; k2 <= i; k2++) sq += (v[k2] - mean) * (v[k2] - mean);
      o.push(Math.sqrt(sq / n));
    }
    return o; }
  function quantile(s, p) { if (!s.length) return 0;
    var i = (s.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return s[lo] + (s[hi] - s[lo]) * (i - lo); }

  function niceStep(range, want) {
    var raw = range / Math.max(want, 1); if (raw <= 0) return 1;
    var mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
  }

  function isDistribution(t) { return t === "histogram" || t === "heatmap"; }

  // Controls that only mean something for a continuous time series are
  // dimmed rather than hidden when a distribution view is active, so the
  // toolbar layout never jumps as the type changes.
  function setAvailability() {
    var distribution = isDistribution(S.type);
    root.querySelectorAll(".ch-intervals button, .ch-overlays button").forEach(function (b) { b.disabled = distribution; });
    root.querySelectorAll('[data-act="y-in"],[data-act="y-out"],[data-act="log"],[data-act="pctchg"]')
      .forEach(function (b) { b.disabled = distribution; });
    setHidden(q("[data-legend]"), S.type !== "heatmap");
  }

  function draw() {
    setAvailability();
    if (S.type === "histogram") { view = null; heatView = null; drawHistogram(); return; }
    if (S.type === "heatmap") { view = null; histView = null; drawHeatmap(); return; }
    histView = null; heatView = null;
    drawSeries();
  }

  function drawSeries() {
    var win = windowMs(), iv = interval(win);
    var pts = rebase(bucket(win, iv)), n = pts.length;
    if (!n) return;

    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < n; i++) { if (pts[i].l < lo) lo = pts[i].l; if (pts[i].h > hi) hi = pts[i].h; }
    var pad = (hi - lo || 1) * 0.10, baseLo = lo - pad, baseHi = hi + pad;
    var baseSpan = baseHi - baseLo, baseMid = (baseLo + baseHi) / 2;
    var ySpan = baseSpan * S.yZoom, yMid = baseMid + S.yOffset * baseSpan;
    var yLo = yMid - ySpan / 2, yHi = yMid + ySpan / 2;
    var canLog = yLo > 0 && !S.pctChange;
    var useLog = S.logY && canLog;
    var logLo = useLog ? Math.log(yLo) : 0, logHi = useLog ? Math.log(yHi) : 0;

    function X(i) { return n < 2 ? (PAD_L + W) / 2 : PAD_L + (i / (n - 1)) * (W - PAD_L); }
    function Y(v) {
      if (useLog) {
        var lv = Math.log(Math.max(v, yLo * 0.001));
        return PAD_T + (H - PAD_T - PAD_B) * (1 - (lv - logLo) / (logHi - logLo));
      }
      return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - yLo) / (yHi - yLo));
    }
    var bw = Math.max(1.2, (W - PAD_L) / Math.max(n, 1) * 0.62);

    var g = "", yt = "";
    if (useLog) {
      var decadeLo = Math.floor(Math.log10(yLo)), decadeHi = Math.ceil(Math.log10(yHi));
      for (var d = decadeLo; d <= decadeHi; d++) {
        [1, 2, 5].forEach(function (mant) {
          var v = mant * Math.pow(10, d);
          if (v < yLo || v > yHi) return;
          var yy = Y(v);
          g += '<line class="ch-gl" x1="' + PAD_L + '" y1="' + yy.toFixed(1) +
               '" x2="' + W + '" y2="' + yy.toFixed(1) + '"/>';
          yt += '<text class="ch-ytick" x="' + (PAD_L - 6) + '" y="' + (yy + 3.5).toFixed(1) +
                '" text-anchor="end">' + num(v) + "</text>";
        });
      }
    } else {
      var step = niceStep(yHi - yLo, 8);
      for (var v2 = Math.ceil(yLo / step) * step; v2 <= yHi; v2 += step) {
        var yy2 = Y(v2); if (yy2 < PAD_T || yy2 > H - PAD_B) continue;
        g += '<line class="ch-gl" x1="' + PAD_L + '" y1="' + yy2.toFixed(1) +
             '" x2="' + W + '" y2="' + yy2.toFixed(1) + '"/>';
        yt += '<text class="ch-ytick" x="' + (PAD_L - 6) + '" y="' + (yy2 + 3.5).toFixed(1) +
              '" text-anchor="end">' + num(v2) + "</text>";
      }
    }
    q(".ch-grid").innerHTML = S.grid ? g : ""; q(".ch-yaxis").innerHTML = yt;

    var marks = "", env = "";
    if (S.type === "candle" || S.type === "bar") {
      for (var k = 0; k < n; k++) {
        var p = pts[k], x = X(k), cls = p.c >= p.o ? "ch-up" : "ch-down";
        marks += '<line class="' + cls + ' ch-wick" x1="' + x.toFixed(1) + '" y1="' +
                 Y(p.h).toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + Y(p.l).toFixed(1) + '"/>';
        if (S.type === "candle") {
          var top = Y(Math.max(p.o, p.c)), bot = Y(Math.min(p.o, p.c));
          marks += '<rect class="' + cls + ' ch-body" x="' + (x - bw / 2).toFixed(1) +
                   '" y="' + top.toFixed(1) + '" width="' + bw.toFixed(1) +
                   '" height="' + Math.max(1, bot - top).toFixed(1) + '"/>';
        } else {
          marks += '<line class="' + cls + ' ch-tick2" x1="' + (x - bw / 2).toFixed(1) +
                   '" y1="' + Y(p.o).toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' +
                   Y(p.o).toFixed(1) + '"/><line class="' + cls + ' ch-tick2" x1="' + x.toFixed(1) +
                   '" y1="' + Y(p.c).toFixed(1) + '" x2="' + (x + bw / 2).toFixed(1) +
                   '" y2="' + Y(p.c).toFixed(1) + '"/>';
        }
      }
      q(".ch-envelope").setAttribute("d", "");
    } else if (S.type === "baseline") {
      var means0 = pts.map(function (p) { return p.m; });
      var baseVal = means0.reduce(function (a, b) { return a + b; }, 0) / (means0.length || 1);
      var fills = baselineFills(pts, X, Y, baseVal);
      var d0 = "";
      for (var j0 = 0; j0 < n; j0++) d0 += (j0 ? "L" : "M") + X(j0).toFixed(1) + "," + Y(pts[j0].m).toFixed(1);
      marks = '<path class="ch-baseline-up" d="' + fills.up + '"/>' +
              '<path class="ch-baseline-down" d="' + fills.down + '"/>' +
              '<line class="ch-baseline-ref" x1="' + PAD_L + '" x2="' + W + '" y1="' + Y(baseVal).toFixed(1) +
              '" y2="' + Y(baseVal).toFixed(1) + '"/>' +
              '<path class="ch-line" d="' + d0 + '"/>';
      q(".ch-envelope").setAttribute("d", "");
    } else {
      var d = "";
      for (var j = 0; j < n; j++) d += (j ? "L" : "M") + X(j).toFixed(1) + "," + Y(pts[j].m).toFixed(1);
      marks = '<path class="ch-line" d="' + d + '"/>';
      if (S.type === "area")
        env = "M" + X(0).toFixed(1) + "," + Y(yLo).toFixed(1) + "L" + d.slice(1) +
              "L" + X(n - 1).toFixed(1) + "," + Y(yLo).toFixed(1) + "Z";
      if (pts.some(function (p) { return p.n > 1; })) {
        var u = "", dn = "";
        for (var a = 0; a < n; a++) u += (a ? "L" : "M") + X(a).toFixed(1) + "," + Y(pts[a].h).toFixed(1);
        for (var b2 = n - 1; b2 >= 0; b2--) dn += "L" + X(b2).toFixed(1) + "," + Y(pts[b2].l).toFixed(1);
        marks = '<path class="ch-range" d="' + u + dn + 'Z"/>' + marks;
      }
      q(".ch-envelope").setAttribute("d", env);
    }
    q(".ch-marks").innerHTML = marks;

    var means = pts.map(function (p) { return p.m; });
    function pathOf(vals) { var s = "", on = false;
      for (var i2 = 0; i2 < vals.length; i2++) { if (vals[i2] === null) continue;
        s += (on ? "L" : "M") + X(i2).toFixed(1) + "," + Y(vals[i2]).toFixed(1); on = true; }
      return s; }
    var win2 = Math.min(24, Math.max(2, n >> 2));
    var maEl = q(".ch-ma"), emEl = q(".ch-ema");
    setHidden(maEl, !S.ma); setHidden(emEl, !S.ema);
    if (S.ma) maEl.setAttribute("d", pathOf(sma(means, win2)));
    if (S.ema) emEl.setAttribute("d", pathOf(ema(means, win2)));

    setHidden(q(".ch-pct"), !S.pct);
    if (S.pct) {
      var sorted = means.slice().sort(function (x, y) { return x - y; });
      var p10 = quantile(sorted, 0.10), p90 = quantile(sorted, 0.90);
      q(".ch-pctband").setAttribute("d", "M" + PAD_L + "," + Y(p90).toFixed(1) +
        "L" + W + "," + Y(p90).toFixed(1) + "L" + W + "," + Y(p10).toFixed(1) +
        "L" + PAD_L + "," + Y(p10).toFixed(1) + "Z");
    }

    var bollGroup = q(".ch-boll");
    setHidden(bollGroup, !S.boll);
    if (S.boll) {
      var smaLine = sma(means, win2), sd = stddev(means, win2);
      var upper = smaLine.map(function (v, i3) { return v === null || sd[i3] === null ? null : v + 2 * sd[i3]; });
      var lower = smaLine.map(function (v, i3) { return v === null || sd[i3] === null ? null : v - 2 * sd[i3]; });
      var up2 = "", dn2 = "", started = false;
      for (var bi = 0; bi < n; bi++) {
        if (upper[bi] === null) continue;
        up2 += (started ? "L" : "M") + X(bi).toFixed(1) + "," + Y(upper[bi]).toFixed(1); started = true;
      }
      started = false;
      for (var bj = n - 1; bj >= 0; bj--) {
        if (lower[bj] === null) continue;
        dn2 += (started ? "L" : "M") + X(bj).toFixed(1) + "," + Y(lower[bj]).toFixed(1); started = true;
      }
      q(".ch-bollband").setAttribute("d", up2 && dn2 ? up2 + dn2.replace("M", "L") + "Z" : "");
      q(".ch-bollmid").setAttribute("d", pathOf(smaLine));
    }

    var t0 = pts[0].t, t1 = pts[n - 1].t, ts = (t1 - t0) || 1;
    function XT(ms) { return PAD_L + Math.max(0, Math.min(1, (ms - t0) / ts)) * (W - PAD_L); }
    var sp = "";
    (CFG.bands || []).forEach(function (bd) {
      if (bd.b < t0 || bd.a > t1) return;
      sp += '<rect class="ch-span" x="' + XT(bd.a).toFixed(1) + '" y="' + PAD_T +
            '" width="' + Math.max(XT(bd.b) - XT(bd.a), 2).toFixed(1) +
            '" height="' + (H - PAD_T - PAD_B) + '" rx="3"/>';
    });
    q(".ch-spans").innerHTML = sp;
    var ng = q(".ch-now");
    if (CFG.now && CFG.now > t0 && CFG.now < t1) {
      var nx = XT(CFG.now), nl = q(".ch-nowline");
      nl.setAttribute("x1", nx); nl.setAttribute("x2", nx);
      nl.setAttribute("y1", PAD_T); nl.setAttribute("y2", H - PAD_B); setHidden(ng, false);
    } else setHidden(ng, true);

    var fine = (t1 - t0) <= 3 * 86400000;
    var want = Math.min(9, n), ax = "", xg = "";
    for (var m = 0; m < want; m++) {
      var idx = Math.round(m * (n - 1) / Math.max(want - 1, 1));
      var o = fine ? { hour: "2-digit", minute: "2-digit", hour12: false }
                   : ((t1 - t0) > 120 * 86400000 ? { month: "short", year: "numeric" }
                                                 : { day: "numeric", month: "short" });
      if (S.utc) o.timeZone = "UTC";
      xg += '<line class="ch-gl ch-xgl" x1="' + X(idx).toFixed(1) + '" y1="' + PAD_T +
             '" x2="' + X(idx).toFixed(1) + '" y2="' + (H - PAD_B) + '"/>';
      ax += '<text class="ch-tick" x="' + X(idx).toFixed(1) + '" y="' + (H - 8) +
            '" text-anchor="' + (m === 0 ? "start" : m === want - 1 ? "end" : "middle") + '">' +
            new Intl.DateTimeFormat("en-GB", o).format(new Date(pts[idx].t)) + "</text>";
    }
    if (S.grid) q(".ch-grid").innerHTML += xg;
    q(".ch-axis").innerHTML = ax;

    setHidden(sub, !S.spread);
    if (S.spread) {
      var mx = 0; pts.forEach(function (p) { mx = Math.max(mx, p.h - p.l); }); mx = mx || 1;
      var sb = "";
      for (var s2 = 0; s2 < n; s2++) {
        var hg = (pts[s2].h - pts[s2].l) / mx * (SUB_H - 18);
        sb += '<rect class="ch-sbar" x="' + (X(s2) - bw / 2).toFixed(1) + '" y="' +
              (SUB_H - 4 - hg).toFixed(1) + '" width="' + bw.toFixed(1) +
              '" height="' + Math.max(0.5, hg).toFixed(1) + '"/>';
      }
      q(".ch-subbars").innerHTML = sb;
    }

    var sum = 0, cnt = 0;
    pts.forEach(function (p) { sum += p.m * p.n; cnt += p.n; });
    q("[data-stats]").innerHTML = st("High", num(hi)) + st("Low", num(lo)) +
      st("Average", num(cnt ? sum / cnt : 0)) +
      st("Spread", (!S.pctChange && lo > 0) ? (hi / lo).toFixed(1) + "×" : "—") +
      st("Candles", String(n));

    var ivLabel = iv >= 86400000 ? (iv / 86400000) + "D" :
                  iv >= 3600000 ? (iv / 3600000) + "h" : (iv / 60000) + "m";
    q("[data-iv]").textContent = ivLabel;

    var latest = pts[n - 1], latestY = Math.max(PAD_T, Math.min(H - PAD_B, Y(latest.m)));
    var latestGroup = q(".ch-latest"), latestLine = q(".ch-latest-line");
    latestLine.setAttribute("x1", PAD_L); latestLine.setAttribute("x2", W);
    latestLine.setAttribute("y1", latestY); latestLine.setAttribute("y2", latestY);
    var latestBadge = q(".ch-latest-badge"), latestText = q(".ch-latest-text");
    latestBadge.setAttribute("x", W - 84); latestBadge.setAttribute("y", latestY - 10);
    latestText.setAttribute("x", W - 43); latestText.setAttribute("y", latestY + 4);
    latestText.textContent = num(latest.m); setHidden(latestGroup, false);

    view = { pts: pts, X: X, Y: Y, n: n, fine: fine, win: win, iv: iv,
      yLo: yLo, yHi: yHi, baseLo: baseLo, baseHi: baseHi, canLog: canLog };
    readout(pts[n - 1]);

    var logBtn = q('[data-act="log"]');
    if (logBtn && !isDistribution(S.type)) {
      logBtn.disabled = !canLog;
      if (!canLog && S.logY) { S.logY = false; logBtn.classList.remove("on"); logBtn.setAttribute("aria-pressed", "false"); }
    }
  }

  // Splits a line into filled polygons above and below a reference value,
  // interpolating the crossing point so the fill boundary sits exactly on
  // the baseline rather than jumping at candle edges.
  function baselineFills(pts, X, Y, base) {
    var up = "", down = "", By = Y(base);
    for (var i = 0; i < pts.length - 1; i++) {
      var x1 = X(i), x2 = X(i + 1), v1 = pts[i].m, v2 = pts[i + 1].m;
      var y1 = Y(v1), y2 = Y(v2);
      if ((v1 >= base && v2 >= base) || (v1 <= base && v2 <= base)) {
        var seg = "M" + x1.toFixed(1) + "," + By.toFixed(1) + "L" + x1.toFixed(1) + "," + y1.toFixed(1) +
                  "L" + x2.toFixed(1) + "," + y2.toFixed(1) + "L" + x2.toFixed(1) + "," + By.toFixed(1) + "Z";
        if (v1 + v2 >= base * 2) up += seg; else down += seg;
      } else {
        var t = (base - v1) / ((v2 - v1) || 1), xm = x1 + (x2 - x1) * t;
        var seg1 = "M" + x1.toFixed(1) + "," + By.toFixed(1) + "L" + x1.toFixed(1) + "," + y1.toFixed(1) +
                    "L" + xm.toFixed(1) + "," + By.toFixed(1) + "Z";
        var seg2 = "M" + xm.toFixed(1) + "," + By.toFixed(1) + "L" + x2.toFixed(1) + "," + y2.toFixed(1) +
                    "L" + x2.toFixed(1) + "," + By.toFixed(1) + "Z";
        if (v1 >= base) { up += seg1; down += seg2; } else { down += seg1; up += seg2; }
      }
    }
    return { up: up, down: down };
  }

  // The distribution behind the line: how often the series actually sits at
  // each level within the selected range, independent of when.
  function drawHistogram() {
    var win = windowMs(), vals = [];
    for (var i = 0; i < P.length; i++) if (P[i].t >= win[0] && P[i].t <= win[1]) vals.push(P[i].v);
    [sub, q(".ch-pct"), q(".ch-boll"), q(".ch-ma"), q(".ch-ema"), q(".ch-now"), q(".ch-latest"), q("[data-tip]")]
      .forEach(function (el) { setHidden(el, true); });
    q(".ch-spans").innerHTML = ""; q(".ch-envelope").setAttribute("d", "");
    if (!vals.length) { q(".ch-marks").innerHTML = ""; q(".ch-grid").innerHTML = ""; q(".ch-axis").innerHTML = ""; return; }
    var sorted = vals.slice().sort(function (a, b) { return a - b; });
    var lo = sorted[0], hi = sorted[sorted.length - 1], span = (hi - lo) || 1;
    var bins = Math.max(10, Math.min(28, Math.round(Math.sqrt(vals.length))));
    var counts = new Array(bins).fill(0);
    vals.forEach(function (v) { counts[Math.min(bins - 1, Math.floor((v - lo) / span * bins))]++; });
    var maxCount = 0; counts.forEach(function (c) { if (c > maxCount) maxCount = c; }); maxCount = maxCount || 1;

    function BX(i) { return PAD_L + (i / bins) * (W - PAD_L); }
    function BY(c) { return PAD_T + (H - PAD_T - PAD_B) * (1 - c / maxCount); }
    var bw = (W - PAD_L) / bins * 0.86, gap = (W - PAD_L) / bins * 0.07;

    var g = "", yt = "", step = niceStep(maxCount, 5) || 1;
    for (var v2 = 0; v2 <= maxCount; v2 += step) {
      var yy = BY(v2);
      g += '<line class="ch-gl" x1="' + PAD_L + '" y1="' + yy.toFixed(1) + '" x2="' + W + '" y2="' + yy.toFixed(1) + '"/>';
      yt += '<text class="ch-ytick" x="' + (PAD_L - 6) + '" y="' + (yy + 3.5).toFixed(1) + '" text-anchor="end">' + Math.round(v2) + "</text>";
    }
    q(".ch-grid").innerHTML = S.grid ? g : ""; q(".ch-yaxis").innerHTML = yt;

    var bars = "";
    for (var b = 0; b < bins; b++) {
      var bx = BX(b) + gap, by = BY(counts[b]);
      bars += '<rect class="ch-hbar" x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) +
              '" width="' + bw.toFixed(1) + '" height="' + Math.max(0.5, H - PAD_B - by).toFixed(1) +
              '" data-bin="' + b + '"/>';
    }
    q(".ch-marks").innerHTML = bars;

    function VX(v) { return PAD_L + (v - lo) / span * (W - PAD_L); }
    var med = quantile(sorted, 0.5), p10v = quantile(sorted, 0.10), p90v = quantile(sorted, 0.90);
    var refs = "";
    [[p10v, "P10"], [med, "Median"], [p90v, "P90"]].forEach(function (mk) {
      var mx = VX(mk[0]);
      refs += '<line class="ch-hist-ref" x1="' + mx.toFixed(1) + '" x2="' + mx.toFixed(1) +
              '" y1="' + PAD_T + '" y2="' + (H - PAD_B) + '"/>';
    });
    q(".ch-spans").innerHTML = refs;

    var ax = "";
    for (var xi = 0; xi <= 4; xi++) {
      var vv = lo + span * xi / 4;
      ax += '<text class="ch-tick" x="' + VX(vv).toFixed(1) + '" y="' + (H - 8) +
            '" text-anchor="' + (xi === 0 ? "start" : xi === 4 ? "end" : "middle") + '">' + num(vv) + "</text>";
    }
    q(".ch-axis").innerHTML = ax;

    q("[data-stats]").innerHTML = st("N", String(vals.length)) + st("Min", num(lo)) +
      st("P10", num(p10v)) + st("Median", num(med)) + st("P90", num(p90v)) + st("Max", num(hi));
    q("[data-ohlc]").innerHTML = "";
    q("[data-val]").textContent = vals.length + " samples";
    q("[data-when]").textContent = when(win[0], false) + " – " + when(win[1], false);
    q("[data-iv]").textContent = bins + " bins";

    var edges = []; for (var eb = 0; eb < bins; eb++) edges.push([lo + span * eb / bins, lo + span * (eb + 1) / bins]);
    histView = { lo: lo, span: span, bins: bins, counts: counts, edges: edges };
  }

  // Seasonality: when the series is typically cheap or dirty, which a
  // continuous time axis over a long range cannot show at a glance.
  function drawHeatmap() {
    var win = windowMs();
    var cells = [];
    for (var d = 0; d < 7; d++) { var row = []; for (var h0 = 0; h0 < 24; h0++) row.push({ sum: 0, n: 0 }); cells.push(row); }
    for (var i = 0; i < P.length; i++) {
      var p = P[i]; if (p.t < win[0] || p.t > win[1]) continue;
      var dt = new Date(p.t);
      var wd = S.utc ? dt.getUTCDay() : dt.getDay(), hr = S.utc ? dt.getUTCHours() : dt.getHours();
      var r = (wd + 6) % 7, cell = cells[r][hr]; cell.sum += p.v; cell.n++;
    }
    var lo = Infinity, hi = -Infinity;
    for (var r2 = 0; r2 < 7; r2++) for (var c = 0; c < 24; c++) {
      var cc = cells[r2][c]; if (cc.n) { cc.mean = cc.sum / cc.n; if (cc.mean < lo) lo = cc.mean; if (cc.mean > hi) hi = cc.mean; }
    }
    [sub, q(".ch-pct"), q(".ch-boll"), q(".ch-ma"), q(".ch-ema"), q(".ch-now"), q(".ch-latest"), q("[data-tip]")]
      .forEach(function (el) { setHidden(el, true); });
    q(".ch-envelope").setAttribute("d", "");
    if (!isFinite(lo)) { q(".ch-marks").innerHTML = ""; q(".ch-yaxis").innerHTML = ""; q(".ch-axis").innerHTML = ""; return; }
    var span = (hi - lo) || 1;
    var plotW = W - PAD_L, plotH = H - PAD_T - PAD_B, cw = plotW / 24, ch2 = plotH / 7;
    var DOWS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    var rects = "", yt = "";
    for (var row2 = 0; row2 < 7; row2++) {
      yt += '<text class="ch-ytick" x="' + (PAD_L - 8) + '" y="' + (PAD_T + row2 * ch2 + ch2 / 2 + 4).toFixed(1) +
            '" text-anchor="end">' + DOWS[row2] + "</text>";
      for (var col = 0; col < 24; col++) {
        var cellv = cells[row2][col], x = PAD_L + col * cw, y = PAD_T + row2 * ch2;
        if (cellv.n) {
          var pct = 10 + ((cellv.mean - lo) / span) * 80;
          rects += '<rect class="ch-hmcell" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
                   '" width="' + (cw - 1.6).toFixed(1) + '" height="' + (ch2 - 1.6).toFixed(1) +
                   '" rx="2" style="fill:color-mix(in srgb, var(--series) ' + pct.toFixed(0) +
                   '%, var(--bg))" data-r="' + row2 + '" data-c="' + col + '"/>';
        } else {
          rects += '<rect class="ch-hmcell ch-hmcell-empty" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
                   '" width="' + (cw - 1.6).toFixed(1) + '" height="' + (ch2 - 1.6).toFixed(1) + '" rx="2"/>';
        }
      }
    }
    var ax = "";
    for (var hcol = 0; hcol < 24; hcol += 4)
      ax += '<text class="ch-tick" x="' + (PAD_L + hcol * cw + cw / 2).toFixed(1) + '" y="' + (H - 8) +
            '" text-anchor="middle">' + (hcol < 10 ? "0" + hcol : hcol) + "</text>";
    q(".ch-marks").innerHTML = rects; q(".ch-yaxis").innerHTML = yt; q(".ch-axis").innerHTML = ax;
    q(".ch-grid").innerHTML = ""; q(".ch-spans").innerHTML = "";

    var legend = q("[data-legend]");
    legend.innerHTML = '<span>' + num(lo) + '</span><span class="ch-legend-bar"></span><span>' + num(hi) + '</span>';

    var filled = 0; for (var rr = 0; rr < 7; rr++) for (var cc2 = 0; cc2 < 24; cc2++) if (cells[rr][cc2].n) filled++;
    q("[data-stats]").innerHTML = st("Cells", filled + " / 168") + st("Coolest", num(lo)) + st("Warmest", num(hi));
    q("[data-ohlc]").innerHTML = "";
    q("[data-val]").textContent = "Hour × weekday mean";
    q("[data-when]").textContent = when(win[0], false) + " – " + when(win[1], false);
    q("[data-iv]").textContent = filled + " cells";

    heatView = { cells: cells, DOWS: DOWS };
  }

  function st(k, v) { return '<span class="ch-stat"><i>' + k + "</i><b>" + v + "</b></span>"; }
  function readout(p) {
    q("[data-val]").textContent = num(p.m) + (CFG.prefix ? "" : " " + CFG.unit);
    q("[data-when]").textContent = when(p.t, view ? view.fine : true);
    q("[data-ohlc]").innerHTML = st("O", num(p.o)) + st("H", num(p.h)) +
      st("L", num(p.l)) + st("C", num(p.c));
  }

  function idxAt(cx) {
    if (!view) return 0;
    var r = svg.getBoundingClientRect(), vx = (cx - r.left) / r.width * W;
    var best = 0, bd = Infinity;
    for (var i = 0; i < view.n; i++) { var d = Math.abs(view.X(i) - vx); if (d < bd) { bd = d; best = i; } }
    return best;
  }

  // Positions the floating cursor tooltip relative to the plot, clamped so
  // it never spills outside the chart even near an edge.
  function positionTip(clientX, clientY, html) {
    var tip = q("[data-tip]"), plotRect = root.querySelector(".ch-plot").getBoundingClientRect();
    tip.innerHTML = html; setHidden(tip, false);
    var x = clientX - plotRect.left, y = clientY - plotRect.top;
    var tw = tip.offsetWidth || 160, th = tip.offsetHeight || 60;
    var left = Math.min(Math.max(8, x + 16), Math.max(8, plotRect.width - tw - 8));
    var top = Math.min(Math.max(8, y - th - 16), Math.max(8, plotRect.height - th - 8));
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }

  function hoverSeries(e) {
    var i = idxAt(e.clientX), p = view.pts[i];
    setHidden(q(".ch-cross"), false);
    var x = view.X(i), y = view.Y(p.m);
    var vl = q(".ch-vline"), hl = q(".ch-hline");
    vl.setAttribute("x1", x); vl.setAttribute("x2", x);
    vl.setAttribute("y1", PAD_T); vl.setAttribute("y2", H - PAD_B);
    hl.setAttribute("x1", PAD_L); hl.setAttribute("x2", W);
    hl.setAttribute("y1", y); hl.setAttribute("y2", y);
    q(".ch-dot2").setAttribute("cx", x); q(".ch-dot2").setAttribute("cy", y);
    var yBadge = q(".ch-ybadge"), yBadgeText = q(".ch-ybadge-text");
    var badgeY = Math.max(PAD_T, Math.min(H - PAD_B - 20, y - 10));
    yBadge.setAttribute("x", W - 84); yBadge.setAttribute("y", badgeY);
    yBadgeText.setAttribute("x", W - 43); yBadgeText.setAttribute("y", badgeY + 14);
    yBadgeText.textContent = num(p.m);
    var xBadge = q(".ch-xbadge"), xBadgeText = q(".ch-xbadge-text");
    var badgeX = Math.max(PAD_L, Math.min(W - 138, x - 69));
    xBadge.setAttribute("x", badgeX); xBadge.setAttribute("y", H - PAD_B);
    xBadgeText.setAttribute("x", badgeX + 69); xBadgeText.setAttribute("y", H - PAD_B + 14);
    xBadgeText.textContent = when(p.t, true);
    readout(p);
    var html = "<b>" + when(p.t, true) + "</b>" +
      (p.n > 1 ? "<i>O</i><span>" + num(p.o) + "</span><i>H</i><span>" + num(p.h) +
        "</span><i>L</i><span>" + num(p.l) + "</span><i>C</i><span>" + num(p.c) + "</span>" :
        "<i>Value</i><span>" + num(p.m) + "</span>") +
      (p.n > 1 ? "<i>Samples</i><span>" + p.n + "</span>" : "");
    positionTip(e.clientX, e.clientY, html);
  }

  function hoverHistogram(e) {
    var r = svg.getBoundingClientRect(), vx = (e.clientX - r.left) / r.width * W;
    var b = Math.floor((vx - PAD_L) / (W - PAD_L) * histView.bins);
    var edge = histView.edges[b];
    root.querySelectorAll(".ch-hbar").forEach(function (bar) { bar.classList.toggle("ch-hbar-hot", +bar.dataset.bin === b); });
    if (!edge) { setHidden(q("[data-tip]"), true); return; }
    var total = 0; histView.counts.forEach(function (c) { total += c; });
    var html = "<b>" + num(edge[0]) + " – " + num(edge[1]) + "</b><i>Count</i><span>" +
      histView.counts[b] + "</span><i>Share</i><span>" +
      (histView.counts[b] / (total || 1) * 100).toFixed(1) + "%</span>";
    positionTip(e.clientX, e.clientY, html);
  }

  function hoverHeatmap(e) {
    var r = svg.getBoundingClientRect();
    var vx = (e.clientX - r.left) / r.width * W, vy = (e.clientY - r.top) / r.height * H;
    var col = Math.floor((vx - PAD_L) / (W - PAD_L) * 24), row = Math.floor((vy - PAD_T) / (H - PAD_T - PAD_B) * 7);
    if (col < 0 || col > 23 || row < 0 || row > 6) { setHidden(q("[data-tip]"), true); return; }
    var cell = heatView.cells[row][col];
    if (!cell || !cell.n) { setHidden(q("[data-tip]"), true); return; }
    var html = "<b>" + heatView.DOWS[row] + " " + (col < 10 ? "0" + col : col) + ":00</b><i>Mean</i><span>" +
      num(cell.mean) + "</span><i>Samples</i><span>" + cell.n + "</span>";
    positionTip(e.clientX, e.clientY, html);
  }

  function zoomY(factor, fraction) {
    if (!view) return;
    fraction = Math.max(0, Math.min(1, fraction === undefined ? 0.5 : fraction));
    var baseSpan = view.baseHi - view.baseLo, oldSpan = view.yHi - view.yLo;
    var nextSpan = Math.max(baseSpan * 0.04, Math.min(baseSpan * 20, oldSpan * factor));
    var actual = nextSpan / oldSpan;
    var anchor = view.yHi - oldSpan * fraction, oldMid = (view.yLo + view.yHi) / 2;
    var nextMid = anchor + (oldMid - anchor) * actual;
    S.yZoom = nextSpan / baseSpan;
    S.yOffset = (nextMid - (view.baseLo + view.baseHi) / 2) / baseSpan;
    draw();
  }

  function panY(fraction) {
    if (!view) return;
    S.yOffset += fraction * S.yZoom;
    draw();
  }

  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    if (e.shiftKey) {
      if (isDistribution(S.type)) return;
      var yr = svg.getBoundingClientRect();
      zoomY(Math.exp(e.deltaY * 0.0014), (e.clientY - yr.top) / yr.height);
      return;
    }
    var win = windowMs(), span = win[1] - win[0];
    var r = svg.getBoundingClientRect();
    var frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    var focus = win[0] + span * frac;
    var next = Math.max(3600000, Math.min(LAST - FIRST, span * (e.deltaY > 0 ? 1.3 : 0.75)));
    var a = focus - next * frac, b = a + next;
    if (a < FIRST) { a = FIRST; b = a + next; }
    if (b > LAST) { b = LAST; a = b - next; }
    S.win = [Math.max(FIRST, a), Math.min(LAST, b)];
    draw();
  }, { passive: false });

  var drag = null;
  svg.addEventListener("pointerdown", function (e) {
    drag = { x: e.clientX, y: e.clientY, shift: e.shiftKey, alt: e.altKey,
      win: windowMs(), moved: false, yOffset: S.yOffset, yZoom: S.yZoom };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", function (e) {
    if (S.type === "histogram" && histView) hoverHistogram(e);
    else if (S.type === "heatmap" && heatView) hoverHeatmap(e);
    else if (view && S.cross) hoverSeries(e);
    if (!drag) return;
    drag.moved = true;
    var r = svg.getBoundingClientRect();
    if (drag.shift) {
      var x1 = Math.min(drag.x, e.clientX) - r.left, x2 = Math.max(drag.x, e.clientX) - r.left;
      var sel = q(".ch-sel"); setHidden(sel, false);
      sel.setAttribute("x", (x1 / r.width * W).toFixed(1));
      sel.setAttribute("width", ((x2 - x1) / r.width * W).toFixed(1));
      sel.setAttribute("y", PAD_T); sel.setAttribute("height", H - PAD_T - PAD_B);
    } else if (drag.alt) {
      if (isDistribution(S.type)) return;
      S.yOffset = drag.yOffset + (e.clientY - drag.y) / r.height * drag.yZoom;
      draw();
    } else {
      var span = drag.win[1] - drag.win[0];
      var shift = -(e.clientX - drag.x) / r.width * span;
      var a = drag.win[0] + shift, b = drag.win[1] + shift;
      if (a < FIRST) { a = FIRST; b = a + span; }
      if (b > LAST) { b = LAST; a = b - span; }
      S.win = [a, b]; draw();
    }
  });
  svg.addEventListener("pointerup", function (e) {
    setHidden(q(".ch-sel"), true);
    if (drag && drag.shift && drag.moved) {
      var r = svg.getBoundingClientRect(), span = drag.win[1] - drag.win[0];
      var f1 = (Math.min(drag.x, e.clientX) - r.left) / r.width;
      var f2 = (Math.max(drag.x, e.clientX) - r.left) / r.width;
      if (f2 - f1 > 0.02) S.win = [drag.win[0] + span * f1, drag.win[0] + span * f2];
      draw();
    }
    drag = null;
  });
  svg.addEventListener("pointerleave", function () {
    setHidden(q(".ch-cross"), true); setHidden(q("[data-tip]"), true);
    root.querySelectorAll(".ch-hbar").forEach(function (bar) { bar.classList.remove("ch-hbar-hot"); });
    if (view) readout(view.pts[view.n - 1]);
  });
  svg.addEventListener("dblclick", function () {
    S.win = null; S.yZoom = 1; S.yOffset = 0; draw();
  });

  root.addEventListener("keydown", function (e) {
    var win = windowMs(), span = win[1] - win[0], stp = span * 0.15;
    if (e.key === "ArrowLeft") { S.win = [Math.max(FIRST, win[0] - stp), win[1] - stp]; }
    else if (e.key === "ArrowRight") { S.win = [win[0] + stp, Math.min(LAST, win[1] + stp)]; }
    else if (e.key === "ArrowUp") { e.preventDefault(); panY(0.08); return; }
    else if (e.key === "ArrowDown") { e.preventDefault(); panY(-0.08); return; }
    else if ((e.key === "+" || e.key === "=") && e.shiftKey) { e.preventDefault(); zoomY(0.75); return; }
    else if (e.key === "-" && e.shiftKey) { e.preventDefault(); zoomY(1 / 0.75); return; }
    else if (e.key === "+" || e.key === "=") { S.win = [win[0] + stp, win[1] - stp]; }
    else if (e.key === "-") { S.win = [Math.max(FIRST, win[0] - stp), Math.min(LAST, win[1] + stp)]; }
    else if (e.key.toLowerCase() === "r") { S.win = null; S.yZoom = 1; S.yOffset = 0; }
    else return;
    e.preventDefault(); draw();
  });

  function seg(sel, attr, fn) {
    root.querySelectorAll(sel + " button").forEach(function (b) {
      b.addEventListener("click", function () { if (!b.disabled) fn(b.dataset[attr], b); });
    });
  }
  function exclusive(sel, b) {
    root.querySelectorAll(sel + " button").forEach(function (o) { o.classList.remove("on"); });
    b.classList.add("on");
  }
  seg(".ch-ranges", "r", function (v, b) { exclusive(".ch-ranges", b); S.range = v;
    S.win = null; S.yZoom = 1; S.yOffset = 0; draw(); });
  seg(".ch-intervals", "iv", function (v, b) { exclusive(".ch-intervals", b); S.interval = +v; draw(); });
  seg(".ch-types", "type", function (v, b) { exclusive(".ch-types", b); S.type = v;
    S.yZoom = 1; S.yOffset = 0; draw(); });
  seg(".ch-overlays", "ov", function (v, b) { S[v] = !S[v]; b.classList.toggle("on", S[v]); draw(); });

  root.querySelectorAll(".ch-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.disabled) return;
      var a = b.dataset.act;
      if (a === "reset") { S.win = null; S.yZoom = 1; S.yOffset = 0; draw(); }
      else if (a === "zone") { S.utc = !S.utc; b.textContent = S.utc ? "UTC" : "Local"; draw(); }
      else if (a === "grid") { S.grid = !S.grid; b.classList.toggle("on", S.grid);
        b.setAttribute("aria-pressed", String(S.grid)); draw(); }
      else if (a === "cross") { S.cross = !S.cross; b.classList.toggle("on", S.cross);
        b.setAttribute("aria-pressed", String(S.cross)); if (!S.cross) setHidden(q(".ch-cross"), true); }
      else if (a === "log") { S.logY = !S.logY; b.classList.toggle("on", S.logY);
        b.setAttribute("aria-pressed", String(S.logY)); draw(); }
      else if (a === "pctchg") { S.pctChange = !S.pctChange; b.classList.toggle("on", S.pctChange);
        b.setAttribute("aria-pressed", String(S.pctChange)); S.yZoom = 1; S.yOffset = 0; draw(); }
      else if (a === "y-in") zoomY(0.75);
      else if (a === "y-out") zoomY(1 / 0.75);
      else if (a === "csv") {
        var rows, csvName;
        if (S.type === "histogram" && histView) {
          rows = [["bin_start", "bin_end", "count"]];
          histView.edges.forEach(function (e2, i) { rows.push([e2[0], e2[1], histView.counts[i]]); });
          csvName = CFG.key + "_histogram.csv";
        } else if (S.type === "heatmap" && heatView) {
          rows = [["weekday", "hour", "mean", "samples"]];
          heatView.DOWS.forEach(function (dow, r) { for (var c = 0; c < 24; c++) {
            var cell = heatView.cells[r][c]; rows.push([dow, c, cell.n ? cell.mean : "", cell.n]); } });
          csvName = CFG.key + "_heatmap.csv";
        } else if (view) {
          rows = [["interval_start", "open", "high", "low", "close", "mean", "samples"]];
          view.pts.forEach(function (p) { rows.push([new Date(p.t).toISOString(), p.o, p.h, p.l, p.c, p.m, p.n]); });
          csvName = CFG.key + "_" + S.range + ".csv";
        } else return;
        var csv = rows.map(function (r) { return r.join(","); }).join(String.fromCharCode(10));
        var el = document.createElement("a");
        el.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        el.download = csvName; el.click();
        URL.revokeObjectURL(el.href);
      }
      else if (a === "expand") {
        var expanded = !root.classList.contains("ch-full");
        root.classList.toggle("ch-full", expanded);
        document.body.classList.toggle("ch-lock", expanded);
        if (expanded) { root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true"); }
        else { root.removeAttribute("role"); root.removeAttribute("aria-modal"); }
        b.setAttribute("aria-expanded", String(expanded));
        b.textContent = expanded ? "Exit full screen" : "Full screen";
        if (expanded) root.querySelector(".ch-full-close").focus(); else root.focus();
        requestAnimationFrame(draw);
      }
    });
  });

  root.querySelector(".ch-full-close").addEventListener("click", function () {
    var button = root.querySelector('[data-act="expand"]');
    root.classList.remove("ch-full"); document.body.classList.remove("ch-lock");
    root.removeAttribute("role"); root.removeAttribute("aria-modal");
    button.setAttribute("aria-expanded", "false"); button.textContent = "Full screen";
    button.focus(); requestAnimationFrame(draw);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && root.classList.contains("ch-full"))
      root.querySelector(".ch-full-close").click();
    if (e.key === "Tab" && root.classList.contains("ch-full")) {
      var items = Array.from(root.querySelectorAll('button:not([disabled]),[tabindex]:not([tabindex="-1"])'))
        .filter(function (item) { return item.offsetParent !== null; });
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  draw();
})();
"""


CHART_CSS = """
.ch { margin: 0; outline: none; }
.ch:focus-visible { box-shadow: 0 0 0 3px color-mix(in srgb, var(--price) 25%, transparent);
  border-radius: 12px; }
.ch-head { display: flex; align-items: flex-start; gap: 18px; flex-wrap: wrap; margin-bottom: 12px; }
.ch-id { display: flex; flex-direction: column; gap: 2px; }
.ch-label { display: inline-flex; align-items: center; gap: 7px; font-size: 14px;
  font-weight: 590; letter-spacing: -0.01em; }
.ch-dot { width: 10px; height: 10px; border-radius: 3px; background: var(--series); }
.ch-unit { font-size: 12px; color: var(--text-2); padding-left: 17px; }
.ch-read { min-width: 170px; }
.ch-val { display: block; font-size: 26px; font-weight: 630; letter-spacing: -0.02em;
  line-height: 1.1; font-variant-numeric: tabular-nums; }
.ch-when { display: block; font-size: 12px; color: var(--text-2); font-variant-numeric: tabular-nums; }
.ch-ohlc, .ch-stats { display: flex; gap: 14px; align-items: flex-start; }
.ch-ohlc { opacity: .85; }
.ch-stat { display: flex; flex-direction: column; gap: 1px; }
.ch-stat i { font-style: normal; font-size: 11px; color: var(--text-2); }
.ch-stat b { font-size: 13px; font-weight: 590; letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums; }

.ch-bar { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.ch-grp { display: inline-flex; align-items: center; gap: 7px; }
.ch-right { margin-left: auto; gap: 5px; }
.ch-lbl { font-size: 11px; color: var(--text-2); text-transform: uppercase;
  letter-spacing: .05em; font-weight: 590; }
.ch-seg { display: inline-flex; gap: 2px; padding: 3px; border-radius: 10px;
  background: color-mix(in srgb, var(--text) 5%, transparent); }
.ch-seg button { font: inherit; font-size: 12px; font-weight: 550; padding: 5px 11px;
  border: 0; border-radius: 8px; background: transparent; color: var(--text-2);
  cursor: pointer; transition: background .15s ease, color .15s ease; }
.ch-seg button:hover:not(:disabled) { color: var(--text); }
.ch-seg button.on { background: var(--card); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.10); }
.ch-seg button:disabled { opacity: .3; cursor: default; }
.ch-btn { font: inherit; font-size: 12px; font-weight: 550; padding: 6px 12px;
  border-radius: 9px; cursor: pointer; border: 1px solid var(--sep);
  background: transparent; color: var(--text-2); transition: background .12s ease, border-color .12s ease, color .12s ease; }
.ch-btn:hover:not(:disabled) { color: var(--text); border-color: var(--text-3);
  background: color-mix(in srgb, var(--text) 4%, transparent); }
.ch-btn:active:not(:disabled) { transform: scale(.97); }
.ch-btn.on { color: var(--series); border-color: color-mix(in srgb,var(--series) 50%,var(--sep));
  background: color-mix(in srgb,var(--series) 8%,transparent); }
.ch-btn:disabled { opacity: .35; cursor: default; }

.ch-plot { position: relative; }
.ch svg { width: 100%; height: auto; display: block; overflow: visible; cursor: crosshair;
  touch-action: none; }
.ch-sub { margin-top: 4px; }
.ch-line { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round;
  stroke-linecap: round; vector-effect: non-scaling-stroke; }
.ch-envelope { fill: var(--series); opacity: .13; }
.ch-range { fill: var(--series); opacity: .13; stroke: none; }
.ch-up { stroke: var(--series); fill: var(--series); }
.ch-down { stroke: var(--text-2); fill: var(--card); }
.ch-body { stroke-width: 1; }
.ch-wick, .ch-tick2 { stroke-width: 1.4; vector-effect: non-scaling-stroke; fill: none; }
.ch-ma { fill: none; stroke: var(--text-2); stroke-width: 1.6; stroke-dasharray: 5 3;
  vector-effect: non-scaling-stroke; }
.ch-ema { fill: none; stroke: var(--text); stroke-width: 1.4; opacity: .7;
  vector-effect: non-scaling-stroke; }
.ch-pctband { fill: var(--series); opacity: .07; }
.ch-bollband { fill: var(--series); opacity: .08; }
.ch-bollmid { fill: none; stroke: var(--series); stroke-width: 1.2; stroke-dasharray: 2 3;
  opacity: .55; vector-effect: non-scaling-stroke; }
.ch-baseline-up { fill: var(--series); opacity: .16; }
.ch-baseline-down { fill: var(--text-2); opacity: .16; }
.ch-baseline-ref { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 4 3;
  vector-effect: non-scaling-stroke; }
.ch-hbar { fill: var(--series); opacity: .55; transition: opacity .12s ease; }
.ch-hbar-hot { opacity: .95; }
.ch-hist-ref { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }
.ch-hmcell { transition: opacity .1s ease; }
.ch-hmcell:hover { opacity: .82; }
.ch-hmcell-empty { fill: var(--sep); opacity: .35; }
.ch-gl { stroke: var(--sep); stroke-width: 1; vector-effect: non-scaling-stroke; }
.ch-ytick, .ch-tick, .ch-sublabel { fill: var(--text-2); font-size: 11px; font-family: inherit;
  font-variant-numeric: tabular-nums; }
.ch-vline, .ch-hline { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }
.ch-xgl { opacity: .58; }
.ch-dot2 { fill: var(--series); stroke: var(--card); stroke-width: 2; }
.ch-ybadge, .ch-xbadge { fill: var(--text); opacity: .94; }
.ch-badge-text { fill: var(--bg); font-size: 10px; font-weight: 700; font-family: inherit;
  font-variant-numeric: tabular-nums; }
.ch-latest-line { stroke: var(--series); stroke-width: 1; stroke-dasharray: 5 4;
  opacity: .72; vector-effect: non-scaling-stroke; }
.ch-latest-badge { fill: var(--series); opacity: .92; }
.ch-span { fill: var(--series); opacity: .13; }
.ch-sel { fill: var(--series); opacity: .15; }
.ch-nowline { stroke: var(--text-2); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }
.ch-sbar { fill: var(--series); opacity: .45; }
.ch-ivtag { font-size: 11px; font-weight: 640; letter-spacing: .04em;
  padding: 3px 8px; border-radius: 6px; align-self: center;
  background: color-mix(in srgb, var(--series) 14%, transparent); color: var(--series);
  font-variant-numeric: tabular-nums; }
.ch-legend { position: absolute; top: 10px; right: 14px; display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text-2); font-variant-numeric: tabular-nums; pointer-events: none; z-index: 4; }
.ch-legend-bar { width: 64px; height: 6px; border-radius: 3px;
  background: linear-gradient(to right, color-mix(in srgb, var(--series) 10%, var(--bg)),
    color-mix(in srgb, var(--series) 90%, var(--bg))); }
.ch-tip { position: absolute; z-index: 6; pointer-events: none; background: var(--card);
  border: 1px solid var(--sep); border-radius: 10px; padding: 8px 10px; font-size: 12px;
  box-shadow: 0 10px 28px rgba(0,0,0,.16); display: grid; grid-template-columns: auto auto;
  gap: 2px 10px; max-width: 220px; }
.ch-tip b { grid-column: 1 / -1; font-size: 12px; font-weight: 620; margin-bottom: 2px; }
.ch-tip i { font-style: normal; color: var(--text-2); }
.ch-tip span { font-weight: 590; font-variant-numeric: tabular-nums; text-align: right; }
.ch-hint { margin: 8px 0 0; font-size: 11px; color: var(--text-3); }
.ch-full-close { display: none; position: absolute; top: 18px; right: 22px; z-index: 11002;
  font: 600 12px inherit; color: var(--text); background: var(--card);
  border: 1px solid var(--sep); border-radius: 10px; padding: 7px 12px; cursor: pointer; }
.ch-full { position: fixed; inset: 12px; z-index: 11000; margin: 0; padding: 24px 28px 18px;
  border-radius: 18px; background: var(--card); box-shadow: 0 28px 90px rgba(0,0,0,.5);
  display: flex; flex-direction: column; overflow: auto; }
.ch-full .ch-head { padding-right: 90px; }
.ch-full .ch-plot { flex: 1; min-height: 420px; display: flex; flex-direction: column; }
.ch-full .ch-plot>svg:first-child { flex: 1; min-height: 380px; height: 100%; }
.ch-full .ch-full-close { display: block; }
.ch-full [data-act="expand"] { display: none; }
body.ch-lock { overflow: hidden; }
body.ch-lock::before { content: ""; position: fixed; inset: 0; z-index: 10900;
  background: rgba(0,0,0,.58); backdrop-filter: blur(3px); }
.ch svg[hidden], .ch [hidden] { display: none; }
@media (max-width: 900px) { .ch-stats, .ch-ohlc { display: none; } }
@media (max-width: 640px) { .ch-full { inset: 0; border-radius: 0; padding: 54px 12px 12px; }
  .ch-full .ch-full-close { top: 12px; right: 12px; } }
"""
