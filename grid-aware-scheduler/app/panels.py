"""
Analytical panels — compact SVG for the questions an operator asks once.

These are compact analytical summaries. Every panel can be opened into a
full-screen inspection view with a mouse, keyboard, or touch input. The SVG
uses a viewBox, so the same chart remains sharp at either size.

Written for a data-centre operator with a carbon target, so each panel answers
something that changes a decision:

- What is a longer deadline actually worth?  (savings curve)
- When in the day should flexible work run?  (profile)
- How much of the year is expensive or dirty? (duration curve)
- Does optimising cost also get me carbon?    (price-carbon)
"""
from __future__ import annotations

import html
import json

from core.analytics import Profile, SavingsCurve
from app import template


def _inspector(payload: dict) -> str:
    """Embed chart data as an inert, HTML-safe inspector contract."""
    return html.escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       quote=True)


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


def savings_panel(curve: SavingsCurve, *, unit: str = "cost", color: str = "--blue") -> str:
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
    payload = {
        "kind": "line", "xLabel": "Deadline", "xSuffix": " h", "xPrecision": 0,
        "yLabel": unit.lstrip("% ").capitalize(), "ySuffix": "%", "precision": 2,
        "series": [
            {"name": "Median", "color": color,
             "points": [[x, y] for x, y in zip(curve.deadlines, curve.median)]},
            {"name": "Best case", "color": "--orange", "dash": True,
             "points": [[x, y] for x, y in zip(curve.deadlines, curve.best)]},
        ],
        "band": {
            "name": "Median to best case", "color": color,
            "low": [[x, y] for x, y in zip(curve.deadlines, curve.median)],
            "high": [[x, y] for x, y in zip(curve.deadlines, curve.best)],
        },
    }
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var({color})"
      data-inspector="{_inspector(payload)}"
      role="img" aria-label="Savings against deadline length">{grid}
      <path class="p-band" d="{band}"/>
      <path class="p-line p-dim" d="{path(curve.best)}"/>
      <path class="p-line" d="{path(curve.median)}"/>{pts}{labels}
      <text class="p-key" x="{pad_l+2}" y="14">median · best case ({html.escape(unit)})</text>
    </svg>"""


def profile_panel(profile: Profile, *, color: str = "--price",
                  label: str = "Signal", unit: str = "") -> str:
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
    payload = {
        "kind": "line", "xLabel": "Hour of day", "xSuffix": ":00", "xPrecision": 0,
        "yLabel": label, "ySuffix": unit, "precision": 2,
        "series": [{"name": "Mean", "color": color,
                    "points": [[x, y] for x, y in zip(profile.hours, profile.mean)]}],
        "band": {
            "name": "p10 to p90", "color": color,
            "low": [[x, y] for x, y in zip(profile.hours, profile.p10)],
            "high": [[x, y] for x, y in zip(profile.hours, profile.p90)],
        },
        "guides": [{"axis": "x", "value": profile.hours[best],
                    "label": f"Best {profile.hours[best]:02d}:00"}],
    }
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var({color})"
      data-inspector="{_inspector(payload)}"
      role="img" aria-label="Average by hour of day">{grid}
      <path class="p-band" d="{band}"/><path class="p-line" d="{line}"/>
      <line class="p-mark" x1="{X(best):.1f}" y1="6" x2="{X(best):.1f}" y2="{h-pad_b}"/>
      <text class="p-key" x="{X(best)+4:.1f}" y="14">best {profile.hours[best]:02d}:00</text>
      {ticks}</svg>"""


def duration_panel(curve: list[tuple[float, float]], *, color: str = "--price",
                   label: str = "Signal", unit: str = "") -> str:
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
    payload = {
        "kind": "line", "xLabel": "Time at or above", "xSuffix": "%", "xPrecision": 0,
        "yLabel": label, "ySuffix": unit, "precision": 2, "area": True,
        "series": [{"name": label, "color": color,
                    "points": [[x, y] for x, y in curve]}],
    }
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var({color})"
      data-inspector="{_inspector(payload)}"
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
    payload = {
        "kind": "scatter", "xLabel": "Price", "yLabel": "Carbon intensity",
        "ySuffix": " gCO₂/kWh", "precision": 2,
        "series": [{"name": "Observed interval", "color": "--price",
                    "points": [[x, y] for x, y in pts]}],
        "guides": [
            {"axis": "x", "value": corr["price_p10"], "label": "Price p10"},
            {"axis": "y", "value": corr["carbon_p10"], "label": "Carbon p10"},
        ],
    }
    return f"""<svg class="panel" viewBox="0 0 {w} {h}" style="--series:var(--price)"
      data-inspector="{_inspector(payload)}"
      role="img" aria-label="Price against carbon intensity">{grid}{dots}
      <line class="p-mark" x1="{qx:.1f}" y1="6" x2="{qx:.1f}" y2="{h-pad_b}"/>
      <line class="p-mark" x1="{pad_l}" y1="{qy:.1f}" x2="{w}" y2="{qy:.1f}"/>
      <text class="p-key" x="{pad_l+2}" y="14">r = {corr['r']:.2f}</text>
      <text class="p-xt" x="{w-4}" y="{h-4}" text-anchor="end">price →</text></svg>"""


EXPAND_JS = template.load("panels.js")
PANEL_CSS = template.load("panels.css")
