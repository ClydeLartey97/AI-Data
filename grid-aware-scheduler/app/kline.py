"""
KLineCharts panels, restyled for this app.

Ported from the National Grid Tool's `services/kline.py`, which had already
been through the painful part. That version renders into Streamlit; this one
emits plain HTML/JS, but the hard-won behaviour carries over unchanged because
it was never really Streamlit-specific:

- **Custom date/time formatters.** The library's defaults put a bare "HH:mm"
  on the axis with no date, and a terse "MM-DD" that reads ambiguously to
  anyone outside the US. These spell the month and keep the year.
- **Bucket-aware labels.** Once bars are daily or coarser, a clock time on the
  axis is noise — zoomed out you want dates, and times only matter inside a
  day. The formatter keys off the active bucket rather than always printing
  a time.
- **One source of truth for timezone.** `chart.setTimezone()` is the library's
  own mechanism and it rebuilds the internal formatter and forces a repaint,
  so formatters read `dtf.resolvedOptions().timeZone` — what setTimezone
  actually produced — instead of tracking toggle state separately.
- **Axis label weight is left alone.** The library styles every bottom-axis
  label as one group, so bolding the dates would bold the times too.

Why this library and not Plotly: these are dense half-hourly series over days
to months, where pan, zoom and a crosshair are the whole interaction. Plotly
is heavier, slower on that density, and hard to make not look like Plotly.

Why not use it for everything: it is a *financial candlestick* library. A
generation-mix breakdown or a fleet diagram is not an OHLC series, and forcing
those through it would be worse than drawing them directly. Those stay as
hand-written SVG in `dashboard.py`.

KLineChart v9.8.12, Apache-2.0, vendored at `app/static/vendor/`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "static" / "vendor" / "klinecharts.min.js"


@dataclass
class Series:
    """One value-over-time series to plot."""

    key: str
    label: str
    unit: str
    points: list[tuple[datetime, float | None]]
    color_var: str = "--price"      # CSS custom property holding the hue
    precision: int = 2

    def to_json(self) -> str:
        return json.dumps([
            {"timestamp": int(ts.timestamp() * 1000), "close": v,
             "open": v, "high": v, "low": v}
            for ts, v in self.points if v is not None
        ])


def library_tag() -> str:
    """Inline the vendored library. No CDN — the page must work offline."""
    return f"<script>{VENDOR.read_text(encoding='utf-8')}</script>"


def panel(series: Series, *, height: int = 300, chart_id: str | None = None) -> str:
    """One KLineCharts panel, themed from the page's CSS variables."""
    div_id = chart_id or f"kline-{series.key}"
    return f"""
<div class="kline-wrap">
  <div class="kline-head">
    <span class="kline-title" style="--series: var({series.color_var});">
      <span class="swatch"></span>{series.label}
    </span>
    <span class="kline-unit">{series.unit}</span>
    <span class="kline-tools">
      <button type="button" data-kl="{div_id}" data-act="area" class="on">Area</button>
      <button type="button" data-kl="{div_id}" data-act="candle">Candles</button>
      <button type="button" data-kl="{div_id}" data-act="zone">UTC</button>
    </span>
  </div>
  <div id="{div_id}" class="kline" style="height:{height}px"></div>
</div>
<script>
(function () {{
  var DATA = {series.to_json()};
  var ID = {json.dumps(div_id)};
  var PRECISION = {series.precision};

  function css(name) {{
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }}

  function start() {{
    if (!window.klinecharts) {{ return setTimeout(start, 60); }}
    var el = document.getElementById(ID);
    if (!el || !el.clientWidth) {{ return setTimeout(start, 60); }}

    var chart = klinecharts.init(ID);
    var hue = css("{series.color_var}") || "#007AFF";
    var ink = css("--text-2"), line = css("--sep"), surface = css("--card");

    chart.setStyles({{
      grid: {{
        horizontal: {{ show: true, style: "dashed", color: line }},
        vertical: {{ show: false }}
      }},
      candle: {{
        type: "area",
        area: {{
          lineSize: 2, lineColor: hue,
          backgroundColor: [
            {{ offset: 0, color: hue + "33" }},
            {{ offset: 1, color: hue + "05" }}
          ]
        }},
        priceMark: {{
          high: {{ show: false }}, low: {{ show: false }},
          last: {{ show: true, text: {{ size: 11 }} }}
        }},
        // The default tooltip lists open/high/low/close. For a line series
        // those are the same number four times, which is noise pretending to
        // be detail. Replaced with the two things a reader actually wants at
        // a given moment: when, and how much.
        tooltip: {{
          showRule: "follow_cross",
          showType: "standard",
          text: {{ size: 12, color: ink, marginLeft: 0, marginTop: 6 }},
          custom: function (data) {{
            var v = (data && data.current && data.current.close);
            return [
              {{ title: {json.dumps(series.label)} + ": ",
                 value: (v === undefined || v === null)
                   ? "no data"
                   : v.toLocaleString("en-GB", {{
                       minimumFractionDigits: PRECISION,
                       maximumFractionDigits: PRECISION }}) + " {series.unit}" }}
            ];
          }}
        }}
      }},
      xAxis: {{
        axisLine: {{ show: true, color: line, size: 1 }},
        tickLine: {{ show: true, color: line, size: 1, length: 4 }},
        tickText: {{ show: true, color: ink, size: 11 }}
      }},
      yAxis: {{
        position: "left", inside: false,
        axisLine: {{ show: true, color: line, size: 1 }},
        tickLine: {{ show: true, color: line, size: 1, length: 4 }},
        tickText: {{ show: true, color: ink, size: 11 }}
      }},
      crosshair: {{
        horizontal: {{ line: {{ color: ink }}, text: {{ backgroundColor: hue }} }},
        vertical:   {{ line: {{ color: ink }}, text: {{ backgroundColor: hue }} }}
      }},
      separator: {{ color: line }}
    }});

    // --- formatters ------------------------------------------------------
    // The library's defaults are ambiguous outside the US. These spell the
    // month, keep the year, and drop clock times once bars are daily or
    // coarser, where a time would be meaningless.
    var _zone = null, _time, _day, _month, _full, _dateOnly;
    var _bucketMs = 1800000;

    function ensure(zone) {{
      if (zone === _zone) return;
      _zone = zone;
      var z = zone ? {{ timeZone: zone }} : {{}};
      var A = Object.assign;
      _time     = new Intl.DateTimeFormat("en-GB", A({{ hour: "2-digit", minute: "2-digit", hour12: false }}, z));
      _day      = new Intl.DateTimeFormat("en-GB", A({{ day: "numeric", month: "short" }}, z));
      _month    = new Intl.DateTimeFormat("en-GB", A({{ month: "short", year: "numeric" }}, z));
      _full     = new Intl.DateTimeFormat("en-GB", A({{ weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }}, z));
      _dateOnly = new Intl.DateTimeFormat("en-GB", A({{ weekday: "short", day: "numeric", month: "short", year: "numeric" }}, z));
    }}

    // v9 exposes this as setCustomApi, NOT setFormatter. Calling the wrong
    // one throws, and because it sits before applyNewData the chart renders
    // its axes and no data at all — a silent-looking failure that is really
    // an exception. Verified against the vendored bundle's exported names.
    chart.setCustomApi({{
      formatDate: function (dtf, timestamp, format, type) {{
        ensure(dtf.resolvedOptions().timeZone);
        var d = new Date(timestamp);
        var daily = _bucketMs >= 86400000;
        if (type === "crosshair" || type === "tooltip") {{
          return daily ? _dateOnly.format(d) : _full.format(d);
        }}
        if (daily) return _bucketMs >= 2592000000 ? _month.format(d) : _day.format(d);
        return d.getUTCHours() === 0 && d.getUTCMinutes() === 0
          ? _day.format(d) : _time.format(d);
      }}
    }});

    chart.applyNewData(DATA);
    chart.setPriceVolumePrecision(PRECISION, 0);
    chart.setTimezone("UTC");

    // --- toolbar ---------------------------------------------------------
    var zoneUTC = true;
    document.querySelectorAll('[data-kl="' + ID + '"]').forEach(function (btn) {{
      btn.addEventListener("click", function () {{
        var act = btn.dataset.act;
        if (act === "zone") {{
          zoneUTC = !zoneUTC;
          chart.setTimezone(zoneUTC ? "UTC" : Intl.DateTimeFormat().resolvedOptions().timeZone);
          btn.textContent = zoneUTC ? "UTC" : "Local";
          return;
        }}
        chart.setStyles({{ candle: {{ type: act === "candle" ? "candle_solid" : "area" }} }});
        btn.parentNode.querySelectorAll("[data-act]").forEach(function (b) {{
          if (b.dataset.act !== "zone") b.classList.toggle("on", b === btn);
        }});
      }});
    }});

    // Repaint on theme change so the chart follows light/dark like the page.
    if (window.matchMedia) {{
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {{
        setTimeout(function () {{ location.reload(); }}, 50);
      }});
    }}
    new ResizeObserver(function () {{ chart.resize(); }}).observe(el);
  }}
  start();
}})();
</script>"""


PANEL_CSS = """
.kline-wrap { margin: 4px 0 2px; }
.kline-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.kline-title { display: inline-flex; align-items: center; gap: 7px;
  font-size: 14px; font-weight: 590; letter-spacing: -0.01em; }
.kline-title .swatch { width: 10px; height: 10px; border-radius: 3px; background: var(--series); }
.kline-unit { font-size: 13px; color: var(--text-2); }
.kline-tools { margin-left: auto; display: inline-flex; gap: 6px; }
.kline-tools button {
  font: inherit; font-size: 12px; font-weight: 510; letter-spacing: -0.005em;
  padding: 4px 11px; border-radius: 980px; cursor: pointer;
  border: 1px solid var(--sep); background: transparent; color: var(--text-2);
  transition: background .15s ease, color .15s ease;
}
.kline-tools button:hover { background: color-mix(in srgb, var(--text) 6%, transparent); }
.kline-tools button.on { background: var(--price); border-color: transparent; color: #fff; }
.kline { width: 100%; }
"""
