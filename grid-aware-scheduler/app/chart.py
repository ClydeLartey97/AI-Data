"""
Time-series chart — a trading terminal built for grid data.

Feature parity with a financial charting library, then past it where the
domain differs:

**Parity.** Area / line / candle / bar rendering, wheel zoom centred on the
cursor, drag to pan, shift-drag box zoom, moving averages, a crosshair reading
full OHLC, timezone switching, keyboard control, CSV export.

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
      </span>
    </span>
    <span class="ch-grp" role="group" aria-label="Overlays">
      <span class="ch-lbl">Overlay</span>
      <span class="ch-seg ch-overlays">
        <button type="button" data-ov="ma">MA</button>
        <button type="button" data-ov="ema">EMA</button>
        <button type="button" data-ov="pct">Percentiles</button>
        <button type="button" data-ov="spread">Spread</button>
      </span>
    </span>
    <span class="ch-grp ch-right">
      <button type="button" class="ch-btn" data-act="zone">UTC</button>
      <button type="button" class="ch-btn" data-act="reset">Reset</button>
      <button type="button" class="ch-btn" data-act="csv">CSV</button>
    </span>
  </div>

  <div class="ch-plot">
    <svg viewBox="0 0 1000 __H__" preserveAspectRatio="none" role="img"
         aria-label="__LABEL__ over time in __UNIT__">
      <g class="ch-grid"></g>
      <g class="ch-spans"></g>
      <path class="ch-envelope" d=""/>
      <g class="ch-pct" hidden><path class="ch-pctband" d=""/></g>
      <g class="ch-marks"></g>
      <path class="ch-ma" d="" hidden/>
      <path class="ch-ema" d="" hidden/>
      <g class="ch-now" hidden><line class="ch-nowline"/></g>
      <rect class="ch-sel" hidden/>
      <g class="ch-cross" hidden>
        <line class="ch-vline"/><line class="ch-hline"/>
        <circle class="ch-dot2" r="4"/>
      </g>
      <g class="ch-yaxis"></g>
      <g class="ch-axis"></g>
    </svg>
    <svg class="ch-sub" viewBox="0 0 1000 __HSUB__" preserveAspectRatio="none"
         hidden aria-label="High-low spread per interval">
      <g class="ch-subbars"></g>
      <text class="ch-sublabel" x="4" y="11">spread (high − low)</text>
    </svg>
  </div>
  <p class="ch-hint">Scroll to zoom · drag to pan · shift-drag to select · double-click to reset</p>
</figure>"""


_JS = r"""
(function () {
  var P = __POINTS__;                 // native half-hourly series
  var CFG = __CFG__;

  var root = document.getElementById(CFG.id);
  var svg = root.querySelector("svg");
  var sub = root.querySelector(".ch-sub");
  var q = function (s) { return root.querySelector(s); };
  var W = 1000, H = CFG.height, PAD_T = 8, PAD_B = 26, PAD_L = 52;
  var SUB_H = __HSUB__;

  var LAST = P.length ? P[P.length - 1].t : 0;
  var FIRST = P.length ? P[0].t : 0;

  // Zoom is a TIME WINDOW, not an index pair, so it survives changes to the
  // candle interval. Switching from 1h to 6h candles keeps you looking at the
  // same stretch of time, which is what every trading platform does.
  var S = { range: CFG.start, interval: 0, type: "area", win: null, utc: true,
            ma: false, ema: false, pct: false, spread: false };
  var view = null;

  var fmt = new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: CFG.precision, maximumFractionDigits: CFG.precision });
  function num(v) { return CFG.prefix + fmt.format(v); }

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

  function sma(v, n) { var o = [], s = 0; for (var i = 0; i < v.length; i++) {
    s += v[i]; if (i >= n) s -= v[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
  function ema(v, n) { var o = [], k = 2 / (n + 1), pr = null;
    for (var i = 0; i < v.length; i++) { pr = pr === null ? v[i] : v[i] * k + pr * (1 - k); o.push(pr); }
    return o; }
  function quantile(s, p) { if (!s.length) return 0;
    var i = (s.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return s[lo] + (s[hi] - s[lo]) * (i - lo); }

  function niceStep(range, want) {
    var raw = range / Math.max(want, 1); if (raw <= 0) return 1;
    var mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
  }

  function draw() {
    var win = windowMs(), iv = interval(win);
    var pts = bucket(win, iv), n = pts.length;
    if (!n) return;

    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < n; i++) { if (pts[i].l < lo) lo = pts[i].l; if (pts[i].h > hi) hi = pts[i].h; }
    var pad = (hi - lo || 1) * 0.10, yLo = lo - pad, yHi = hi + pad;

    function X(i) { return n < 2 ? (PAD_L + W) / 2 : PAD_L + (i / (n - 1)) * (W - PAD_L); }
    function Y(v) { return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - yLo) / (yHi - yLo)); }
    var bw = Math.max(1.2, (W - PAD_L) / Math.max(n, 1) * 0.62);

    var step = niceStep(yHi - yLo, 5), g = "", yt = "";
    for (var v = Math.ceil(yLo / step) * step; v <= yHi; v += step) {
      var yy = Y(v); if (yy < PAD_T || yy > H - PAD_B) continue;
      g += '<line class="ch-gl" x1="' + PAD_L + '" y1="' + yy.toFixed(1) +
           '" x2="' + W + '" y2="' + yy.toFixed(1) + '"/>';
      yt += '<text class="ch-ytick" x="' + (PAD_L - 6) + '" y="' + (yy + 3.5).toFixed(1) +
            '" text-anchor="end">' + num(v) + "</text>";
    }
    q(".ch-grid").innerHTML = g; q(".ch-yaxis").innerHTML = yt;

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
        for (var b = n - 1; b >= 0; b--) dn += "L" + X(b).toFixed(1) + "," + Y(pts[b].l).toFixed(1);
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
    maEl.hidden = !S.ma; emEl.hidden = !S.ema;
    if (S.ma) maEl.setAttribute("d", pathOf(sma(means, win2)));
    if (S.ema) emEl.setAttribute("d", pathOf(ema(means, win2)));

    q(".ch-pct").hidden = !S.pct;
    if (S.pct) {
      var sorted = means.slice().sort(function (x, y) { return x - y; });
      var p10 = quantile(sorted, 0.10), p90 = quantile(sorted, 0.90);
      q(".ch-pctband").setAttribute("d", "M" + PAD_L + "," + Y(p90).toFixed(1) +
        "L" + W + "," + Y(p90).toFixed(1) + "L" + W + "," + Y(p10).toFixed(1) +
        "L" + PAD_L + "," + Y(p10).toFixed(1) + "Z");
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
      nl.setAttribute("y1", PAD_T); nl.setAttribute("y2", H - PAD_B); ng.hidden = false;
    } else ng.hidden = true;

    var fine = (t1 - t0) <= 3 * 86400000;
    var want = Math.min(7, n), ax = "";
    for (var m = 0; m < want; m++) {
      var idx = Math.round(m * (n - 1) / Math.max(want - 1, 1));
      var o = fine ? { hour: "2-digit", minute: "2-digit", hour12: false }
                   : ((t1 - t0) > 120 * 86400000 ? { month: "short", year: "numeric" }
                                                 : { day: "numeric", month: "short" });
      if (S.utc) o.timeZone = "UTC";
      ax += '<text class="ch-tick" x="' + X(idx).toFixed(1) + '" y="' + (H - 8) +
            '" text-anchor="' + (m === 0 ? "start" : m === want - 1 ? "end" : "middle") + '">' +
            new Intl.DateTimeFormat("en-GB", o).format(new Date(pts[idx].t)) + "</text>";
    }
    q(".ch-axis").innerHTML = ax;

    sub.hidden = !S.spread;
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
      st("Spread", lo > 0 ? (hi / lo).toFixed(1) + "\u00d7" : "\u2014") +
      st("Candles", String(n));

    var ivLabel = iv >= 86400000 ? (iv / 86400000) + "D" :
                  iv >= 3600000 ? (iv / 3600000) + "h" : (iv / 60000) + "m";
    q("[data-iv]").textContent = ivLabel;

    view = { pts: pts, X: X, Y: Y, n: n, fine: fine, win: win, iv: iv };
    readout(pts[n - 1]);
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

  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
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
    drag = { x: e.clientX, shift: e.shiftKey, win: windowMs(), moved: false };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", function (e) {
    if (view) {
      var i = idxAt(e.clientX), p = view.pts[i];
      q(".ch-cross").hidden = false;
      var x = view.X(i), y = view.Y(p.m);
      var vl = q(".ch-vline"), hl = q(".ch-hline");
      vl.setAttribute("x1", x); vl.setAttribute("x2", x);
      vl.setAttribute("y1", PAD_T); vl.setAttribute("y2", H - PAD_B);
      hl.setAttribute("x1", PAD_L); hl.setAttribute("x2", W);
      hl.setAttribute("y1", y); hl.setAttribute("y2", y);
      q(".ch-dot2").setAttribute("cx", x); q(".ch-dot2").setAttribute("cy", y);
      readout(p);
    }
    if (!drag) return;
    drag.moved = true;
    var r = svg.getBoundingClientRect();
    if (drag.shift) {
      var x1 = Math.min(drag.x, e.clientX) - r.left, x2 = Math.max(drag.x, e.clientX) - r.left;
      var sel = q(".ch-sel"); sel.hidden = false;
      sel.setAttribute("x", (x1 / r.width * W).toFixed(1));
      sel.setAttribute("width", ((x2 - x1) / r.width * W).toFixed(1));
      sel.setAttribute("y", PAD_T); sel.setAttribute("height", H - PAD_T - PAD_B);
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
    q(".ch-sel").hidden = true;
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
    q(".ch-cross").hidden = true; if (view) readout(view.pts[view.n - 1]);
  });
  svg.addEventListener("dblclick", function () { S.win = null; draw(); });

  root.addEventListener("keydown", function (e) {
    var win = windowMs(), span = win[1] - win[0], stp = span * 0.15;
    if (e.key === "ArrowLeft") { S.win = [Math.max(FIRST, win[0] - stp), win[1] - stp]; }
    else if (e.key === "ArrowRight") { S.win = [win[0] + stp, Math.min(LAST, win[1] + stp)]; }
    else if (e.key === "+" || e.key === "=") { S.win = [win[0] + stp, win[1] - stp]; }
    else if (e.key === "-") { S.win = [Math.max(FIRST, win[0] - stp), Math.min(LAST, win[1] + stp)]; }
    else if (e.key.toLowerCase() === "r") { S.win = null; }
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
  seg(".ch-ranges", "r", function (v, b) { exclusive(".ch-ranges", b); S.range = v; S.win = null; draw(); });
  seg(".ch-intervals", "iv", function (v, b) { exclusive(".ch-intervals", b); S.interval = +v; draw(); });
  seg(".ch-types", "type", function (v, b) { exclusive(".ch-types", b); S.type = v; draw(); });
  seg(".ch-overlays", "ov", function (v, b) { S[v] = !S[v]; b.classList.toggle("on", S[v]); draw(); });

  root.querySelectorAll(".ch-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      var a = b.dataset.act;
      if (a === "reset") { S.win = null; draw(); }
      else if (a === "zone") { S.utc = !S.utc; b.textContent = S.utc ? "UTC" : "Local"; draw(); }
      else if (a === "csv") {
        var rows = [["interval_start", "open", "high", "low", "close", "mean", "samples"]];
        view.pts.forEach(function (p) {
          rows.push([new Date(p.t).toISOString(), p.o, p.h, p.l, p.c, p.m, p.n]); });
        var csv = rows.map(function (r) { return r.join(","); }).join(String.fromCharCode(10));
        var el = document.createElement("a");
        el.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        el.download = CFG.key + "_" + S.range + ".csv"; el.click();
        URL.revokeObjectURL(el.href);
      }
    });
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
.ch-seg { display: inline-flex; gap: 2px; padding: 3px; border-radius: 980px;
  background: color-mix(in srgb, var(--text) 5%, transparent); }
.ch-seg button { font: inherit; font-size: 12px; font-weight: 550; padding: 5px 11px;
  border: 0; border-radius: 980px; background: transparent; color: var(--text-2);
  cursor: pointer; transition: background .15s ease, color .15s ease; }
.ch-seg button:hover:not(:disabled) { color: var(--text); }
.ch-seg button.on { background: var(--card); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.10); }
.ch-seg button:disabled { opacity: .3; cursor: default; }
.ch-btn { font: inherit; font-size: 12px; font-weight: 550; padding: 6px 12px;
  border-radius: 980px; cursor: pointer; border: 1px solid var(--sep);
  background: transparent; color: var(--text-2); }
.ch-btn:hover { color: var(--text); border-color: var(--text-3); }

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
.ch-gl { stroke: var(--sep); stroke-width: 1; vector-effect: non-scaling-stroke; }
.ch-ytick, .ch-tick, .ch-sublabel { fill: var(--text-2); font-size: 11px; font-family: inherit;
  font-variant-numeric: tabular-nums; }
.ch-vline, .ch-hline { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }
.ch-dot2 { fill: var(--series); stroke: var(--card); stroke-width: 2; }
.ch-span { fill: var(--series); opacity: .13; }
.ch-sel { fill: var(--series); opacity: .15; }
.ch-nowline { stroke: var(--text-2); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }
.ch-sbar { fill: var(--series); opacity: .45; }
.ch-ivtag { font-size: 11px; font-weight: 640; letter-spacing: .04em;
  padding: 3px 8px; border-radius: 6px; align-self: center;
  background: color-mix(in srgb, var(--series) 14%, transparent); color: var(--series);
  font-variant-numeric: tabular-nums; }
.ch-hint { margin: 8px 0 0; font-size: 11px; color: var(--text-3); }
svg [hidden] { display: none; }
@media (max-width: 900px) { .ch-stats, .ch-ohlc { display: none; } }
"""
