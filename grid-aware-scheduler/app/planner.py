"""Interactive workload placement planner.

The page evaluates every feasible hardware and half-hour start combination at
the operator-selected grid location. It exposes the objective weights and the
constraints rather than hiding them behind a recommendation. The matching
Python engine lives in :mod:`core.planner` for batch and API use; this browser
implementation keeps local scenario exploration immediate.
"""
from __future__ import annotations

import html
import json
from urllib.parse import urlencode

from app.markets import MarketContext
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS
from app.simulator import COUNTS, device_specs, model_specs
from core import models as model_catalog


def _options(context: MarketContext) -> str:
    return "".join(
        f'<option value="{html.escape(x.key)}"'
        f'{" selected" if x.key == context.location_key else ""}>'
        f'{html.escape(x.name)} · {html.escape(x.detail)}</option>'
        for x in context.locations
    )


def render(context: MarketContext) -> str:
    devices, models = device_specs(), model_specs()
    model_options = "".join(
        '<optgroup label="{}">{}</optgroup>'.format(
            html.escape(family),
            "".join(
                f'<option value="{key}">{html.escape(model["name"])}</option>'
                for key, model in models.items() if model["family"] == family
            ),
        )
        for family in model_catalog.families()
    )
    count_options = "".join(
        f'<option value="{n}"{" selected" if n == 8 else ""}>{n:,}</option>'
        for n in COUNTS
    )
    precision_options = "".join(
        f'<option value="{p}"{" selected" if p == "bf16" else ""}>{p}</option>'
        for p in model_catalog.PRECISIONS
    )
    points = [
        {"t": p.timestamp.isoformat(), "p": p.price, "c": p.carbon_intensity}
        for p in context.series
    ]
    current_market = html.escape(context.market_key)
    grid_href = "/grid?" + urlencode({
        "market": context.market_key,
        "location": context.location_key,
    })
    simulator_href = "/simulator?" + urlencode({
        "market": context.market_key,
        "location": context.location_key,
    })
    operations_href = "/?" + urlencode({
        "market": context.market_key,
        "location": context.location_key,
    })
    custom_node = "" if not context.allows_custom_node else """
      <div class="ctl"><label for="customNode">Custom CAISO PNode</label>
        <div class="joined"><input id="customNode" placeholder="Enter exact node ID">
        <button type="button" id="loadNode">Load</button></div></div>"""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Placement Planner · Grid-Aware Scheduler</title>
{THEME_BOOTSTRAP}
<style>
:root {{ color-scheme:light dark; --bg:#F2F2F7; --card:#fff; --text:#000;
  --text-2:rgba(60,60,67,.62); --text-3:rgba(60,60,67,.30); --sep:rgba(60,60,67,.18);
  --blue:#007AFF; --green:#248A3D; --orange:#B35300; --red:#C7261B;
  --price:var(--blue); --carbon:var(--green);
  --shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.06); }}
@media(prefers-color-scheme:dark) {{ :root:not([data-theme="light"]) {{ --bg:#000; --card:#1C1C1E; --text:#fff;
  --text-2:rgba(235,235,245,.62); --text-3:rgba(235,235,245,.30);
  --sep:rgba(84,84,88,.65); --blue:#0A84FF; --green:#2A9D48;
  --orange:#E08A2E; --red:#E2554A; --shadow:none; }} }}
:root[data-theme="dark"] {{ --bg:#000; --card:#1C1C1E; --text:#fff;
  --text-2:rgba(235,235,245,.62); --text-3:rgba(235,235,245,.30);
  --sep:rgba(84,84,88,.65); --blue:#0A84FF; --green:#2A9D48;
  --orange:#E08A2E; --red:#E2554A; --shadow:none; }}
*{{box-sizing:border-box}} body{{margin:0;padding:0 24px 72px;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased}} .wrap{{max-width:1180px;margin:0 auto}}
header{{padding:52px 0 26px}} h1{{font-size:40px;line-height:1.08;letter-spacing:-.025em;margin:0 0 6px}}
.sub{{font-size:17px;color:var(--text-2);margin:0}} nav{{display:flex;gap:8px;margin-top:16px}}
nav a{{font-size:13px;font-weight:600;text-decoration:none;padding:6px 14px;border-radius:999px;
color:var(--text-2);background:color-mix(in srgb,var(--text) 5%,transparent)}}
nav a.on{{background:var(--blue);color:#fff}} .card{{background:var(--card);border-radius:18px;
padding:22px 24px;box-shadow:var(--shadow);margin-bottom:18px}} .card h2{{font-size:20px;margin:0 0 4px}}
.note{{color:var(--text-2);font-size:13px;margin:0 0 17px}} .controls{{display:grid;
grid-template-columns:repeat(auto-fit,minmax(166px,1fr));gap:14px}} .ctl{{display:flex;flex-direction:column;gap:6px}}
.ctl label{{font-size:12px;color:var(--text-2);font-weight:550}} select,input{{width:100%;font:inherit;font-size:13px;
padding:9px 11px;border:1px solid var(--sep);border-radius:10px;background:var(--card);color:var(--text)}}
select:focus,input:focus,button:focus-visible{{outline:3px solid color-mix(in srgb,var(--blue) 25%,transparent);
outline-offset:1px}} .joined{{display:flex}} .joined input{{border-radius:10px 0 0 10px}}
.joined button,.primary{{border:0;background:var(--blue);color:#fff;font:600 13px inherit;padding:0 13px;cursor:pointer}}
.joined button{{border-radius:0 10px 10px 0}} .seg{{display:flex;background:color-mix(in srgb,var(--text) 5%,transparent);
padding:3px;border-radius:999px}} .seg button{{flex:1;border:0;background:transparent;color:var(--text-2);
font:600 13px inherit;padding:7px 10px;border-radius:999px;cursor:pointer}} .seg button.on{{background:var(--card);
color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.12)}} .range-row{{display:grid;grid-template-columns:1fr 38px;
align-items:center;gap:7px}} input[type=range]{{padding:0;border:0;accent-color:var(--blue)}} .weight{{font-size:12px;
text-align:right;font-variant-numeric:tabular-nums}} .hide{{display:none!important}}
.status{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:18px;padding:10px 13px;
border-radius:11px;background:color-mix(in srgb,var(--green) 9%,transparent);color:var(--green);font-size:12px}}
.status b{{font-weight:700}} .status .sep{{color:var(--text-3)}} .tiles{{display:grid;
grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:1px;background:var(--sep);border-radius:14px;overflow:hidden}}
.tile{{padding:15px 16px;background:var(--card)}} .tile label{{display:block;color:var(--text-2);font-size:11px}}
.tile strong{{display:block;font-size:25px;letter-spacing:-.02em;margin:3px 0 1px;font-variant-numeric:tabular-nums}}
.tile small{{display:block;color:var(--text-2);font-size:11px}} .trace{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
margin-top:18px}} .step{{position:relative;padding:14px;border-radius:12px;background:color-mix(in srgb,var(--text) 4%,transparent)}}
.step:not(:last-child)::after{{content:">";position:absolute;right:-9px;top:50%;z-index:2;color:var(--text-3)}}
.step label{{display:block;color:var(--text-2);font-size:10px;text-transform:uppercase;letter-spacing:.08em}}
.step b{{display:block;font-size:14px;margin:4px 0}} .step span{{display:block;color:var(--text-2);font-size:11px}}
.why{{margin:16px 0 0;color:var(--text-2);font-size:13px}} .why b{{color:var(--text)}}
.grid2{{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(460px,1.2fr);gap:18px}}
.pnl{{background:var(--card);border-radius:18px;padding:20px 22px;box-shadow:var(--shadow);margin-bottom:18px;min-height:330px}}
.pnl h3{{margin:0 0 4px;font-size:18px}} .pnl .note{{margin-bottom:12px}} #pareto{{width:100%;height:240px;display:block}}
.axis{{stroke:var(--sep);stroke-width:1;vector-effect:non-scaling-stroke}} .dot{{fill:var(--blue);opacity:.25}}
.dot.front{{fill:var(--green);opacity:.8}} .dot.selected{{fill:var(--orange);opacity:1;stroke:var(--card);stroke-width:3}}
.axis-label{{fill:var(--text-2);font-size:10px}} .legend{{display:flex;gap:14px;color:var(--text-2);font-size:11px}}
.legend i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;background:var(--blue)}}
.legend i.front{{background:var(--green)}} .legend i.sel{{background:var(--orange)}}
.table-card{{overflow:hidden}} .scroll{{overflow:auto;max-height:420px}} table{{width:100%;border-collapse:collapse;
font-size:12px;font-variant-numeric:tabular-nums}} th,td{{padding:8px 9px;border-bottom:1px solid var(--sep);text-align:right;
white-space:nowrap}} th{{position:sticky;top:0;background:var(--card);z-index:1;color:var(--text-2);font-weight:550}}
th:first-child,td:first-child{{text-align:left}} tr.best td{{background:color-mix(in srgb,var(--green) 9%,transparent)}}
.pill{{font-size:9px;padding:2px 5px;border-radius:5px;background:color-mix(in srgb,var(--green) 13%,transparent);color:var(--green)}}
.empty{{padding:20px;border-radius:12px;background:color-mix(in srgb,var(--red) 10%,transparent);color:var(--red)}}
.method{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}} .method div{{padding:13px;border:1px solid var(--sep);border-radius:12px}}
.method b{{display:block;font-size:12px;margin-bottom:4px}} .method span{{color:var(--text-2);font-size:11px}}
.foot{{color:var(--text-2);font-size:12px;margin-top:24px}} {PANEL_CSS}
.section-head{{display:flex;justify-content:space-between;gap:12px;align-items:start;flex-wrap:wrap}}
.actions{{display:flex;gap:7px;flex-wrap:wrap}} .actions button{{border:1px solid var(--sep);border-radius:9px;
background:var(--card);color:var(--text);font:600 11px inherit;padding:7px 10px;cursor:pointer}}
.actions button:hover{{border-color:var(--blue);color:var(--blue)}}
.audit-status{{width:100%;text-align:right;color:var(--text-2);font-size:10px;min-height:15px}}
@media(max-width:800px){{.grid2{{grid-template-columns:1fr}}.trace,.method{{grid-template-columns:1fr 1fr}}
.step::after{{display:none}}}} @media(max-width:560px){{h1{{font-size:32px}}body{{padding-left:14px;padding-right:14px}}
.trace,.method{{grid-template-columns:1fr}}}}
{THEME_CSS}
</style></head><body><div class="wrap">
{THEME_CONTROL}
<header><h1>Placement Planner</h1>
<p class="sub">Choose the hardware and half-hour that best satisfy an explicit operating objective.</p>
<nav><a href="{html.escape(operations_href)}">Operations</a><a href="{html.escape(simulator_href)}">Fleet Lab</a><a class="on" href="/planner">Placement Lab</a><a href="{html.escape(grid_href)}">Sites &amp; Grid</a><a href="/decisions">Decisions</a></nav></header>

<section class="card"><h2>Operating constraints</h2>
<p class="note">The selected location is a facility constraint. The planner searches every catalogued device and legal start window there.</p>
<div class="status"><b>{html.escape(context.signal_mode)}</b><span class="sep">|</span>
{html.escape(context.market_name)}<span class="sep">|</span>{html.escape(context.location_name)}
<span class="sep">|</span>{len(points)} half-hours</div>
<div class="controls">
  <div class="ctl"><label for="market">Power market</label><select id="market">
    <option value="GB"{" selected" if context.market_key == "GB" else ""}>Great Britain</option>
    <optgroup label="United States">
      <option value="CAISO"{" selected" if context.market_key == "CAISO" else ""}>California ISO</option>
      <option value="NYISO"{" selected" if context.market_key == "NYISO" else ""}>New York ISO</option>
    </optgroup>
  </select></div>
  <div class="ctl"><label for="location">Grid location</label><select id="location">{_options(context)}</select></div>
  {custom_node}
  <div class="ctl"><label for="model">Model</label><select id="model">{model_options}</select></div>
  <div class="ctl"><label>Task</label><div class="seg" id="task"><button class="on" data-task="training">Training</button>
    <button data-task="inference">Inference</button></div></div>
  <div class="ctl"><label for="precision">Precision</label><select id="precision">{precision_options}</select></div>
  <div class="ctl"><label for="count">Accelerators</label><select id="count">{count_options}</select></div>
  <div class="ctl"><label for="tokens">Token budget</label><select id="tokens">
    <option value="10000000">10M</option><option value="100000000">100M</option>
    <option value="1000000000" selected>1B</option><option value="10000000000">10B</option>
    <option value="100000000000">100B</option></select></div>
  <div class="ctl"><label for="deadline">Deadline from first interval</label><select id="deadline">
    <option value="6">6 hours</option><option value="12">12 hours</option><option value="24" selected>24 hours</option>
    <option value="36">36 hours</option><option value="48">48 hours</option></select></div>
  <div class="ctl"><label for="maxCost">Hard cost cap · {html.escape(context.symbol)} (0 = off)</label>
    <input id="maxCost" type="number" min="0" step="0.01" value="0"></div>
  <div class="ctl"><label for="maxCarbon">Hard carbon cap · kg (0 = off)</label>
    <input id="maxCarbon" type="number" min="0" step="0.01" value="0"></div>
  <div class="ctl"><label for="maxDelay">Latest start delay · hours (-1 = off)</label>
    <input id="maxDelay" type="number" min="-1" step="0.5" value="-1"></div>
  <div class="ctl training-only"><label for="shard">Training memory</label><select id="shard">
    <option value="zero3">FSDP / ZeRO-3 sharded</option><option value="replicated">Replicated</option></select></div>
  <div class="ctl training-only"><label for="statebytes">Training state bytes / parameter</label><select id="statebytes">
    <option value="16">16 · mixed-precision Adam</option><option value="8">8 · reduced-state optimiser</option></select></div>
  <div class="ctl training-only"><label for="headroom">Activation and buffer reserve</label><select id="headroom">
    <option value="10">10%</option><option value="20" selected>20%</option><option value="30">30%</option><option value="50">50%</option></select></div>
  <div class="ctl inference-only hide"><label for="context">Context length</label><select id="context">
    <option value="2048">2k</option><option value="8192" selected>8k</option><option value="32768">32k</option>
    <option value="131072">128k</option></select></div>
  <div class="ctl inference-only hide"><label for="batch">Concurrent sequences</label><select id="batch">
    <option value="1">1</option><option value="8" selected>8</option><option value="32">32</option><option value="128">128</option></select></div>
  <div class="ctl inference-only hide"><label for="kvprecision">KV cache precision</label><select id="kvprecision">
    <option value="bf16">bf16</option><option value="fp16">fp16</option><option value="fp8">fp8</option><option value="int8">int8</option></select></div>
  <div class="ctl"><label for="pue">Facility PUE</label><select id="pue"><option value="1">1.00</option>
    <option value="1.1">1.10</option><option value="1.2" selected>1.20</option><option value="1.3">1.30</option>
    <option value="1.4">1.40</option><option value="1.5">1.50</option></select></div>
  <div class="ctl"><label for="system">Measured system efficiency</label><select id="system"><option value="100">100%</option>
    <option value="90">90%</option><option value="85" selected>85%</option><option value="80">80%</option><option value="70">70%</option></select></div>
  <div class="ctl"><label for="costW">Cost weight</label><div class="range-row"><input id="costW" type="range" min="0" max="100" value="50"><span class="weight" id="costWV">50</span></div></div>
  <div class="ctl"><label for="carbonW">Carbon weight</label><div class="range-row"><input id="carbonW" type="range" min="0" max="100" value="50"><span class="weight" id="carbonWV">50</span></div></div>
  <div class="ctl"><label for="delayW">Delay weight</label><div class="range-row"><input id="delayW" type="range" min="0" max="100" value="0"><span class="weight" id="delayWV">0</span></div></div>
</div></section>

<section class="card" id="decision"><div class="section-head"><div><h2>Recommended placement</h2>
<p class="note">Exact enumeration across feasible devices and starts.</p></div><div class="actions">
<button type="button" id="saveDecision">Save audited decision</button><button type="button" id="copyLink">Copy review link</button>
<button type="button" id="exportPlan">Plan JSON</button><button type="button" id="exportCsv">Alternatives CSV</button>
<div class="audit-status" id="auditStatus" aria-live="polite"></div></div></div>
<div id="result"></div></section>
<div class="grid2">
  <section class="pnl"><h3>Cost and carbon frontier</h3><p class="note">Click to inspect full screen. Frontier points are not beaten on both measures.</p>
    <svg id="pareto" viewBox="0 0 500 260" role="img" aria-label="Cost and carbon Pareto frontier"></svg>
    <div class="legend"><span><i></i>Feasible</span><span><i class="front"></i>Pareto</span><span><i class="sel"></i>Selected</span></div></section>
  <section class="card table-card"><h2>Ranked alternatives</h2><p class="note" id="countNote"></p>
    <div class="scroll"><table><thead><tr><th>Hardware</th><th>Start UTC</th><th>Runtime</th><th>Energy</th>
    <th>Cost</th><th>Carbon</th><th>Score</th></tr></thead><tbody id="rows"></tbody></table></div></section>
</div>
<section class="card"><h2>Decision method</h2><p class="note">Every result can be reproduced from the displayed inputs.</p>
<div class="method"><div><b>1 · Feasibility</b><span>Reject memory failures, runtimes past the deadline, incomplete signals and windows outside hard policy caps.</span></div>
<div><b>2 · Exact enumeration</b><span>Price every legal contiguous half-hour window, including fractional final-period energy.</span></div>
<div><b>3 · Explicit score</b><span>Min-max normalise cost, carbon and delay, then apply the visible weights. No hidden objective.</span></div></div></section>
<p class="foot">{html.escape(context.provenance)} {html.escape(context.carbon_label)} is not presented as nodal carbon. Configuration is stored in the URL for review and sharing.</p>
</div><script>
var D={json.dumps(devices)}; var M={json.dumps(models)}; var G={json.dumps(points)};
var BYTES={json.dumps(model_catalog.BYTES_PER_PARAM)}; var SYMBOL={json.dumps(context.symbol)};
var LAST_PLAN=null;
var S={{model:"llama31-8b",task:"training",precision:"bf16",count:8,tokens:1e9,deadline:24,
shard:"zero3",statebytes:16,headroom:20,context:8192,batch:8,kvprecision:"bf16",
pue:1.2,system:85,costW:50,carbonW:50,delayW:0,maxCost:0,maxCarbon:0,maxDelay:-1}};
var LINK={{"NVLink":450,"PCIe":50,"Ethernet":25,"Unified memory":0,"None":0}};
function nf(x,d){{return Number(x).toLocaleString("en-GB",{{minimumFractionDigits:d,maximumFractionDigits:d}})}}
function duration(h){{if(h<1)return nf(h*60,0)+" min";if(h<48)return nf(h,1)+" h";return nf(h/24,1)+" days"}}
function arch(m){{if(m.arch)return m.arch;return {{layers:32,hidden:4096,heads:32,kvheads:8,estimated:true}}}}
function weightBytes(m){{return m.params*1e9*BYTES[S.precision]}}
function kvGB(m){{var a=arch(m),hd=a.hidden/a.heads;return 2*a.layers*a.kvheads*hd*S.context*S.batch*BYTES[S.kvprecision]/1e9}}
function memoryNeed(m,n){{var w=weightBytes(m)/1e9;if(S.task==="inference")return w+kvGB(m);
var state=m.params*S.statebytes*(1+S.headroom/100);return S.shard==="zero3"?state:state*n}}
function fits(m,d,n){{var state=m.params*S.statebytes*(1+S.headroom/100);
if(S.task==="training"&&S.shard==="replicated")return d.mem>=state;
return d.mem*n>=memoryNeed(m,n)}}
function scaling(m,d,n){{var sys=Math.max(.01,S.system/100);if(n<=1||d.link==="Unified memory")return sys;
var link=LINK[d.link]||0;if(!link)return sys;var compute=(6*m.active*1e9*2e6)/(d.tflops*d.mfu*n*1e12);
var comm=(2*(n-1)/n*weightBytes(m))/(link*1e9);return compute/(compute+comm)*sys}}
function estimate(key){{var d=D[key],m=M[S.model],n=S.count,eff=scaling(m,d,n),hours,itkw;
if(S.task==="training"){{hours=(6*m.active*1e9*S.tokens)/(d.tflops*d.mfu*n*eff*1e12)/3600;itkw=d.tdp*n/1000}}
else{{hours=S.tokens/((d.bw*1e9*n)/weightBytes(m))/3600/Math.max(.01,S.system/100);itkw=(d.idle+(d.tdp-d.idle)*.6)*n/1000}}
return {{key:key,d:d,hours:hours,itkw:itkw,kw:itkw*S.pue,fit:fits(m,d,n)}}}}
function loadState(){{var q=new URLSearchParams(location.search);Object.keys(S).forEach(function(k){{if(!q.has(k))return;
var raw=q.get(k);if(["model","task","precision","shard","kvprecision"].indexOf(k)>=0)S[k]=raw;
else{{var numeric=Number(raw);if(Number.isFinite(numeric))S[k]=numeric}}}})}}
function validateState(){{if(!M[S.model])S.model="llama31-8b";if(!BYTES[S.precision])S.precision="bf16";
if(["training","inference"].indexOf(S.task)<0)S.task="training";if(["zero3","replicated"].indexOf(S.shard)<0)S.shard="zero3";
if(!BYTES[S.kvprecision])S.kvprecision="bf16";if([8,16].indexOf(S.statebytes)<0)S.statebytes=16;
if([10,20,30,50].indexOf(S.headroom)<0)S.headroom=20;
if(!(S.count>0))S.count=8;if(!(S.tokens>0))S.tokens=1e9;S.deadline=Math.min(48,Math.max(.5,S.deadline||24));
S.pue=Math.min(3,Math.max(1,S.pue||1.2));S.system=Math.min(100,Math.max(1,S.system||85));
S.context=Math.max(1,S.context||8192);S.batch=Math.max(1,S.batch||8);["costW","carbonW","delayW"].forEach(function(k){{S[k]=Math.min(100,Math.max(0,S[k]||0))}});
S.maxCost=Math.max(0,S.maxCost||0);S.maxCarbon=Math.max(0,S.maxCarbon||0);S.maxDelay=Math.max(-1,Number.isFinite(S.maxDelay)?S.maxDelay:-1)}}
function syncState(){{var q=new URLSearchParams(location.search);Object.keys(S).forEach(function(k){{q.set(k,String(S[k]))}});
history.replaceState(null,"",location.pathname+"?"+q.toString())}}
function goMarket(market,locationKey){{var q=new URLSearchParams(location.search);q.set("market",market);q.set("location",locationKey);
location.href=location.pathname+"?"+q.toString()}}
function normal(v,lo,hi){{return hi<=lo?0:(v-lo)/(hi-lo)}}
function enumerate(){{var out=[],rejected={{memory:0,deadline:0,signal:0,policy:0}},origin=G.length?Date.parse(G[0].t):0;
Object.keys(D).forEach(function(key){{var e=estimate(key);if(!e.fit){{rejected.memory++;return}}if(e.hours>S.deadline){{rejected.deadline++;return}}
var periods=Math.max(1,Math.ceil(e.hours*2));for(var start=0;start+periods<=G.length;start++){{
var delay=(Date.parse(G[start].t)-origin)/36e5;if(delay+e.hours>S.deadline+1e-9)continue;
var contiguous=true;for(var q=1;q<periods;q++)if(Date.parse(G[start+q].t)-Date.parse(G[start+q-1].t)!==18e5)contiguous=false;
if(!contiguous)continue;var remaining=e.hours,cost=0,carbon=0,okP=true,okC=true;
for(var j=0;j<periods;j++){{var h=Math.min(.5,remaining),kwh=e.kw*h,p=G[start+j];
if(p.p===null)okP=false;else cost+=kwh*p.p/1000;if(p.c===null)okC=false;else carbon+=kwh*p.c/1000;remaining-=h}}
if(((S.costW>0||S.maxCost>0)&&!okP)||((S.carbonW>0||S.maxCarbon>0)&&!okC)){{rejected.signal++;continue}}
if((S.maxCost>0&&cost>S.maxCost)||(S.maxCarbon>0&&carbon>S.maxCarbon)||(S.maxDelay>=0&&delay>S.maxDelay)){{rejected.policy++;continue}}
out.push({{e:e,start:start,time:G[start].t,finish:new Date(Date.parse(G[start].t)+e.hours*36e5),
delay:delay,kwh:e.kw*e.hours,cost:okP?cost:null,carbon:okC?carbon:null,score:0,pareto:false}})}}}});
if(!out.length)return {{options:out,rejected:rejected}};var costs=out.filter(x=>x.cost!==null).map(x=>x.cost),
carbons=out.filter(x=>x.carbon!==null).map(x=>x.carbon),delays=out.map(x=>x.delay);
var clo=Math.min.apply(null,costs.length?costs:[0]),chi=Math.max.apply(null,costs.length?costs:[0]);
var glo=Math.min.apply(null,carbons.length?carbons:[0]),ghi=Math.max.apply(null,carbons.length?carbons:[0]);
var dlo=Math.min.apply(null,delays),dhi=Math.max.apply(null,delays),tw=S.costW+S.carbonW+S.delayW||1;
out.forEach(function(x){{x.score=(S.costW*normal(x.cost||0,clo,chi)+S.carbonW*normal(x.carbon||0,glo,ghi)+
S.delayW*normal(x.delay,dlo,dhi))/tw}});var comp=out.filter(x=>x.cost!==null&&x.carbon!==null)
.sort((a,b)=>a.cost-b.cost||a.carbon-b.carbon),best=Infinity,i=0;
while(i<comp.length){{var j=i,min=comp[i].carbon;while(j<comp.length&&Math.abs(comp[j].cost-comp[i].cost)<1e-9){{min=Math.min(min,comp[j].carbon);j++}}
if(min<best)for(var k=i;k<j;k++)if(Math.abs(comp[k].carbon-min)<1e-9)comp[k].pareto=true;best=Math.min(best,min);i=j}}
out.sort((a,b)=>a.score-b.score||(a.cost??Infinity)-(b.cost??Infinity)||(a.carbon??Infinity)-(b.carbon??Infinity)||a.time.localeCompare(b.time));
return {{options:out,rejected:rejected}}}}
function tile(label,value,sub){{return '<div class="tile"><label>'+label+'</label><strong>'+value+'</strong><small>'+sub+'</small></div>'}}
function drawChart(options,selected){{var svg=document.getElementById("pareto"),pts=options.filter(x=>x.cost!==null&&x.carbon!==null);
if(!pts.length){{svg.dataset.inspector=JSON.stringify({{kind:"scatter",xLabel:"Cost",xSuffix:" "+SYMBOL,
yLabel:"Carbon",ySuffix:" kg CO₂",precision:3,series:[]}});
svg.innerHTML='<text x="20" y="40" class="axis-label">Cost and carbon signals are required.</text>';return}}
var W=500,H=260,L=42,R=12,T=12,B=32,xlo=Math.min(...pts.map(x=>x.cost)),xhi=Math.max(...pts.map(x=>x.cost)),
ylo=Math.min(...pts.map(x=>x.carbon)),yhi=Math.max(...pts.map(x=>x.carbon));
function X(v){{return L+(W-L-R)*(v-xlo)/((xhi-xlo)||1)}}function Y(v){{return T+(H-T-B)*(1-(v-ylo)/((yhi-ylo)||1))}}
var dots=pts.map(function(x){{var cls=x===selected?"dot selected":(x.pareto?"dot front":"dot");return '<circle class="'+cls+
'" cx="'+X(x.cost).toFixed(1)+'" cy="'+Y(x.carbon).toFixed(1)+'" r="'+(x===selected?6:(x.pareto?4:2))+'"><title>'+x.e.d.name+
' · '+SYMBOL+nf(x.cost,2)+' · '+nf(x.carbon,2)+' kg</title></circle>'}}).join("");
svg.innerHTML='<line class="axis" x1="'+L+'" y1="'+(H-B)+'" x2="'+(W-R)+'" y2="'+(H-B)+'"/><line class="axis" x1="'+L+
'" y1="'+T+'" x2="'+L+'" y2="'+(H-B)+'"/><text class="axis-label" x="'+(W-R)+'" y="'+(H-8)+'" text-anchor="end">cost '+SYMBOL+
'</text><text class="axis-label" x="8" y="'+(T+4)+'">kg CO2</text><text class="axis-label" x="'+L+'" y="'+(H-8)+'">'+SYMBOL+nf(xlo,2)+
'</text><text class="axis-label" x="8" y="'+(H-B)+'">'+nf(ylo,1)+'</text>'+dots;
function inspectorPoint(x){{return [x.cost,x.carbon,x.e.d.name+' · '+new Date(x.time).toLocaleString("en-GB",{{timeZone:"UTC"}})+' UTC']}}
svg.dataset.inspector=JSON.stringify({{kind:"scatter",xLabel:"Cost",xSuffix:" "+SYMBOL,
yLabel:"Carbon",ySuffix:" kg CO₂",precision:3,pointLabel:"Placement",
series:[{{name:"Feasible",color:"--blue",pointsOnly:true,radius:2.7,points:pts.filter(x=>!x.pareto&&x!==selected).map(inspectorPoint)}},
{{name:"Pareto frontier",color:"--green",pointsOnly:true,radius:4,points:pts.filter(x=>x.pareto&&x!==selected).map(inspectorPoint)}},
{{name:"Selected",color:"--orange",pointsOnly:true,radius:5.5,points:selected?[inspectorPoint(selected)]:[]}}]}})}}
function renderPlan(){{["costW","carbonW","delayW"].forEach(k=>document.getElementById(k+"V").textContent=S[k]);
var box=document.getElementById("result");if(S.costW+S.carbonW+S.delayW<=0){{LAST_PLAN=null;box.innerHTML='<div class="empty"><b>No objective selected.</b> Set at least one of the cost, carbon or delay weights above zero.</div>';
document.getElementById("rows").innerHTML="";drawChart([],null);return}}var plan=enumerate(),out=plan.options;if(!out.length){{box.innerHTML='<div class="empty"><b>No feasible plan.</b> Adjust the deadline, fleet size, memory mode, or objective signals.</div>';
LAST_PLAN=null;document.getElementById("rows").innerHTML="";drawChart([],null);return}}var x=out[0],m=M[S.model],d=x.e.d,ts=new Date(x.time),finish=x.finish;LAST_PLAN={{selected:x,options:out}};
box.innerHTML='<div class="tiles">'+tile("Hardware",S.count+'x '+d.name,d.vendor+' · '+d.prov)+tile("Start",ts.toLocaleString("en-GB",{{timeZone:"UTC",weekday:"short",hour:"2-digit",minute:"2-digit"}}),'UTC · half-hour placement')+
tile("Finish",finish.toLocaleString("en-GB",{{timeZone:"UTC",weekday:"short",hour:"2-digit",minute:"2-digit"}}),duration(x.e.hours)+' runtime')+
tile("Facility energy",nf(x.kwh,1)+' kWh',nf(x.e.itkw,1)+' kW IT · PUE '+nf(S.pue,2))+
tile("Cost",x.cost===null?'Not weighted':SYMBOL+nf(x.cost,2),'{html.escape(context.price_label)}')+
tile("Carbon",x.carbon===null?'Not weighted':nf(x.carbon,2)+' kg','{html.escape(context.carbon_label)}')+'</div><div class="trace">'+
'<div class="step"><label>Workload</label><b>'+m.name+'</b><span>'+S.task+' · '+S.precision+' · '+nf(S.tokens/1e9,2)+'B tokens</span></div>'+
'<div class="step"><label>Hardware search</label><b>'+S.count+'x '+d.name+'</b><span>'+out.filter(o=>o.e.key===x.e.key).length+' legal starts · '+nf(x.e.itkw,1)+' kW IT</span></div>'+
'<div class="step"><label>Facility constraint</label><b>{html.escape(context.location_name)}</b><span>{html.escape(context.market_name)} · {html.escape(context.price_label)}</span></div>'+
'<div class="step"><label>Time placement</label><b>'+ts.toLocaleString("en-GB",{{timeZone:"UTC",weekday:"short",hour:"2-digit",minute:"2-digit"}})+'</b><span>'+nf(x.delay,1)+' h delay · score '+nf(x.score,3)+'</span></div></div>'+
'<p class="why"><b>Why this plan:</b> lowest weighted score across '+nf(out.length,0)+' feasible device-window combinations. '+
plan.rejected.memory+' devices failed memory, '+plan.rejected.deadline+' exceeded the deadline, and '+plan.rejected.policy+' windows violated a hard policy cap. '+out.filter(o=>o.pareto).length+' options sit on the cost-carbon frontier.</p>';
document.getElementById("countNote").textContent=nf(out.length,0)+' feasible combinations · top 30 shown';
document.getElementById("rows").innerHTML=out.slice(0,30).map(function(o,i){{var t=new Date(o.time).toLocaleString("en-GB",{{timeZone:"UTC",weekday:"short",hour:"2-digit",minute:"2-digit"}});
return '<tr class="'+(i===0?'best':'')+'"><td>'+S.count+'x '+o.e.d.name+(o.pareto?' <span class="pill">Pareto</span>':'')+'</td><td>'+t+'</td><td>'+duration(o.e.hours)+'</td><td>'+nf(o.kwh,1)+' kWh</td><td>'+
(o.cost===null?'n/a':SYMBOL+nf(o.cost,2))+'</td><td>'+(o.carbon===null?'n/a':nf(o.carbon,2)+' kg')+'</td><td>'+nf(o.score,3)+'</td></tr>'}}).join('');drawChart(out,x);syncState()}}
function auditOption(o){{return {{hardware_key:o.e.key,hardware:o.e.d.name,start:o.time,
finish:o.finish.toISOString(),runtime_hours:o.e.hours,it_power_kw:o.e.itkw,pue:S.pue,
facility_energy_kwh:o.kwh,cost:o.cost,currency:{json.dumps(context.currency)},carbon_kg:o.carbon,
delay_hours:o.delay,score:o.score,pareto:o.pareto}}}}
function auditPlan(){{if(!LAST_PLAN)return null;return {{schema:"grid-aware-plan-v1",
generated_at:new Date().toISOString(),market:{json.dumps(context.market_key)},
location:{json.dumps(context.location_key)},signal_mode:{json.dumps(context.signal_mode)},
price_label:{json.dumps(context.price_label)},carbon_label:{json.dumps(context.carbon_label)},
provenance:{json.dumps(context.provenance)},configuration:Object.assign({{}},S),
selected:auditOption(LAST_PLAN.selected),feasible_count:LAST_PLAN.options.length}}}}
function download(name,content,type){{var url=URL.createObjectURL(new Blob([content],{{type:type}}));
var anchor=document.createElement("a");anchor.href=url;anchor.download=name;document.body.appendChild(anchor);
anchor.click();anchor.remove();setTimeout(function(){{URL.revokeObjectURL(url)}},0)}}
document.getElementById("exportPlan").addEventListener("click",function(){{var plan=auditPlan();
if(plan)download("placement-plan.json",JSON.stringify(plan,null,2),"application/json")}});
document.getElementById("exportCsv").addEventListener("click",function(){{if(!LAST_PLAN)return;
var head=["hardware","start","finish","runtime_hours","facility_energy_kwh","cost","currency","carbon_kg","delay_hours","score","pareto"],rows=[head.join(",")];
LAST_PLAN.options.forEach(function(option){{var row=auditOption(option);rows.push(head.map(function(key){{
var value=String(row[key]??"").replaceAll('"','""');return '"'+value+'"'}}).join(","))}});
download("placement-alternatives.csv",rows.join("\\n"),"text/csv")}});
document.getElementById("copyLink").addEventListener("click",function(){{var button=this;
navigator.clipboard.writeText(location.href).then(function(){{button.textContent="Copied";
setTimeout(function(){{button.textContent="Copy review link"}},1200)}})}});
document.getElementById("saveDecision").addEventListener("click",async function(){{
var status=document.getElementById("auditStatus");status.textContent="Saving canonical server plan...";
var payload={{market:{json.dumps(context.market_key)},location:{json.dumps(context.location_key)},workload:{{
model_key:S.model,task:S.task,precision:S.precision,tokens:S.tokens,accelerator_count:S.count,
pue:S.pue,system_efficiency:S.system/100,memory_mode:S.shard,
training_state_bytes_per_param:S.statebytes,activation_buffer_headroom:S.headroom/100,
context_length:S.context,batch_size:S.batch,kv_precision:S.kvprecision}},planning:{{
deadline_hours:S.deadline,cost_weight:S.costW,carbon_weight:S.carbonW,delay_weight:S.delayW,
max_cost:S.maxCost>0?S.maxCost:null,max_carbon_kg:S.maxCarbon>0?S.maxCarbon:null,
max_delay_hours:S.maxDelay>=0?S.maxDelay:null}}}};
try{{var response=await fetch("/api/v1/plan?market="+encodeURIComponent(payload.market)+"&location="+
encodeURIComponent(payload.location),{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(payload)}});
var result=await response.json();if(!response.ok)throw new Error(result.error||"Plan could not be saved");
status.textContent="Saved · ";var link=document.createElement("a");
link.href="/api/v1/decisions/"+encodeURIComponent(result.decision_id);link.textContent=result.decision_id;
status.appendChild(link);
}}catch(error){{status.textContent="Not saved: "+error.message}}}});
loadState();validateState();["model","precision","count","tokens","deadline","shard","statebytes","headroom","context","batch","kvprecision","pue","system","maxCost","maxCarbon","maxDelay"].forEach(function(id){{var el=document.getElementById(id);el.value=String(S[id]);el.addEventListener("change",function(){{S[id]=["model","precision","shard","kvprecision"].includes(id)?el.value:+el.value;renderPlan()}})}});
["costW","carbonW","delayW"].forEach(function(id){{var el=document.getElementById(id);el.value=S[id];el.addEventListener("input",function(){{S[id]=+el.value;renderPlan()}})}});
document.querySelectorAll("#task button").forEach(function(b){{b.classList.toggle("on",b.dataset.task===S.task);b.addEventListener("click",function(){{S.task=b.dataset.task;document.querySelectorAll("#task button").forEach(x=>x.classList.toggle("on",x===b));document.querySelectorAll(".training-only").forEach(x=>x.classList.toggle("hide",S.task!=="training"));document.querySelectorAll(".inference-only").forEach(x=>x.classList.toggle("hide",S.task!=="inference"));renderPlan()}})}});
document.querySelectorAll(".training-only").forEach(x=>x.classList.toggle("hide",S.task!=="training"));document.querySelectorAll(".inference-only").forEach(x=>x.classList.toggle("hide",S.task!=="inference"));
document.getElementById("market").addEventListener("change",function(){{var defaults={{GB:"national",CAISO:"sp15",NYISO:"nyc"}};goMarket(this.value,defaults[this.value]||"national")}});
document.getElementById("location").addEventListener("change",function(){{goMarket("{current_market}",this.value)}});
var loadNode=document.getElementById("loadNode");if(loadNode)loadNode.addEventListener("click",function(){{var n=document.getElementById("customNode").value.trim();if(n)goMarket("CAISO",n)}});
renderPlan();
{EXPAND_JS}
</script></body></html>"""
