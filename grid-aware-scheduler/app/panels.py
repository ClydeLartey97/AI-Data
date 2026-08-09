"""
Analytical panels — compact SVG for the questions an operator asks once.

These are not time series and deliberately not interactive. A duration curve
or a time-of-day profile is a conclusion drawn over a year; hovering it adds
nothing, and giving every panel a toolbar would bury the two charts where
interaction genuinely matters.

Written for a data-centre operator with a carbon target, so each panel answers
something that changes a decision:

- What is a longer deadline actually worth?  (savings curve)
- When in the day should flexible work run?  (profile)
- How much of the year is expensive or dirty? (duration curve)
- Does optimising cost also get me carbon?    (price-carbon)
"""
from __future__ import annotations

import html

from core.analytics import Profile, SavingsCurve


def _axis_num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v/1000:,.1f}k"
    return f"{v:,.0f}"


def _frame(w: int, h: int, pad_l: int, pad_b: int, lo: float, hi: float,
           ticks: int = 4) -> tuple[str, callable]:
    span = (hi - lo) or 1
    def Y(v: float) -> float:
        return 6 + (h - 6 - pad_b) * (1 - (v - lo) / span)
    grid = "".join(
        f'<line class="p-gl" x1="{pad_l}" y1="{Y(lo + span*i/ticks):.1f}" '
        f'x2="{w}" y2="{Y(lo + span*i/ticks):.1f}"/>'
        f'<text class="p-yt" x="{pad_l-5}" y="{Y(lo + span*i/ticks)+3:.1f}" '
        f'text-anchor="end">{_axis_num(lo + span*i/ticks)}</text>'
        for i in range(ticks + 1))
    return grid, Y


def savings_panel(curve: SavingsCurve, *, unit: str = "cost") -> str:
    """What a longer deadline is worth — the product's central claim."""
    if not curve.deadlines:
        return '<p class="empty">Not enough history.</p>'
    w, h, pad_l, pad_b = 340, 150, 32, 18
    hi = max(max(curve.best), 1)
    grid, Y = _frame(w, h, pad_l, pad_b, 0, hi)
    n = len(curve.deadlines)
    def X(i): return pad_l + (w - pad_l - 6) * (i / max(n - 1, 1))

    def path(vals):
        return " ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(v):.1f}"
                        for i, v in enumerate(vals))
    band = (path(curve.best) + " " +
            " ".join(f"L{X(i):.1f},{Y(v):.1f}" for i, v in reversed(list(enumerate(curve.median)))) + " Z")
    labels = "".join(
        f'<text class="p-xt" x="{X(i):.1f}" y="{h-4}" text-anchor="middle">'
        f'{d:.0f}h</text>'
        for i, d in enumerate(curve.deadlines) if i % max(1, n // 5) == 0 or i == n - 1)
    pts = "".join(f'<circle class="p-dot" cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.5"/>'
                  for i, v in enumerate(curve.median))
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var(--blue)"
      role="img" aria-label="Savings against deadline length">{grid}
      <path class="p-band" d="{band}"/>
      <path class="p-line p-dim" d="{path(curve.best)}"/>
      <path class="p-line" d="{path(curve.median)}"/>{pts}{labels}
      <text class="p-key" x="{pad_l+2}" y="14">median · best case ({html.escape(unit)})</text>
    </svg>"""


def profile_panel(profile: Profile, *, color: str = "--price") -> str:
    """Mean by hour with a p10-p90 band — when flexible work should run."""
    if not profile.hours:
        return '<p class="empty">No data.</p>'
    w, h, pad_l, pad_b = 340, 150, 34, 18
    lo, hi = min(profile.p10), max(profile.p90)
    grid, Y = _frame(w, h, pad_l, pad_b, lo, hi)
    n = len(profile.hours)
    def X(i): return pad_l + (w - pad_l - 6) * (i / max(n - 1, 1))
    band = (" ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(v):.1f}"
                     for i, v in enumerate(profile.p90)) + " " +
            " ".join(f"L{X(i):.1f},{Y(v):.1f}"
                     for i, v in reversed(list(enumerate(profile.p10)))) + " Z")
    line = " ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(v):.1f}"
                    for i, v in enumerate(profile.mean))
    best = min(range(n), key=lambda i: profile.mean[i])
    ticks = "".join(
        f'<text class="p-xt" x="{X(i):.1f}" y="{h-4}" text-anchor="middle">'
        f'{profile.hours[i]:02d}</text>'
        for i in range(0, n, max(1, n // 6)))
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var({color})"
      role="img" aria-label="Average by hour of day">{grid}
      <path class="p-band" d="{band}"/><path class="p-line" d="{line}"/>
      <line class="p-mark" x1="{X(best):.1f}" y1="6" x2="{X(best):.1f}" y2="{h-pad_b}"/>
      <text class="p-key" x="{X(best)+4:.1f}" y="14">best {profile.hours[best]:02d}:00</text>
      {ticks}</svg>"""


def duration_panel(curve: list[tuple[float, float]], *, color: str = "--price") -> str:
    """Every half-hour of the year sorted worst to best."""
    if not curve:
        return '<p class="empty">No data.</p>'
    w, h, pad_l, pad_b = 340, 150, 34, 18
    vals = [v for _, v in curve]
    grid, Y = _frame(w, h, pad_l, pad_b, min(vals), max(vals))
    def X(p): return pad_l + (w - pad_l - 6) * (p / 100.0)
    line = " ".join(f"{'M' if i==0 else 'L'}{X(p):.1f},{Y(v):.1f}"
                    for i, (p, v) in enumerate(curve))
    area = f"M{X(0):.1f},{Y(min(vals)):.1f} " + line[1:] + f" L{X(100):.1f},{Y(min(vals)):.1f} Z"
    ticks = "".join(f'<text class="p-xt" x="{X(p):.1f}" y="{h-4}" text-anchor="middle">{p:.0f}%</text>'
                    for p in (0, 25, 50, 75, 100))
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var({color})"
      role="img" aria-label="Duration curve">{grid}
      <path class="p-fill" d="{area}"/><path class="p-line" d="{line}"/>{ticks}
      <text class="p-key" x="{pad_l+2}" y="14">% of the year at or above</text></svg>"""


def scatter_panel(corr: dict) -> str:
    """Price against carbon. The spread is the point: cheap is not clean."""
    if not corr.get("n"):
        return '<p class="empty">No data.</p>'
    w, h, pad_l, pad_b = 340, 150, 34, 18
    pts = corr["scatter"]
    xs = [a for a, _ in pts]; ys = [b for _, b in pts]
    xlo, xhi = min(xs), max(xs); ylo, yhi = min(ys), max(ys)
    grid, Y = _frame(w, h, pad_l, pad_b, ylo, yhi)
    def X(v): return pad_l + (w - pad_l - 6) * ((v - xlo) / ((xhi - xlo) or 1))
    dots = "".join(f'<circle class="p-pt" cx="{X(a):.1f}" cy="{Y(b):.1f}" r="1.4"/>'
                   for a, b in pts)
    qx, qy = X(corr["price_p10"]), Y(corr["carbon_p10"])
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var(--price)"
      role="img" aria-label="Price against carbon intensity">{grid}{dots}
      <line class="p-mark" x1="{qx:.1f}" y1="6" x2="{qx:.1f}" y2="{h-pad_b}"/>
      <line class="p-mark" x1="{pad_l}" y1="{qy:.1f}" x2="{w}" y2="{qy:.1f}"/>
      <text class="p-key" x="{pad_l+2}" y="14">r = {corr['r']:.2f}</text>
      <text class="p-xt" x="{w-4}" y="{h-4}" text-anchor="end">price →</text></svg>"""


EXPAND_JS = """
// Click a panel to expand it. The SVGs are viewBox-based, so scaling is free —
// the panel simply becomes a fixed overlay and the same markup fills the
// screen. Escape or a second click closes it.
(function () {
  var open = null;
  function close() {
    if (!open) return;
    open.classList.remove("pnl-open");
    document.body.classList.remove("pnl-lock");
    open = null;
  }
  document.addEventListener("click", function (e) {
    var pnl = e.target.closest ? e.target.closest(".pnl") : null;
    if (!pnl) { if (open && !e.target.closest(".pnl-open")) close(); return; }
    if (pnl === open) { close(); return; }
    close();
    open = pnl;
    pnl.classList.add("pnl-open");
    document.body.classList.add("pnl-lock");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
})();
"""


PANEL_CSS = """
.panel { width: 100%; height: auto; display: block; }
.p-gl { stroke: var(--sep); stroke-width: 1; vector-effect: non-scaling-stroke; }
.p-yt, .p-xt { fill: var(--text-3); font-size: 9px; font-variant-numeric: tabular-nums; }
.p-key { fill: var(--text-2); font-size: 9px; font-weight: 600; letter-spacing: .02em; }
.p-line { fill: none; stroke: var(--series, var(--blue)); stroke-width: 1.8;
  stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.p-dim { opacity: .32; stroke-dasharray: 3 2; }
.p-band, .p-fill { fill: var(--series, var(--blue)); opacity: .14; }
.p-dot { fill: var(--series, var(--blue)); }
.p-pt { fill: var(--series, var(--blue)); opacity: .34; }
.p-mark { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke; }

/* expand-on-click */
.pnl { cursor: zoom-in; transition: box-shadow .15s ease, transform .15s ease; }
.pnl:hover { box-shadow: 0 0 0 1px var(--sep), 0 6px 20px rgba(0,0,0,.10); }
.pnl::after { content: "⤢"; position: absolute; top: 10px; right: 12px; font-size: 11px;
  color: var(--text-3); opacity: 0; transition: opacity .15s ease; }
.pnl { position: relative; }
.pnl:hover::after { opacity: 1; }
.pnl-open { position: fixed; inset: 3vh 3vw; z-index: 500; cursor: zoom-out;
  overflow: auto; box-shadow: 0 24px 80px rgba(0,0,0,.45); border-radius: 16px;
  padding: 26px 30px; display: flex; flex-direction: column; }
.pnl-open .panel { flex: 1; min-height: 0; height: auto; }
.pnl-open h3 { font-size: 15px; margin-bottom: 16px; }
.pnl-open .pnl-note { font-size: 14px; max-width: 70ch; }
.pnl-open::after { content: "esc"; opacity: .7; font-size: 10px; letter-spacing: .1em; }
body.pnl-lock { overflow: hidden; }
body.pnl-lock::before { content: ""; position: fixed; inset: 0; z-index: 400;
  background: rgba(0,0,0,.55); backdrop-filter: blur(2px); }
"""
