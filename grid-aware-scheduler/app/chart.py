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

**On implementation:** the markup and JavaScript live in ``app/templates/`` with
``__TOKEN__`` placeholders, not in an f-string. Brace-escaping inside an f-string previously
emitted a raw newline into a JS string literal — a syntax error that silently
blanked every chart. Removing the f-string removes that whole class of bug.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime

from core.resample import RANGES
from app import template


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

    body = template.fill(_MARKUP, {
        "__ID__": cid,
        "__LABEL__": html.escape(series.label),
        "__UNIT__": html.escape(series.unit),
        "__PILLS__": pills,
        "__INTERVALS__": intervals,
        "__COLOR__": series.color_var,
        "__H__": height,
        "__HSUB__": _SUB_H,
    })
    script = template.fill(_JS, {
        "__POINTS__": json.dumps(points),
        "__CFG__": json.dumps(cfg),
        "__HSUB__": _SUB_H,
    })
    return body + "\n<script>\n" + script + "\n</script>"


#: Candle intervals, in seconds. 0 = automatic, chosen so the visible window
#: lands near a readable number of candles.
INTERVALS = [(0, "Auto"), (1800, "30m"), (3600, "1h"), (7200, "2h"),
             (21600, "6h"), (86400, "1D"), (604800, "1W")]


#: Height of the spread sub-pane, in viewBox units.
_SUB_H = 64


_MARKUP = template.load("chart.html")
_JS = template.load("chart.js")


CHART_CSS = template.load("chart.css")
