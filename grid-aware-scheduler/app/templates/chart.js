
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
