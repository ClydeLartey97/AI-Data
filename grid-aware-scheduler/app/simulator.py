"""
Page 2 — the model-on-hardware simulator.

**The estimator runs in the page, not on the server.** The first version
precomputed every (model, task, device, count) combination and embedded the
lot, which capped the catalogue at whatever the payload could carry and made a
custom model impossible — you cannot precompute a number nobody has typed yet.
Now the device and model specs ship and the arithmetic happens on each control
change. The maths is a dozen lines; precomputing it was the expensive way to
do less.

That change is what allows a real catalogue, arbitrary fleet sizes, any
precision, and a custom model defined by parameter count alone.

Provenance stays visible: SPEC for datasheet-backed hardware, ESTIMATED for
Apple, where no vendor figures for GPU throughput or package power exist.
"""
from __future__ import annotations

import argparse
import html
import json
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from adapters.gb import GBAdapter
from adapters.gb_regional import GBRegionalAdapter
from adapters.weather import PRESETS, WeatherAdapter
from core import models as model_catalog
from core.grid import PERIOD_HOURS
from core.renewables import solar_capacity_factor, wind_capacity_factor
from hardware import catalog
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS

OUT = Path(__file__).resolve().parent / "build" / "simulator.html"

COUNTS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


def device_specs() -> dict:
    return {k: {
        "name": d.name, "vendor": d.vendor, "kind": d.kind,
        "tflops": d.peak_tflops_bf16, "mfu": d.mfu,
        "mem": d.memory_gb, "bw": d.memory_bandwidth_gbs,
        "tdp": d.tdp_watts, "idle": d.idle_watts,
        "link": d.interconnect.value, "prov": d.provenance.value,
        "source": d.source,
    } for k, d in catalog.CATALOG.items()}


def model_specs() -> dict:
    out = {}
    for k, m in model_catalog.CATALOG.items():
        arch = model_catalog.architecture_for(k, m.params_b)
        out[k] = {
            "name": m.name, "family": m.family, "params": m.params_b,
            "active": m.compute_params_b, "moe": m.is_moe, "notes": m.notes,
            "confidence": m.confidence,
            "arch": {"layers": arch.layers, "hidden": arch.hidden,
                     "heads": arch.heads, "kvheads": arch.kv_heads,
                     "estimated": arch.estimated},
        }
    return out


def build_sites(days: int = 2) -> dict:
    """Per-location renewable factors, bounded and fetched concurrently.

    Site weather is supplementary to the placement calculation. One slow
    public endpoint must not serially hold the Simulator page for minutes.
    Each location gets one short attempt and failures are omitted truthfully.
    """
    def build_one(loc):
        try:
            weather = WeatherAdapter(timeout_seconds=2.5, max_attempts=1)
            wx = weather.forecast(loc, days=days)
            try:
                regional = GBRegionalAdapter(timeout_seconds=2.5, max_attempts=1)
                region = regional.for_postcode(loc.postcode) if loc.postcode else None
            except Exception:
                region = None
            return loc.name, {
                "name": loc.name, "region": region.name if region else "—",
                "carbon": region.carbon_forecast if region else None,
                "solar": [round(solar_capacity_factor(w.solar_radiation_wm2,
                                                      w.temperature_c), 4) for w in wx],
                "wind": [round(wind_capacity_factor(w.wind_speed_100m_ms), 4) for w in wx],
            }
        except Exception:
            return loc.name, None

    sites: dict = {}
    with ThreadPoolExecutor(max_workers=len(PRESETS),
                            thread_name_prefix="site-signals") as pool:
        futures = [pool.submit(build_one, loc) for loc in PRESETS]
        for future in as_completed(futures):
            name, result = future.result()
            if result:
                sites[name] = result
    return sites


def grid_context(days: int = 2) -> dict:
    try:
        end = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        series = GBAdapter().get_data(end - timedelta(days=days), end)
        prices = [p.price for p in series if p.price is not None]
        carbon = [p.carbon_intensity for p in series if p.carbon_intensity is not None]
        if not prices or not carbon:
            raise ValueError("no grid data")
        w = max(1, int(4 / PERIOD_HOURS))
        return {"ok": True, "price_now": prices[-1],
                "price_cheap": min(sum(prices[i:i + w]) / w for i in range(len(prices) - w + 1)),
                "carbon_now": carbon[-1],
                "carbon_clean": min(sum(carbon[i:i + w]) / w for i in range(len(carbon) - w + 1)),
                "from": series[0].timestamp.strftime("%d %b"),
                "to": series[-1].timestamp.strftime("%d %b %Y")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def render(devices: dict, models: dict, grid: dict, sites: dict,
           detected: dict | None = None) -> str:
    devices = {key: dict(value) for key, value in devices.items()}
    local_rows = (detected or {}).get("devices", [])
    for evidence in local_rows:
        key = evidence.get("catalog_key")
        if key in devices and evidence.get("memory_gb") is not None:
            devices[key]["mem"] = evidence["memory_gb"]
            devices[key]["memprov"] = evidence.get("memory_provenance", "MEASURED")
    local_default = next(
        (row.get("catalog_key") for row in local_rows
         if row.get("catalog_key") in devices),
        "h100-sxm",
    )
    local_default_count = next(
        (int(row.get("count", 1)) for row in local_rows
         if row.get("catalog_key") == local_default),
        8,
    )
    model_opts = "".join(
        '<optgroup label="{}">{}</optgroup>'.format(
            html.escape(f),
            "".join(f'<option value="{k}">{html.escape(m["name"])}</option>'
                    for k, m in models.items() if m["family"] == f))
        for f in model_catalog.families())
    model_opts += ('<optgroup label="Custom">'
                   '<option value="__custom__">Custom model…</option></optgroup>')
    device_opts = "".join(
        '<optgroup label="{}">{}</optgroup>'.format(
            html.escape(vendor),
            "".join(
                f'<option value="{k}">{html.escape(d["name"])}</option>'
                for k, d in devices.items() if d["vendor"] == vendor
            ),
        )
        for vendor in dict.fromkeys(d["vendor"] for d in devices.values())
    )
    count_opts = "".join(f'<option value="{n}">{n:,}</option>' for n in COUNTS)
    prec_opts = "".join(f'<option value="{p}">{p}</option>' for p in model_catalog.PRECISIONS)
    site_opts = "".join(f'<option value="{html.escape(k)}">{html.escape(k)}</option>' for k in sites)
    if not site_opts:
        site_opts = '<option value="">Loading site forecasts…</option>'
    cap_opts = "".join(f'<option value="{c}">{c:,} kW</option>'
                       for c in (0, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 25000))

    market_key = grid.get("market_key", "GB")
    location_key = grid.get("location_key", "national")
    grid_locations = grid.get("locations", [])
    grid_location_opts = "".join(
        f'<option value="{html.escape(choice["key"])}"'
        f'{" selected" if choice["key"] == location_key else ""}>'
        f'{html.escape(choice["name"])} · {html.escape(choice["detail"])}</option>'
        for choice in grid_locations
    )
    if grid_locations and location_key not in {choice["key"] for choice in grid_locations}:
        grid_location_opts = (
            f'<option value="{html.escape(location_key)}" selected>'
            f'Custom PNode · {html.escape(grid.get("location_name", location_key))}</option>'
            + grid_location_opts
        )
    query = urlencode({"market": market_key, "location": location_key})
    grid_href, planner_href = f"/grid?{query}", f"/planner?{query}"
    operations_href = f"/?{query}"
    grid_controls = "" if not grid_locations else f"""
    <div class="ctl"><label for="marketSelect">Power market</label><select id="marketSelect">
      <option value="GB"{" selected" if market_key == "GB" else ""}>Great Britain</option>
      <optgroup label="United States">
        <option value="CAISO"{" selected" if market_key == "CAISO" else ""}>California ISO</option>
        <option value="NYISO"{" selected" if market_key == "NYISO" else ""}>New York ISO</option><option value="MISO"{" selected" if market_key == "MISO" else ""}>Midcontinent ISO</option>
      </optgroup>
    </select></div>
    <div class="ctl"><label for="gridLocation">Grid location</label>
      <select id="gridLocation">{grid_location_opts}</select></div>"""
    custom_node = "" if not grid.get("allows_custom_node") else """
    <div class="ctl"><label for="customNode">Custom CAISO PNode</label>
      <div class="joined"><input id="customNode" placeholder="Exact node ID">
      <button id="loadNode" type="button">Load</button></div></div>"""
    detected_items = []
    for evidence in local_rows:
        memory = evidence.get("memory_gb")
        detail = f"{memory:g} GB memory measured" if memory is not None else "memory unavailable"
        detected_items.append(
            f'<b>{html.escape(str(evidence.get("name", "Accelerator")))}</b> · '
            f'{html.escape(detail)} · performance '
            f'{html.escape(str(evidence.get("performance_provenance", "UNAVAILABLE")))}'
        )
    detected_banner = "" if not detected_items else (
        '<div class="detected"><span>Detected locally</span>'
        + "<br>".join(detected_items)
        + '<small>Identity and memory come from the operating system. '
          'Performance and power keep their separate provenance.</small></div>'
    )

    note = (
        f"Priced against {html.escape(grid.get('market_name', 'GB'))}, "
        f"{html.escape(grid.get('location_name', 'national'))}, "
        f"{html.escape(grid['from'])} to {html.escape(grid['to'])}. "
        f"{html.escape(grid.get('signal_mode', ''))}."
        if grid.get("ok") else "Grid data unavailable; energy is shown without cost or carbon."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model Simulator — Grid-Aware Scheduler</title>
{THEME_BOOTSTRAP}
<style>
:root {{
  color-scheme: light dark;
  --bg:#F2F2F7; --card:#FFF; --text:#000;
  --text-2:rgba(60,60,67,.60); --text-3:rgba(60,60,67,.30); --sep:rgba(60,60,67,.18);
  --blue:#007AFF; --green:#248A3D; --orange:#B35300; --red:#C7261B;
  --shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.06);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#000; --card:#1C1C1E; --text:#FFF;
    --text-2:rgba(235,235,245,.60); --text-3:rgba(235,235,245,.30); --sep:rgba(84,84,88,.65);
    --blue:#0A84FF; --green:#2A9D48; --orange:#E08A2E; --red:#E2554A; --shadow:none;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#000; --card:#1C1C1E; --text:#FFF;
  --text-2:rgba(235,235,245,.60); --text-3:rgba(235,235,245,.30); --sep:rgba(84,84,88,.65);
  --blue:#0A84FF; --green:#2A9D48; --orange:#E08A2E; --red:#E2554A; --shadow:none;
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:0 24px 72px;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1120px;margin:0 auto}}
header{{padding:56px 0 28px}}
h1{{margin:0 0 6px;font-size:40px;line-height:1.08;font-weight:700;letter-spacing:-.022em}}
.sub{{color:var(--text-2);font-size:17px;margin:0}}
nav{{margin-top:16px;display:inline-flex;gap:2px;padding:3px;border-radius:11px;
background:color-mix(in srgb,var(--text) 5%,transparent)}}
nav a{{font-size:13px;font-weight:550;text-decoration:none;padding:6px 14px;border-radius:8px;
color:var(--text-2);transition:background .15s ease,color .15s ease}}
nav a:hover{{color:var(--text)}}
nav a.on{{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.10)}}
.card{{background:var(--card);border-radius:18px;padding:22px 24px;box-shadow:var(--shadow);margin-bottom:18px}}
.card>h2{{margin:0 0 4px;font-size:20px;font-weight:640;letter-spacing:-.015em}}
.card>.note{{margin:0 0 18px;color:var(--text-2);font-size:14px}}
.controls{{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:14px}}
.ctl{{display:flex;flex-direction:column;gap:6px}}
.ctl label{{font-size:12px;color:var(--text-2);font-weight:510}}
.ctl select,.ctl input{{font:inherit;font-size:14px;padding:9px 12px;border-radius:10px;
border:1px solid var(--sep);background-color:var(--card);color:var(--text);width:100%}}
.ctl select{{padding-right:34px;cursor:pointer;-webkit-appearance:none;appearance:none;
background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5 6 6.5 11 1.5' stroke='%23888' stroke-width='1.8' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:right 12px center}}
.ctl select:hover,.ctl input:hover{{border-color:var(--text-3)}}
.ctl select:focus,.ctl input:focus{{outline:none;border-color:var(--blue);
box-shadow:0 0 0 3px color-mix(in srgb,var(--blue) 22%,transparent)}}
.ctl select option{{background:var(--card);color:var(--text)}}
.joined{{display:flex}} .joined input{{border-radius:10px 0 0 10px}}
.joined button{{border:0;border-radius:0 10px 10px 0;background:var(--blue);color:#fff;
font:600 12px inherit;padding:0 12px;cursor:pointer}}
.seg{{display:inline-flex;gap:3px;background:color-mix(in srgb,var(--text) 5%,transparent);
padding:3px;border-radius:10px}}
.seg button{{font:inherit;font-size:13px;font-weight:550;padding:7px 16px;border:0;border-radius:8px;
background:transparent;color:var(--text-2);cursor:pointer}}
.seg button.on{{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.10)}}
.hide{{display:none}}
.task-only.hide{{display:none}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:1px;
background:var(--sep);border-radius:14px;overflow:hidden;margin-top:18px}}
.tile{{background:var(--card);padding:16px 18px}}
.tile-label{{font-size:12px;color:var(--text-2)}}
.tile-value{{font-size:27px;font-weight:630;letter-spacing:-.02em;margin:4px 0 2px;
font-variant-numeric:tabular-nums}}
.tile-sub{{font-size:12px;color:var(--text-2)}}
.prov{{display:inline-block;font-size:10px;font-weight:640;letter-spacing:.05em;padding:2px 7px;
border-radius:5px;margin-left:6px;vertical-align:2px}}
.prov.SPEC{{background:color-mix(in srgb,var(--blue) 14%,transparent);color:var(--blue)}}
.prov.ESTIMATED{{background:color-mix(in srgb,var(--orange) 16%,transparent);color:var(--orange)}}
.prov.MEASURED{{background:color-mix(in srgb,var(--green) 14%,transparent);color:var(--green)}}
.detected{{margin-bottom:18px;padding:12px 15px;border-radius:12px;
background:color-mix(in srgb,var(--green) 10%,transparent);color:var(--text);font-size:13px}}
.detected>span{{display:block;color:var(--green);font-size:11.5px;font-weight:650;
letter-spacing:-0.005em;margin-bottom:3px}}
.detected small{{display:block;color:var(--text-2);margin-top:4px}}
.warn{{margin-top:16px;padding:13px 16px;border-radius:12px;font-size:14px;
background:color-mix(in srgb,var(--red) 10%,transparent);color:var(--red)}}
.warn.soft{{background:color-mix(in srgb,var(--orange) 12%,transparent);color:var(--orange)}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{text-align:right;padding:9px 10px;border-bottom:1px solid var(--sep)}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--text-2);font-weight:510;font-size:12px}}
tr.best td{{background:color-mix(in srgb,var(--green) 8%,transparent)}}
tr.nofit td{{opacity:.42}}
.assum{{margin:14px 0 0;padding-left:18px;color:var(--text-2);font-size:13px}}
.assum li{{margin:3px 0}}
.foot{{color:var(--text-2);font-size:13px;margin-top:26px}}
.fleet{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}}
.fleet-chip{{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:9px;
border:1px solid var(--sep);background:color-mix(in srgb,var(--text) 3%,transparent);
font-size:13px;font-variant-numeric:tabular-nums}}
.fleet-chip b{{font-weight:620}}
.fleet-chip i{{font-style:normal;color:var(--text-2);font-size:11px}}
.fleet-chip button{{border:0;background:transparent;color:var(--text-3);cursor:pointer;
font:inherit;font-size:15px;line-height:1;padding:0 2px}}
.fleet-chip button:hover{{color:var(--red)}}
.fleet-add{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}}
.fleet-add select{{font:inherit;font-size:13px;padding:6px 10px;border-radius:9px;
border:1px solid var(--sep);background:var(--card);color:var(--text)}}
.fleet-sum{{margin-left:auto;font-size:12px;color:var(--text-2);font-variant-numeric:tabular-nums}}
#pathmap{{width:100%;height:auto;display:block}}
.pm-col{{fill:var(--text-3);font-size:9px;font-weight:700;letter-spacing:.12em}}
.pm-rib{{opacity:.26}}
.pm-node{{stroke:none}}
.pm-job{{fill:var(--text);opacity:.8}}
.pm-lab{{fill:var(--text);font-size:11.5px;font-weight:600;letter-spacing:-.01em}}
.pm-sub{{fill:var(--text-2);font-size:10px;font-variant-numeric:tabular-nums}}
.pm-joblab{{fill:var(--bg);font-size:11.5px;font-weight:700}}
.pm-jobsub{{fill:var(--bg);font-size:10px;opacity:.75;font-variant-numeric:tabular-nums}}
.pm-warn{{fill:var(--red);font-size:12px;font-weight:800}}
@media(max-width:720px){{h1{{font-size:32px}}}}
{PANEL_CSS}
{THEME_CSS}
</style>
</head>
<body>
{THEME_CONTROL}
<div class="wrap">

<header>
  <h1>Model Simulator</h1>
  <p class="sub">How a model runs on hardware you don't have — and what the grid charges for it.</p>
  <nav><a href="{html.escape(operations_href)}">Operations</a><a href="/simulator?{html.escape(query)}" class="on">Fleet Lab</a><a href="{html.escape(planner_href)}">Placement Lab</a><a href="{html.escape(grid_href)}">Sites &amp; Grid</a><a href="/site">Site</a><a href="/decisions">Decisions</a></nav>
</header>

<section class="card">
  <h2>Configuration</h2>
  <p class="note">{note}</p>
  {detected_banner}
  <div class="controls">
    {grid_controls}
    {custom_node}
    <div class="ctl"><label for="model">Model</label><select id="model">{model_opts}</select></div>
    <div class="ctl custom-only hide"><label for="cparams">Parameters (B)</label>
      <input id="cparams" type="number" min="0.01" step="0.1" value="7"></div>
    <div class="ctl custom-only hide"><label for="cactive">Active (B) — 0 if dense</label>
      <input id="cactive" type="number" min="0" step="0.1" value="0"></div>
    <div class="ctl"><label for="prec">Weight precision</label><select id="prec">{prec_opts}</select></div>
    <div class="ctl"><label for="device">Hardware</label><select id="device">{device_opts}</select></div>
    <div class="ctl"><label for="count">Accelerators</label><select id="count">{count_opts}</select></div>
    <div class="ctl"><label for="tokens">Token budget</label><select id="tokens">
      <option value="10000000">10M</option><option value="100000000">100M</option>
      <option value="1000000000">1B</option><option value="10000000000">10B</option>
      <option value="100000000000">100B</option><option value="15000000000000">15T (full pretrain)</option>
    </select></div>
    <div class="ctl"><label>Task</label><span class="seg" id="task">
      <button type="button" data-t="training" class="on">Training</button>
      <button type="button" data-t="inference">Inference</button></span></div>
    <div class="ctl training-only task-only"><label for="shard">Training memory</label><select id="shard">
      <option value="zero3">FSDP / ZeRO-3 sharded</option>
      <option value="replicated">Replicated data parallel</option>
    </select></div>
    <div class="ctl training-only task-only"><label for="statebytes">Training state bytes / parameter</label><select id="statebytes">
      <option value="16">16 · mixed-precision Adam</option>
      <option value="8">8 · reduced-state optimiser</option>
    </select></div>
    <div class="ctl training-only task-only"><label for="headroom">Activation and buffer reserve</label><select id="headroom">
      <option value="10">10%</option><option value="20">20%</option>
      <option value="30">30%</option><option value="50">50%</option>
    </select></div>
    <div class="ctl inference-only task-only hide"><label for="context">Context length</label><select id="context">
      <option value="2048">2k</option><option value="8192">8k</option>
      <option value="32768">32k</option><option value="131072">128k</option>
    </select></div>
    <div class="ctl inference-only task-only hide"><label for="batch">Concurrent sequences</label><select id="batch">
      <option value="1">1</option><option value="8">8</option><option value="32">32</option>
      <option value="128">128</option><option value="512">512</option>
    </select></div>
    <div class="ctl inference-only task-only hide"><label for="kvprec">KV cache precision</label><select id="kvprec">
      <option value="bf16">bf16</option><option value="fp16">fp16</option>
      <option value="fp8">fp8</option><option value="int8">int8</option>
    </select></div>
    <div class="ctl"><label for="pue">Facility PUE</label><select id="pue">
      <option value="1">1.00 · IT only</option><option value="1.1">1.10</option>
      <option value="1.2">1.20</option><option value="1.3">1.30</option>
      <option value="1.4">1.40</option><option value="1.5">1.50</option>
    </select></div>
    <div class="ctl"><label for="system">Measured system efficiency</label><select id="system">
      <option value="100">100% · idealised</option><option value="90">90%</option>
      <option value="85">85%</option><option value="80">80%</option><option value="70">70%</option>
    </select></div>
  </div>
  <div class="tiles" id="tiles"></div>
  <div id="warn"></div>
  <ul class="assum" id="assum"></ul>
</section>

<section class="card pnl">
  <h2>Allocation path</h2>
  <p class="note">Work splits across groups in proportion to what each can actually deliver, so every group finishes together. Split evenly instead and the slowest sets the finish time while the fastest idle at part load.</p>
  <div class="fleet" id="fleet"></div>
  <div class="fleet-add">
    <select id="addDev"></select>
    <select id="addN">
      <option value="1">1</option><option value="2">2</option><option value="4" selected>4</option>
      <option value="8">8</option><option value="16">16</option><option value="32">32</option>
      <option value="64">64</option><option value="128">128</option>
    </select>
    <button type="button" class="ch-btn" id="addBtn">Add group</button>
    <span class="fleet-sum" id="fleetSum"></span>
  </div>
  <svg id="pathmap" viewBox="0 0 1000 300" role="img" data-inspector='{{"kind":"diagram"}}'
       aria-label="Allocation of work across device groups and time windows"></svg>
</section>

<section class="card pnl">
  <h2>On-site renewables</h2>
  <p class="note"><span class="prov ESTIMATED">ESTIMATED</span></p>
  <div class="controls">
    <div class="ctl"><label for="site">Location</label><select id="site">{site_opts}</select></div>
    <div class="ctl"><label for="solar">Solar installed</label><select id="solar">{cap_opts}</select></div>
    <div class="ctl"><label for="wind">Wind installed</label><select id="wind">{cap_opts}</select></div>
  </div>
  <div class="tiles" id="rtiles"></div>
  <div id="rgap"></div>
  <svg id="rchart" viewBox="0 0 1000 190" preserveAspectRatio="none"
       style="width:100%;height:190px;margin-top:18px;overflow:visible"></svg>
</section>

<section class="card">
  <h2>Every device, ranked by energy</h2>
  <p class="note">Greyed rows don't fit in memory.</p>
  <div style="overflow-x:auto"><table id="cmp"><thead><tr>
    <th>Device</th><th>Runtime</th><th>Power</th><th>Energy</th><th>Memory</th><th>Cost</th><th>CO₂</th>
  </tr></thead><tbody></tbody></table></div>
</section>

<section class="card">
  <h2>Does adding hardware help?</h2>
  
  <div style="overflow-x:auto"><table id="scale"><thead><tr>
    <th>Fleet size</th><th>Runtime</th><th>Energy</th><th>Scaling</th><th>Memory</th>
  </tr></thead><tbody></tbody></table></div>
</section>

<p class="foot" id="foot"></p>
</div>

<script>
var D = {json.dumps(devices)};
var MODELS = {json.dumps(models)};
var GRID = {json.dumps(grid)};
var SITES = {json.dumps(sites)};
var COUNTS = {json.dumps(COUNTS)};
var BYTES = {json.dumps(model_catalog.BYTES_PER_PARAM)};
var LINK = {{"NVLink":450,"PCIe":50,"Ethernet":25,"Unified memory":0,"None":0}};
var SYMBOL = GRID.symbol || "£";

var S = {{ model:"llama31-8b", prec:"bf16", device:{json.dumps(local_default)}, count:{local_default_count},
          task:"training", tokens:1e9, cparams:7, cactive:0,
          site:Object.keys(SITES)[0], solar:10, wind:5, shard:"zero3",
          statebytes:16, headroom:20, context:8192, batch:8, kvprec:"bf16",
          pue:1.2, system:85 }};

// Configuration is URL state. A scenario can be bookmarked, shared with an
// operator, or returned to after a review instead of disappearing on reload.
(function loadURLState(){{
  var q=new URLSearchParams(location.search);
  Object.keys(S).forEach(function(k){{
    if(!q.has(k)) return;
    var raw=q.get(k), numeric=["count","tokens","cparams","cactive","solar","wind",
      "statebytes","headroom","context","batch","pue","system"].indexOf(k)>=0;
    S[k]=numeric?(+raw||S[k]):raw;
  }});
  if(S.model!=="__custom__"&&!MODELS[S.model]) S.model="llama31-8b";
  if(!D[S.device]) S.device={json.dumps(local_default)};
  if(!BYTES[S.prec]) S.prec="bf16";
  if(["training","inference"].indexOf(S.task)<0) S.task="training";
  if(["zero3","replicated"].indexOf(S.shard)<0) S.shard="zero3";
  if(!BYTES[S.kvprec]) S.kvprec="bf16";
  if(COUNTS.indexOf(S.count)<0) S.count={local_default_count};
  if(!(S.tokens>0)) S.tokens=1e9;
  S.pue=Math.min(3,Math.max(1,S.pue));
  S.system=Math.min(100,Math.max(1,S.system));
  S.context=Math.max(1,S.context); S.batch=Math.max(1,S.batch);
  if(S.site&&!SITES[S.site]) S.site=Object.keys(SITES)[0];
}})();

function nf(v,d){{ return v.toLocaleString("en-GB",{{minimumFractionDigits:d,maximumFractionDigits:d}}); }}
function dur(h){{
  if(!isFinite(h)||h<=0) return "—";
  if(h<1) return nf(h*60,0)+" min";
  if(h<48) return nf(h,1)+" h";
  if(h<8760) return nf(h/24,1)+" days";
  return nf(h/8760,1)+" years";
}}
function tile(l,v,s){{ return '<div class="tile"><div class="tile-label">'+l+
  '</div><div class="tile-value">'+v+'</div><div class="tile-sub">'+(s||"")+"</div></div>"; }}

// --- estimator, ported from core/workload.py -----------------------------
// Training FLOPs = 6 x parameters x tokens. Inference decode is bounded by
// memory bandwidth, not arithmetic: each generated token reads all weights.
function modelOf(){{
  if(S.model === "__custom__"){{
    var a = (S.cactive>0 && S.cactive<S.cparams) ? S.cactive : null;
    return {{ name:"Custom "+nf(S.cparams,1)+"B", params:S.cparams,
             active:a||S.cparams, moe:!!a }};
  }}
  return MODELS[S.model];
}}
function wbytes(m){{ return m.params*1e9*BYTES[S.prec]; }}
function archOf(m){{
  if(m.arch) return m.arch;
  var rows=[[1.5,16,2048,16],[4,28,3072,24],[9,32,4096,32],
    [16,40,5120,40],[35,48,6144,48],[80,80,8192,64],[200,96,12288,96],
    [1e12,126,16384,128]];
  for(var i=0;i<rows.length;i++) if(m.params<=rows[i][0]) return {{
    layers:rows[i][1],hidden:rows[i][2],heads:rows[i][3],
    kvheads:Math.min(8,rows[i][3]),estimated:true}};
}}
function kvGB(m){{
  if(S.task!=="inference") return 0;
  var a=archOf(m), elem=BYTES[S.kvprec]||2, hd=a.hidden/a.heads;
  return 2*a.layers*a.kvheads*hd*S.context*S.batch*elem/1e9;
}}
function memNeed(m,n){{
  var weights=wbytes(m)/1e9;
  if(S.task==="inference") return weights+kvGB(m);
  var state=m.params*S.statebytes*(1+S.headroom/100);
  return S.shard==="zero3" ? state : state*n;
}}
function fitsMemory(m,dev,n){{
  var state=m.params*S.statebytes*(1+S.headroom/100);
  if(S.task==="training" && S.shard==="replicated") return dev.mem>=state;
  return dev.mem*n>=memNeed(m,n);
}}
function scaling(m,dev,n){{
  var system=Math.max(.01,S.system/100);
  if(n<=1 || dev.link==="Unified memory") return system;
  var l = LINK[dev.link]||0; if(!l) return system;
  var compute = (6*m.active*1e9*2e6)/(dev.tflops*dev.mfu*n*1e12);
  var comm = (2*(n-1)/n*wbytes(m))/(l*1e9);
  return compute/(compute+comm)*system;
}}
function est(dk,n){{
  var dev=D[dk], m=modelOf(), eff=scaling(m,dev,n), mem=dev.mem*n, hours, itkw;
  if(S.task==="training"){{
    hours = (6*m.active*1e9*S.tokens)/(dev.tflops*dev.mfu*n*eff*1e12)/3600;
    itkw = dev.tdp*n/1000;
  }} else {{
    hours = (S.tokens/((dev.bw*1e9*n)/wbytes(m)))/3600;
    hours = hours/Math.max(.01,S.system/100);
    itkw = (dev.idle+(dev.tdp-dev.idle)*0.6)*n/1000;
  }}
  var kw=itkw*S.pue;
  return {{hours:hours, kwh:kw*hours, kw:kw, itkw:itkw, mem:mem, scaling:eff,
           fits:fitsMemory(m,dev,n)}};
}}
function money(k){{ return GRID.ok ? k*GRID.price_cheap/1000 : null; }}
function co2(k){{ return GRID.ok ? k*GRID.carbon_clean/1000 : null; }}

function render(){{
  var m=modelOf(), dev=D[S.device], r=est(S.device,S.count);
  var c=money(r.kwh), g=co2(r.kwh);
  document.getElementById("tiles").innerHTML =
    tile("Runtime",dur(r.hours),nf(r.kw,1)+" kW facility · "+nf(r.itkw,1)+" kW IT") +
    tile("Facility energy",nf(r.kwh,0)+" kWh","PUE "+nf(S.pue,2)+" · whole run") +
    tile("Cost",c===null?"—":SYMBOL+nf(c,2),"cheapest window") +
    tile("Carbon",g===null?"—":nf(g,1)+" kg","cleanest window") +
    tile("Memory",nf(r.mem,0)+" GB","need ~"+nf(memNeed(m,S.count),0)+" GB"+
      (dev.memprov?" · capacity "+dev.memprov:""));

  var w=[];
  if(!r.fits) w.push('<div class="warn"><b>Does not fit.</b> '+m.name+' at '+S.prec+
    ' needs about '+nf(memNeed(m,S.count),0)+' GB across the fleet for '+S.task+'; '+nf(S.count,0)+'\\u00d7 '+dev.name+
    ' gives '+nf(r.mem,0)+' GB. Runtime below is compute cost only.</div>');
  if(m.moe) w.push('<div class="warn soft"><b>Mixture of experts.</b> '+nf(m.params,0)+
    'B total, ~'+nf(m.active,0)+'B active per token. Compute uses the active count; memory '+
    'uses the total, because every expert must stay resident.</div>');
  document.getElementById("warn").innerHTML=w.join("");

  document.getElementById("assum").innerHTML=[
    S.task==="training"
      ? "Training FLOPs \\u2248 6 \\u00d7 "+nf(m.active,1)+"B parameters \\u00d7 "+
        (S.tokens>=1e12?nf(S.tokens/1e12,1)+"T":nf(S.tokens/1e9,2)+"B")+" tokens."
      : "Decode bounded by memory bandwidth: tokens/sec \\u2248 bandwidth \\u00f7 model bytes.",
    dev.name+" at "+nf(dev.mfu*100,0)+"% of its "+nf(dev.tflops,0)+" TFLOPS peak, not peak.",
    S.count>1 ? "Scaling "+nf(r.scaling*100,0)+"% of linear over "+dev.link+
      " including the selected "+nf(S.system,0)+"% measured system efficiency."
      : "Single device at "+nf(S.system,0)+"% measured system efficiency.",
    "Facility energy = IT energy \\u00d7 PUE "+nf(S.pue,2)+".",
    S.task==="inference" ? "KV cache: "+nf(kvGB(m),1)+" GB for "+nf(S.batch,0)+
      " sequences at "+nf(S.context,0)+" tokens and "+S.kvprec+
      (archOf(m).estimated?" (architecture estimated).":".")
      : (S.shard==="zero3"?"Training state sharded across the fleet.":
        "Training state replicated on every accelerator.")+" "+nf(S.statebytes,0)+
        " bytes/parameter with "+nf(S.headroom,0)+"% activation and buffer reserve.",
    dev.memprov ? "Memory capacity: "+dev.memprov+" from local hardware detection."
      : "Memory capacity: "+dev.prov+" catalogue value.",
    "Performance and power source: "+(dev.source||"\\u2014")
  ].map(function(x){{return "<li>"+x+"</li>";}}).join("");

  var rows=Object.keys(D).map(function(k){{return {{k:k,d:D[k],r:est(k,S.count)}};}})
    .sort(function(a,b){{ if(a.r.fits!==b.r.fits) return a.r.fits?-1:1; return a.r.kwh-b.r.kwh; }});
  var best=rows.find(function(x){{return x.r.fits;}});
  document.querySelector("#cmp tbody").innerHTML=rows.map(function(x){{
    return '<tr class="'+(!x.r.fits?"nofit":(best&&x.k===best.k?"best":""))+'"><td>'+
      x.d.vendor+" "+x.d.name+'<span class="prov '+x.d.prov+'">'+x.d.prov+
      "</span>"+(x.d.memprov?'<span class="prov '+x.d.memprov+'">MEMORY '+x.d.memprov+'</span>':"")+
      "</td><td>"+dur(x.r.hours)+"</td><td>"+nf(x.r.kw,1)+" kW</td><td><b>"+
      nf(x.r.kwh,0)+" kWh</b></td><td>"+nf(x.r.mem,0)+" GB</td><td>"+
      (money(x.r.kwh)===null?"—":SYMBOL+nf(money(x.r.kwh),2))+"</td><td>"+
      (co2(x.r.kwh)===null?"—":nf(co2(x.r.kwh),1)+" kg")+"</td></tr>";
  }}).join("");

  document.querySelector("#scale tbody").innerHTML=COUNTS.map(function(n){{
    var e=est(S.device,n);
    return "<tr"+(n===S.count?' class="best"':"")+"><td>"+nf(n,0)+"\\u00d7 "+dev.name+
      "</td><td>"+dur(e.hours)+"</td><td>"+nf(e.kwh,0)+" kWh</td><td>"+
      nf(e.scaling*100,0)+"%</td><td>"+nf(e.mem,0)+" GB</td></tr>";
  }}).join("");

  renewables(r.kw);
  if (typeof drawFleet === 'function') drawFleet();
  document.getElementById("foot").textContent =
    m.name+" \\u00b7 "+nf(m.params,1)+"B parameters at "+S.prec+" \\u00b7 needs ~"+
    nf(memNeed(m,S.count),0)+" GB.";
  syncURL();
}}

function syncURL(){{
  var q=new URLSearchParams(location.search);
  Object.keys(S).forEach(function(k){{ if(k!=="site"||S[k]) q.set(k,String(S[k])); }});
  history.replaceState(null,"",location.pathname+"?"+q.toString());
}}

function goMarket(market,locationKey){{
  syncURL(); var q=new URLSearchParams(location.search);
  q.set("market",market); q.set("location",locationKey); q.delete("custom_node");
  location.href=location.pathname+"?"+q.toString();
}}

function renewables(load){{
  var svg=document.getElementById("rchart"),s=SITES[S.site];
  if(!s){{svg.dataset.inspector=JSON.stringify({{kind:"line",xLabel:"Forecast hour",xSuffix:" h",
    yLabel:"Power",ySuffix:" kW",precision:2,series:[]}});
    document.getElementById("rtiles").innerHTML=tile("Site forecast","Loading…","The simulator remains usable while optional weather signals refresh.");
    document.getElementById("rgap").innerHTML="";return;}}
  var n=s.solar.length,matched=0,imp=0,cur=0,gen=0,full=0,avail=[];
  for(var i=0;i<n;i++){{
    var a=s.solar[i]*S.solar+s.wind[i]*S.wind;
    avail.push(a); gen+=a; matched+=Math.min(a,load);
    imp+=Math.max(0,load-a); cur+=Math.max(0,a-load); if(a>=load) full++;
  }}
  var dem=load*n, hourly=dem?matched/dem*100:0, annual=dem?gen/dem*100:0;
  document.getElementById("rtiles").innerHTML=
    tile("Hourly matched",nf(hourly,1)+"%","period by period")+
    tile("Imported",nf(imp,0)+" kWh","off the grid")+
    tile("Curtailed",nf(cur,0)+" kWh","nowhere to go")+
    tile("Fully covered",full+" / "+n+" h","no grid needed")+
    tile("Grid region",s.region,s.carbon===null?"":s.carbon+" gCO\\u2082/kWh now");
  document.getElementById("rgap").innerHTML=(annual>100&&hourly<99)
    ? '<div class="warn soft"><b>Generates '+nf(annual,0)+'% of what it needs, covers '+
      nf(hourly,1)+'% of it.</b> Annual netting would call this fully renewable; '+nf(imp,0)+
      ' kWh is still bought, because the generation arrived when the load did not.</div>':"";
  var W=1000,H=190,P=22;
  var peak=Math.max(load,Math.max.apply(null,avail))||1;
  function X(i){{return n<2?W/2:(i/(n-1))*W;}} function Y(v){{return P+(H-P*2)*(1-v/peak);}}
  var line=""; for(var j=0;j<n;j++) line+=(j?"L":"M")+X(j).toFixed(1)+","+Y(avail[j]).toFixed(1);
  svg.innerHTML='<path d="M0,'+Y(0).toFixed(1)+line.slice(1)+"L"+W+","+Y(0).toFixed(1)+
    '" fill="var(--green)" opacity=".16"/><path d="'+line+
    '" fill="none" stroke="var(--green)" stroke-width="2" vector-effect="non-scaling-stroke"/>'+
    '<line x1="0" y1="'+Y(load).toFixed(1)+'" x2="'+W+'" y2="'+Y(load).toFixed(1)+
    '" stroke="var(--blue)" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>'+
    '<text x="4" y="'+(Y(load)-6).toFixed(1)+'" fill="var(--blue)" font-size="11">load '+
    nf(load,1)+' kW</text>';
  svg.dataset.inspector=JSON.stringify({{kind:"line",xLabel:"Forecast hour",xSuffix:" h",
    yLabel:"Power",ySuffix:" kW",precision:2,series:[
      {{name:"Renewable generation",color:"--green",area:true,points:avail.map(function(v,i){{return [i,v]}})}},
      {{name:"Facility load",color:"--blue",dash:true,points:avail.map(function(v,i){{return [i,load]}})}}]}});
}}

function bind(id,key,num){{
  var el=document.getElementById(id); if(!el) return;
  el.value=String(S[key]);
  function upd(e){{
    S[key]= num ? (+e.target.value||0) : e.target.value;
    if(id==="model") document.querySelectorAll(".custom-only").forEach(function(x){{
      x.classList.toggle("hide", S.model!=="__custom__"); }});
    render();
  }}
  el.addEventListener("change",upd);
  if(el.tagName==="INPUT") el.addEventListener("input",upd);
}}
["model","prec","device","site","shard","kvprec"].forEach(function(i){{bind(i,i,false);}});
["count","tokens","cparams","cactive","solar","wind","statebytes","headroom","context","batch","pue","system"].forEach(function(i){{bind(i,i,true);}});
document.querySelectorAll(".custom-only").forEach(function(x){{
  x.classList.toggle("hide",S.model!=="__custom__");
}});
document.querySelectorAll("#task button").forEach(function(b){{
  b.classList.toggle("on",b.dataset.t===S.task);
  b.addEventListener("click",function(){{
    document.querySelectorAll("#task button").forEach(function(o){{o.classList.remove("on");}});
    b.classList.add("on"); S.task=b.dataset.t;
    document.querySelectorAll(".training-only").forEach(function(x){{x.classList.toggle("hide",S.task!=="training");}});
    document.querySelectorAll(".inference-only").forEach(function(x){{x.classList.toggle("hide",S.task!=="inference");}});
    render(); }});
}});
document.querySelectorAll(".training-only").forEach(function(x){{x.classList.toggle("hide",S.task!=="training");}});
document.querySelectorAll(".inference-only").forEach(function(x){{x.classList.toggle("hide",S.task!=="inference");}});
var marketSelect=document.getElementById("marketSelect"), gridLocation=document.getElementById("gridLocation");
if(marketSelect) marketSelect.addEventListener("change",function(){{
  var defaults={{GB:"national",CAISO:"sp15",NYISO:"nyc"}};
  goMarket(this.value,defaults[this.value]||"national");
}});
if(gridLocation) gridLocation.addEventListener("change",function(){{
  goMarket({json.dumps(market_key)},this.value);
}});
var loadNode=document.getElementById("loadNode");
if(loadNode) loadNode.addEventListener("click",function(){{
  var value=document.getElementById("customNode").value.trim();
  if(value) goMarket("CAISO",value);
}});

function installSites(next){{
  var keys=next&&typeof next==="object"?Object.keys(next):[];if(!keys.length)return false;
  SITES=next;var select=document.getElementById("site");select.innerHTML=keys.map(function(k){{
    return '<option value="'+k.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;")+'">'+
      k.replace(/&/g,"&amp;").replace(/</g,"&lt;")+"</option>";}}).join("");
  if(!S.site||!SITES[S.site])S.site=keys[0];select.value=S.site;render();return true;
}}
function pollSites(){{fetch("/api/v1/sites").then(function(response){{return response.json()}}).then(function(payload){{
  if(installSites(payload.sites))return;if(payload.refreshing)setTimeout(pollSites,1000);else
    document.getElementById("rtiles").innerHTML=tile("Site forecast","Unavailable","Optional public weather signals did not respond. Hardware and grid calculations remain active.");
}}).catch(function(){{setTimeout(pollSites,2500)}})}}
if(!Object.keys(SITES).length)setTimeout(pollSites,100);

var FLEET = [];
function fleetCap(dk){{ var d=D[dk], m=modelOf();
  return S.task==="training" ? d.tflops*d.mfu : (d.bw*1e9)/wbytes(m)/1e12; }}
function allocate(){{
  var m=modelOf();
  var gs = FLEET.length ? FLEET : [{{dev:S.device,n:S.count}}];
  var caps = gs.map(function(g){{ return fleetCap(g.dev)*g.n; }});
  var tot = caps.reduce(function(a,b){{return a+b;}},0)||1;
  var mem = gs.reduce(function(a,g){{ return a+D[g.dev].mem*g.n; }},0);
  var hours = S.task==="training"
    ? (6*m.active*1e9*S.tokens)/(tot*Math.max(.01,S.system/100)*1e12)/3600
    : (S.tokens/(tot*Math.max(.01,S.system/100)*1e12))/3600;
  var legs = gs.map(function(g,i){{ var d=D[g.dev];
    var kw=(S.task==="training"? d.tdp : d.idle+(d.tdp-d.idle)*0.6)*g.n/1000*S.pue;
    return {{dev:g.dev,name:d.name,n:g.n,share:caps[i]/tot,kw:kw}}; }});
  var kw = legs.reduce(function(a,l){{return a+l.kw;}},0);
  var count=gs.reduce(function(a,g){{return a+g.n;}},0);
  var need=memNeed(m,count);
  var fits=S.task==="training"&&S.shard==="replicated"
    ? gs.every(function(g){{return D[g.dev].mem>=m.params*S.statebytes*(1+S.headroom/100);}})
    : mem>=need;
  return {{legs:legs,hours:hours,kw:kw,kwh:kw*hours,mem:mem,need:need,fits:fits}};
}}
function drawPath(){{
  var a=allocate(), svg=document.getElementById("pathmap"); if(!svg) return;
  var W=1000,H=300,xJ=6,wJ=120,xD=320,wD=220,xW=690,wW=304,pad=20,gap=7,n=a.legs.length;
  var usable=H-pad*2-gap*(n-1);
  var hs=a.legs.map(function(l){{ return Math.max(26,l.share*usable); }});
  var sum=hs.reduce(function(x,y){{return x+y;}},0);
  if(sum>usable) hs=hs.map(function(h){{return h*usable/sum;}});
  var jobH=hs.reduce(function(x,y){{return x+y;}},0)+gap*(n-1);
  var win = GRID.ok ? "cheapest "+Math.max(1,Math.round(a.hours))+"h window" : "immediate";
  var out='<text class="pm-col" x="'+xJ+'" y="11">JOB</text>'+
          '<text class="pm-col" x="'+xD+'" y="11">DEVICES</text>'+
          '<text class="pm-col" x="'+xW+'" y="11">WINDOW</text>';
  var ribs="",nodes="",labs="",y=pad,sy=pad;
  a.legs.forEach(function(l,i){{
    var h=hs[i], fill=i%3===0?"var(--blue)":(i%3===1?"var(--green)":"var(--orange)");
    var m1=(xJ+wJ+xD)/2, m2=(xD+wD+xW)/2;
    ribs+='<path opacity=".26" fill="'+fill+'" d="M'+(xJ+wJ)+','+sy.toFixed(1)+
      ' C'+m1+','+sy.toFixed(1)+' '+m1+','+y.toFixed(1)+' '+xD+','+y.toFixed(1)+
      ' L'+xD+','+(y+h).toFixed(1)+' C'+m1+','+(y+h).toFixed(1)+' '+m1+','+(sy+h).toFixed(1)+
      ' '+(xJ+wJ)+','+(sy+h).toFixed(1)+' Z"/>';
    ribs+='<path opacity=".26" fill="'+fill+'" d="M'+(xD+wD)+','+y.toFixed(1)+
      ' C'+m2+','+y.toFixed(1)+' '+m2+','+y.toFixed(1)+' '+xW+','+y.toFixed(1)+
      ' L'+xW+','+(y+h).toFixed(1)+' C'+m2+','+(y+h).toFixed(1)+' '+m2+','+(y+h).toFixed(1)+
      ' '+(xD+wD)+','+(y+h).toFixed(1)+' Z"/>';
    nodes+='<rect fill="'+fill+'" opacity=".72" x="'+xD+'" y="'+y.toFixed(1)+
      '" width="'+wD+'" height="'+h.toFixed(1)+'" rx="4"/>'+
      '<rect fill="'+fill+'" opacity=".4" x="'+xW+'" y="'+y.toFixed(1)+
      '" width="'+wW+'" height="'+h.toFixed(1)+'" rx="4"/>';
    var cy=y+h/2, room=h>=32;
    labs+='<text class="pm-lab" x="'+(xD+10)+'" y="'+(cy-(room?4:-4)).toFixed(1)+'">'+
      nf(l.n,0)+"\u00d7 "+l.name+"</text>";
    if(room) labs+='<text class="pm-sub" x="'+(xD+10)+'" y="'+(cy+10).toFixed(1)+'">'+
      nf(l.share*100,1)+"% of work \u00b7 "+nf(l.kw,1)+" kW</text>";
    labs+='<text class="pm-lab" x="'+(xW+10)+'" y="'+(cy-(room?4:-4)).toFixed(1)+'">'+win+"</text>";
    if(room){{ var b=[dur(l.hours!==undefined?l.hours:a.hours)];
      if(GRID.ok){{ b.push(SYMBOL+nf(GRID.price_cheap,0)+"/MWh");
                   b.push(nf(GRID.carbon_clean,0)+" gCO\u2082"); }}
      labs+='<text class="pm-sub" x="'+(xW+10)+'" y="'+(cy+10).toFixed(1)+'">'+
        b.join(" \u00b7 ")+"</text>"; }}
    y+=h+gap; sy+=h+gap;
  }});
  out+=ribs+'<rect class="pm-job" x="'+xJ+'" y="'+pad+'" width="'+wJ+'" height="'+
    jobH.toFixed(1)+'" rx="4"/><text class="pm-joblab" x="'+(xJ+10)+'" y="'+
    (pad+jobH/2-4).toFixed(1)+'">'+modelOf().name+"</text>"+
    '<text class="pm-jobsub" x="'+(xJ+10)+'" y="'+(pad+jobH/2+10).toFixed(1)+'">'+S.task+"</text>"+
    nodes+labs;
  if(!a.fits) out+='<text class="pm-warn" x="'+(xD+wD-6)+'" y="'+(pad+12)+
    '" text-anchor="end">memory short</text>';
  svg.innerHTML=out;
  var fs=document.getElementById("fleetSum");
  if(fs) fs.textContent = a.legs.reduce(function(s,l){{return s+l.n;}},0)+
    " accelerators \u00b7 "+nf(a.mem,0)+" GB \u00b7 "+nf(a.kw,1)+" kW \u00b7 "+
    dur(a.hours)+" \u00b7 "+nf(a.kwh,0)+" kWh";
}}
function drawFleet(){{
  var el=document.getElementById("fleet"); if(!el) return;
  if(!FLEET.length){{
    el.innerHTML='<span class="fleet-chip"><i>single device above \u2014 add groups for a mixed fleet</i></span>';
  }} else {{
    el.innerHTML=FLEET.map(function(g,i){{
      return '<span class="fleet-chip"><b>'+g.n+'\u00d7</b> '+D[g.dev].name+' <i>'+
        D[g.dev].vendor+'</i><button type="button" data-rm="'+i+'">\u00d7</button></span>'; }}).join("");
    el.querySelectorAll("[data-rm]").forEach(function(b){{
      b.addEventListener("click",function(){{ FLEET.splice(+b.dataset.rm,1); drawFleet(); }}); }});
  }}
  drawPath();
}}
(function(){{
  var sel=document.getElementById("addDev"); if(!sel) return;
  sel.innerHTML=Object.keys(D).map(function(k){{
    return '<option value="'+k+'">'+D[k].vendor+" "+D[k].name+"</option>"; }}).join("");
  document.getElementById("addBtn").addEventListener("click",function(){{
    FLEET.push({{dev:sel.value,n:+document.getElementById("addN").value}}); drawFleet(); }});
  drawFleet();
}})();
render();
{EXPAND_JS}
</script>
</body>
</html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the model simulator.")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    print("Fetching grid context …")
    grid = grid_context()
    print("Fetching site weather …")
    sites = build_sites()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(device_specs(), model_specs(), grid, sites), encoding="utf-8")
    print(f"{len(model_catalog.CATALOG)} models × {len(catalog.CATALOG)} devices "
          f"× {len(COUNTS)} fleet sizes × 6 precisions, computed live → {OUT}")
    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
