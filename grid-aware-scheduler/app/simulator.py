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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.gb import GBAdapter
from adapters.gb_regional import GBRegionalAdapter
from adapters.weather import PRESETS, WeatherAdapter
from core import models as model_catalog
from core.grid import PERIOD_HOURS
from core.renewables import solar_capacity_factor, wind_capacity_factor
from hardware import catalog

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
    return {k: {
        "name": m.name, "family": m.family, "params": m.params_b,
        "active": m.compute_params_b, "moe": m.is_moe, "notes": m.notes,
    } for k, m in model_catalog.CATALOG.items()}


def build_sites(days: int = 2) -> dict:
    """Per-location renewable capacity factors, hour by hour."""
    weather, regional = WeatherAdapter(), GBRegionalAdapter()
    sites: dict = {}
    for loc in PRESETS:
        try:
            wx = weather.forecast(loc, days=days)
            region = regional.for_postcode(loc.postcode) if loc.postcode else None
            sites[loc.name] = {
                "name": loc.name, "region": region.name if region else "—",
                "carbon": region.carbon_forecast if region else None,
                "solar": [round(solar_capacity_factor(w.solar_radiation_wm2,
                                                      w.temperature_c), 4) for w in wx],
                "wind": [round(wind_capacity_factor(w.wind_speed_100m_ms), 4) for w in wx],
            }
        except Exception:
            continue
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


def render(devices: dict, models: dict, grid: dict, sites: dict) -> str:
    model_opts = "".join(
        '<optgroup label="{}">{}</optgroup>'.format(
            html.escape(f),
            "".join(f'<option value="{k}">{html.escape(m["name"])}</option>'
                    for k, m in models.items() if m["family"] == f))
        for f in model_catalog.families())
    model_opts += ('<optgroup label="Custom">'
                   '<option value="__custom__">Custom model…</option></optgroup>')
    device_opts = "".join(
        f'<option value="{k}">{html.escape(d["vendor"])} {html.escape(d["name"])}</option>'
        for k, d in devices.items())
    count_opts = "".join(f'<option value="{n}">{n:,}</option>' for n in COUNTS)
    prec_opts = "".join(f'<option value="{p}">{p}</option>' for p in model_catalog.PRECISIONS)
    site_opts = "".join(f'<option value="{html.escape(k)}">{html.escape(k)}</option>' for k in sites)
    cap_opts = "".join(f'<option value="{c}">{c:,} kW</option>'
                       for c in (0, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 25000))

    note = (f"Priced against live GB data, {html.escape(grid['from'])}–{html.escape(grid['to'])}."
            if grid.get("ok") else "Grid data unavailable — energy shown without cost or carbon.")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model Simulator — Grid-Aware Scheduler</title>
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
*{{box-sizing:border-box}}
body{{margin:0;padding:0 24px 72px;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1120px;margin:0 auto}}
header{{padding:56px 0 28px}}
h1{{margin:0 0 6px;font-size:40px;line-height:1.08;font-weight:700;letter-spacing:-.022em}}
.sub{{color:var(--text-2);font-size:17px;margin:0}}
nav{{margin-top:16px;display:flex;gap:8px}}
nav a{{font-size:13px;font-weight:550;text-decoration:none;padding:6px 14px;border-radius:980px;
color:var(--text-2);background:color-mix(in srgb,var(--text) 5%,transparent)}}
nav a.on{{background:var(--blue);color:#fff}}
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
.seg{{display:inline-flex;gap:3px;background:color-mix(in srgb,var(--text) 5%,transparent);
padding:3px;border-radius:980px}}
.seg button{{font:inherit;font-size:13px;font-weight:550;padding:7px 16px;border:0;border-radius:980px;
background:transparent;color:var(--text-2);cursor:pointer}}
.seg button.on{{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.10)}}
.hide{{display:none}}
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
@media(max-width:720px){{h1{{font-size:32px}}}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Model Simulator</h1>
  <p class="sub">How a model runs on hardware you don't have — and what the grid charges for it.</p>
  <nav><a href="/">Grid</a><a href="/simulator" class="on">Simulator</a></nav>
</header>

<section class="card">
  <h2>Configuration</h2>
  <p class="note">{note}</p>
  <div class="controls">
    <div class="ctl"><label for="model">Model</label><select id="model">{model_opts}</select></div>
    <div class="ctl custom-only hide"><label for="cparams">Parameters (B)</label>
      <input id="cparams" type="number" min="0.01" step="0.1" value="7"></div>
    <div class="ctl custom-only hide"><label for="cactive">Active (B) — 0 if dense</label>
      <input id="cactive" type="number" min="0" step="0.1" value="0"></div>
    <div class="ctl"><label for="prec">Precision</label><select id="prec">{prec_opts}</select></div>
    <div class="ctl"><label for="device">Hardware</label><select id="device">{device_opts}</select></div>
    <div class="ctl"><label for="count">How many</label><select id="count">{count_opts}</select></div>
    <div class="ctl"><label for="tokens">Tokens</label><select id="tokens">
      <option value="10000000">10M</option><option value="100000000">100M</option>
      <option value="1000000000">1B</option><option value="10000000000">10B</option>
      <option value="100000000000">100B</option><option value="15000000000000">15T (full pretrain)</option>
    </select></div>
    <div class="ctl"><label>Task</label><span class="seg" id="task">
      <button type="button" data-t="training" class="on">Training</button>
      <button type="button" data-t="inference">Inference</button></span></div>
  </div>
  <div class="tiles" id="tiles"></div>
  <div id="warn"></div>
  <ul class="assum" id="assum"></ul>
</section>

<section class="card">
  <h2>On-site renewables</h2>
  <p class="note"><span class="prov ESTIMATED">ESTIMATED</span></p>
  <div class="controls">
    <div class="ctl"><label for="site">Location</label><select id="site">{site_opts}</select></div>
    <div class="ctl"><label for="solar">Solar capacity</label><select id="solar">{cap_opts}</select></div>
    <div class="ctl"><label for="wind">Wind capacity</label><select id="wind">{cap_opts}</select></div>
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
    <th>Fleet</th><th>Runtime</th><th>Energy</th><th>Scaling</th><th>Memory</th>
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

var S = {{ model:"llama31-8b", prec:"bf16", device:"h100-sxm", count:8,
          task:"training", tokens:1e9, cparams:7, cactive:0,
          site:Object.keys(SITES)[0], solar:10, wind:5 }};

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
function memNeed(m){{ return wbytes(m)/1e9 * (S.task==="training"?4:1.25); }}
function scaling(m,dev,n){{
  if(n<=1 || dev.link==="Unified memory") return 1;
  var l = LINK[dev.link]||0; if(!l) return 1;
  var compute = (6*m.active*1e9*2e6)/(dev.tflops*dev.mfu*n*1e12);
  var comm = (2*(n-1)/n*wbytes(m))/(l*1e9);
  return compute/(compute+comm);
}}
function est(dk,n){{
  var dev=D[dk], m=modelOf(), eff=scaling(m,dev,n), mem=dev.mem*n, hours, kw;
  if(S.task==="training"){{
    hours = (6*m.active*1e9*S.tokens)/(dev.tflops*dev.mfu*n*eff*1e12)/3600;
    kw = dev.tdp*n/1000;
  }} else {{
    hours = (S.tokens/((dev.bw*1e9*n)/wbytes(m)))/3600;
    kw = (dev.idle+(dev.tdp-dev.idle)*0.6)*n/1000;
  }}
  return {{hours:hours, kwh:kw*hours, kw:kw, mem:mem, scaling:eff, fits:mem>=memNeed(m)}};
}}
function money(k){{ return GRID.ok ? k*GRID.price_cheap/1000 : null; }}
function co2(k){{ return GRID.ok ? k*GRID.carbon_clean/1000 : null; }}

function render(){{
  var m=modelOf(), dev=D[S.device], r=est(S.device,S.count);
  var c=money(r.kwh), g=co2(r.kwh);
  document.getElementById("tiles").innerHTML =
    tile("Runtime",dur(r.hours),nf(r.kw,1)+" kW draw") +
    tile("Energy",nf(r.kwh,0)+" kWh","whole run") +
    tile("Cost",c===null?"—":"£"+nf(c,2),"cheapest window") +
    tile("Carbon",g===null?"—":nf(g,1)+" kg","cleanest window") +
    tile("Memory",nf(r.mem,0)+" GB","need ~"+nf(memNeed(m),0)+" GB");

  var w=[];
  if(!r.fits) w.push('<div class="warn"><b>Does not fit.</b> '+m.name+' at '+S.prec+
    ' needs about '+nf(memNeed(m),0)+' GB for '+S.task+'; '+nf(S.count,0)+'\\u00d7 '+dev.name+
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
      " \\u2014 communication only; pipeline bubbles and stragglers not modelled, so optimistic."
      : "Single device \\u2014 no scaling loss.",
    "Source: "+(dev.source||"\\u2014")
  ].map(function(x){{return "<li>"+x+"</li>";}}).join("");

  var rows=Object.keys(D).map(function(k){{return {{k:k,d:D[k],r:est(k,S.count)}};}})
    .sort(function(a,b){{ if(a.r.fits!==b.r.fits) return a.r.fits?-1:1; return a.r.kwh-b.r.kwh; }});
  var best=rows.find(function(x){{return x.r.fits;}});
  document.querySelector("#cmp tbody").innerHTML=rows.map(function(x){{
    return '<tr class="'+(!x.r.fits?"nofit":(best&&x.k===best.k?"best":""))+'"><td>'+
      x.d.vendor+" "+x.d.name+'<span class="prov '+x.d.prov+'">'+x.d.prov+
      "</span></td><td>"+dur(x.r.hours)+"</td><td>"+nf(x.r.kw,1)+" kW</td><td><b>"+
      nf(x.r.kwh,0)+" kWh</b></td><td>"+nf(x.r.mem,0)+" GB</td><td>"+
      (money(x.r.kwh)===null?"—":"£"+nf(money(x.r.kwh),2))+"</td><td>"+
      (co2(x.r.kwh)===null?"—":nf(co2(x.r.kwh),1)+" kg")+"</td></tr>";
  }}).join("");

  document.querySelector("#scale tbody").innerHTML=COUNTS.map(function(n){{
    var e=est(S.device,n);
    return "<tr"+(n===S.count?' class="best"':"")+"><td>"+nf(n,0)+"\\u00d7 "+dev.name+
      "</td><td>"+dur(e.hours)+"</td><td>"+nf(e.kwh,0)+" kWh</td><td>"+
      nf(e.scaling*100,0)+"%</td><td>"+nf(e.mem,0)+" GB</td></tr>";
  }}).join("");

  renewables(r.kw);
  document.getElementById("foot").textContent =
    m.name+" \\u00b7 "+nf(m.params,1)+"B parameters at "+S.prec+" \\u00b7 needs ~"+
    nf(memNeed(m),0)+" GB.";
}}

function renewables(load){{
  var s=SITES[S.site]; if(!s) return;
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
  var svg=document.getElementById("rchart"),W=1000,H=190,P=22;
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
["model","prec","device","site"].forEach(function(i){{bind(i,i,false);}});
["count","tokens","cparams","cactive","solar","wind"].forEach(function(i){{bind(i,i,true);}});
document.querySelectorAll("#task button").forEach(function(b){{
  b.addEventListener("click",function(){{
    document.querySelectorAll("#task button").forEach(function(o){{o.classList.remove("on");}});
    b.classList.add("on"); S.task=b.dataset.t; render(); }});
}});
render();
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
