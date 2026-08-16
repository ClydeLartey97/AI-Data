"""The page a company declares its site on — plants, contracts, connection.

This is the input side of `facility-energy-v1`. Everything here is a field the
operator already knows from a connection agreement, a PPA or a datasheet; the
software does the arithmetic. Deliberately plain: the visual pass comes later,
and a form that is honest about what it is asking for beats a styled one that
is vague about it.
"""
from __future__ import annotations

from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Site Declaration · Grid-Aware Scheduler</title>__THEME_BOOTSTRAP__
<style>
:root{color-scheme:light dark;--bg:#f5f5f7;--card:#fff;--text:#111114;--muted:#65656b;
--line:#dedee3;--blue:#0066d6;--green:#187a38;--amber:#9a5b00;--red:#c53030;
--text-2:var(--muted);--sep:var(--line);--shadow:0 1px 3px rgba(0,0,0,.08);
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Helvetica,Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-size:14px}
header{height:58px;padding:0 max(22px,calc((100% - 1280px)/2));display:flex;align-items:center;
justify-content:space-between;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--card) 90%,transparent);
position:sticky;top:0;z-index:4;backdrop-filter:blur(18px)}h1{font-size:17px;margin:0;letter-spacing:-.02em}
body>.theme-control~header{padding-right:max(190px,calc((100% - 1280px)/2))}
nav{display:flex;gap:4px}nav a{color:var(--muted);text-decoration:none;padding:7px 10px;border-radius:8px}
nav a:hover,nav a.on{color:var(--text);background:var(--bg)}
main{max-width:1280px;margin:auto;padding:34px 22px 60px}
.intro h2{font-size:34px;letter-spacing:-.04em;margin:0 0 6px}
.intro p{margin:0 0 22px;color:var(--muted);max-width:720px;line-height:1.45}
.eyebrow{color:var(--muted);font-size:12.5px;font-weight:600}
.split{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(320px,.6fr);gap:12px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:20px}
.card+.card{margin-top:12px}
h3{font-size:17px;margin:0 0 4px;letter-spacing:-.02em}
.hint{color:var(--muted);margin:0 0 16px;line-height:1.45}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)}
input,select{font:inherit;font-size:14px;color:var(--text);background:var(--bg);
border:1px solid var(--line);border-radius:9px;padding:9px 11px;width:100%}
button{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);
border-radius:9px;padding:9px 13px;cursor:pointer}button:hover{border-color:var(--blue)}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
.plant{border:1px solid var(--line);border-radius:12px;padding:15px;margin-bottom:10px;background:var(--bg)}
.plant-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;gap:10px}
.plant-top b{font-size:14px}
.actions{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}
.status{margin-top:14px;line-height:1.5}.status.error{color:var(--red)}.status.ok{color:var(--green)}
.note{color:var(--muted);font-size:12.5px;line-height:1.5;margin:12px 0 0}
.summary div{display:flex;justify-content:space-between;gap:12px;padding:9px 0;
border-bottom:1px solid var(--line)}.summary div:last-child{border-bottom:0}
.summary span{color:var(--muted)}.summary b{font-weight:600;text-align:right}
.warn{color:var(--amber);line-height:1.5;margin:10px 0 0}
@media(max-width:900px){.split{grid-template-columns:1fr}}
__THEME_CSS__
</style></head>
<body>__THEME_CONTROL__
<header><h1>AI Data Centre Operations</h1><nav><a href="/">Operations</a><a href="/simulator">Fleet Lab</a><a href="/planner">Placement Lab</a><a href="/grid">Sites &amp; Grid</a><a class="on" href="/site">Site</a><a href="/decisions">Decisions</a></nav></header>
<main>
<div class="intro"><div class="eyebrow">Energy declaration</div><h2>Declare your site</h2>
<p>Enter the site once. Everything here comes from a connection agreement, a power purchase agreement or a datasheet — the software does the arithmetic and keeps the source of every figure attached to it. Generation you own or take over a dedicated wire raises how much compute can run at the same time; a contractual instrument is recorded and never powers an accelerator.</p></div>
<div class="split">
<div>
<section class="card"><h3>Site</h3>
<p class="hint">Where the facility physically is. Coordinates drive the weather forecast used to predict what your plants will produce.</p>
<div class="fields">
<label>Site ID<input id="siteId" value="dc-1"></label>
<label>Name<input id="siteName" value="Main campus"></label>
<label>Latitude<input id="siteLat" type="number" step="0.0001" value="51.5074"></label>
<label>Longitude<input id="siteLon" type="number" step="0.0001" value="-0.1278"></label>
<label>Meter or connection ID<input id="siteMeter" placeholder="optional"></label>
<label>Time zone<input id="siteTz" value="Europe/London"></label>
<label>Market<select id="market"><option value="GB">GB</option><option value="CAISO">CAISO</option><option value="NYISO">NYISO</option><option value="MISO">MISO</option></select></label>
<label>Price / carbon location<input id="location" value="national"></label>
<label>Declared by<input id="declaredBy" value="Site engineering"></label>
</div></section>

<section class="card"><h3>Facility</h3>
<p class="hint">Base load is everything that runs regardless of AI workloads. The import limit is your grid connection; the electrical limit is your switchgear rating, and it is larger whenever you generate on site.</p>
<div class="fields">
<label>Base load · kW<input id="baseLoad" type="number" min="0" step="1" value="900"></label>
<label>Daytime PUE<input id="pue" type="number" min="1" max="5" step="0.01" value="1.24"></label>
<label>Night PUE<input id="nightPue" type="number" min="1" max="5" step="0.01" value="1.16"></label>
<label>Max grid import · kW<input id="maxImport" type="number" min="0" step="1" value="4000"></label>
<label>Electrical limit · kW<input id="electricalLimit" type="number" min="0" step="1" placeholder="derived if blank"></label>
<label>How is this known<select id="facilityEvidence"><option value="estimated">Estimated</option><option value="nameplate">Nameplate</option><option value="contracted">Contracted</option><option value="metered">Metered</option></select></label>
<label>Dispatch priority<select id="priority"><option value="renewable">Renewable first</option><option value="carbon_free">Carbon-free first</option><option value="carbon">Lowest carbon</option><option value="cost">Lowest cost</option></select></label>
</div>
<p class="note">Choosing <b>Metered</b> requires a meter or connection ID above. Claiming a meter reading without naming the meter is refused rather than accepted quietly.</p>
</section>

<section class="card"><h3>Generation plants</h3>
<p class="hint">A wind farm, a solar array, a generator, or a contract. Give a plant its own coordinates and the forecast is fetched there — a wind farm fifty kilometres away has its own wind.</p>
<div id="plants"></div>
<div class="actions"><button type="button" id="addPlant">Add a plant</button></div>
</section>

<div class="actions">
<button type="button" class="primary" id="save">Save declaration</button>
<button type="button" id="preview">Preview available power</button>
<button type="button" id="reload">Load saved</button>
</div>
<p class="status" id="status">Nothing declared yet.</p>
</div>

<aside class="card"><div class="eyebrow">What this produces</div><h3>Derived supply</h3>
<p class="hint">Saving the declaration lets the scheduler decide when to run work against your own generation, not only against the grid.</p>
<div class="summary" id="summary"><div><span>Status</span><b>Not declared</b></div></div>
<p class="note">The forecast is a physics conversion of a public weather forecast — irradiance and wind speed into output — using a generic panel and turbine response. Expect the shape to be right and the level optimistic. It is never presented as plant telemetry.</p>
<p class="warn" id="warnings" hidden></p>
</aside>
</div>
</main>
<script>
(function(){"use strict";
var plants=document.getElementById("plants"),status=document.getElementById("status");
var KINDS=["solar","wind","hydro","nuclear","geothermal","biomass","gas","coal","oil","other"];
var METHODS=[["weather","Forecast from its coordinates"],["diurnal","Modelled daily shape"],["flat","Constant"],["series","Supplied capacity factors"]];
var DELIVERY=[["onsite","On site"],["dedicated_wire","Dedicated wire"],["grid","Delivered over the grid"],["contractual","Contractual only (virtual PPA, certificates)"]];
var EVIDENCE=[["estimated","Estimated"],["nameplate","Nameplate"],["contracted","Contracted"],["metered","Metered"]];
function el(tag,text,cls){var n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n}
function field(label,input){var l=el("label",label);l.appendChild(input);return l}
function input(type,value,step){var i=document.createElement("input");i.type=type;if(step)i.step=step;if(value!=null)i.value=value;return i}
function select(options,value){var s=document.createElement("select");options.forEach(function(o){var opt=document.createElement("option");opt.value=Array.isArray(o)?o[0]:o;opt.textContent=Array.isArray(o)?o[1]:o;s.appendChild(opt)});if(value)s.value=value;return s}
function addPlant(data){data=data||{};
var box=el("div",null,"plant"),top=el("div",null,"plant-top"),name=input("text",data.name||"Rooftop array");
name.style.maxWidth="260px";var remove=el("button","Remove");remove.type="button";
remove.addEventListener("click",function(){box.remove()});
top.appendChild(name);top.appendChild(remove);box.appendChild(top);
var grid=el("div",null,"fields");
var refs={name:name,
 id:input("text",data.source_id||("plant-"+(plants.children.length+1))),
 kind:select(KINDS,data.kind||"solar"),
 capacity:input("number",data.capacity_kw!=null?data.capacity_kw:1000,"1"),
 method:select(METHODS,data.availability_method||"weather"),
 lat:input("number",data.latitude!=null?data.latitude:"",  "0.0001"),
 lon:input("number",data.longitude!=null?data.longitude:"","0.0001"),
 delivery:select(DELIVERY,data.delivery_type||"onsite"),
 evidence:select(EVIDENCE,data.evidence||"nameplate"),
 confidence:input("number",data.confidence!=null?data.confidence:0.85,"0.05"),
 cost:input("number",data.cost_per_mwh!=null?data.cost_per_mwh:0,"1"),
 carbon:input("number",data.carbon_g_per_kwh!=null?data.carbon_g_per_kwh:0,"1"),
 loss:input("number",data.delivery_loss_percent!=null?data.delivery_loss_percent:0,"0.1"),
 meter:input("text",data.grid_connection_id||"")};
grid.appendChild(field("Plant ID",refs.id));
grid.appendChild(field("Kind",refs.kind));
grid.appendChild(field("Capacity · kW",refs.capacity));
grid.appendChild(field("Availability from",refs.method));
grid.appendChild(field("Latitude",refs.lat));
grid.appendChild(field("Longitude",refs.lon));
grid.appendChild(field("Delivery",refs.delivery));
grid.appendChild(field("How is this known",refs.evidence));
grid.appendChild(field("Forecast confidence 0-1",refs.confidence));
grid.appendChild(field("Cost · per MWh",refs.cost));
grid.appendChild(field("Carbon · g/kWh",refs.carbon));
grid.appendChild(field("Delivery loss · %",refs.loss));
grid.appendChild(field("Meter or connection ID",refs.meter));
box.appendChild(grid);box.refs=refs;plants.appendChild(box)}
function number(node){var v=node.value.trim();return v===""?null:Number(v)}
function collect(){
 var sources=[];
 Array.prototype.forEach.call(plants.children,function(box){
  var r=box.refs,source={source_id:r.id.value.trim(),name:r.name.value.trim(),
   kind:r.kind.value,capacity_kw:number(r.capacity),availability_method:r.method.value,
   delivery_type:r.delivery.value,evidence:r.evidence.value,
   confidence:number(r.confidence),cost_per_mwh:number(r.cost),
   carbon_g_per_kwh:number(r.carbon),delivery_loss_percent:number(r.loss)};
  if(r.meter.value.trim())source.grid_connection_id=r.meter.value.trim();
  if(number(r.lat)!==null&&number(r.lon)!==null){source.latitude=number(r.lat);source.longitude=number(r.lon)}
  sources.push(source)});
 var facility={base_load_kw:number(document.getElementById("baseLoad")),
  pue:number(document.getElementById("pue")),
  night_pue:number(document.getElementById("nightPue")),
  max_import_kw:number(document.getElementById("maxImport")),
  evidence:document.getElementById("facilityEvidence").value};
 var limit=number(document.getElementById("electricalLimit"));
 if(limit!==null)facility.electrical_limit_kw=limit;
 var meter=document.getElementById("siteMeter").value.trim();
 var site={site_id:document.getElementById("siteId").value.trim(),
  name:document.getElementById("siteName").value.trim(),
  latitude:number(document.getElementById("siteLat")),
  longitude:number(document.getElementById("siteLon")),
  time_zone:document.getElementById("siteTz").value.trim()};
 if(meter)site.grid_connection_id=meter;
 return {version:"facility-energy-v1",
  declared_by:document.getElementById("declaredBy").value.trim(),
  declared_at:new Date().toISOString().slice(0,10),
  site:site,
  market:{market:document.getElementById("market").value,
          location:document.getElementById("location").value.trim()},
  facility:facility,sources:sources,
  dispatch_priority:document.getElementById("priority").value}}
function row(label,value){var d=el("div");d.appendChild(el("span",label));d.appendChild(el("b",value));return d}
function showProfile(profile,extra){
 var summary=document.getElementById("summary");summary.replaceChildren();
 summary.appendChild(row("Site",profile.site.name));
 summary.appendChild(row("Market",profile.market+" · "+profile.location));
 summary.appendChild(row("Plants declared",profile.sources.length));
 summary.appendChild(row("Physically delivered",profile.sources.filter(function(s){return s.physical}).length));
 summary.appendChild(row("Grid import limit",(profile.facility.max_import_kw||0)+" kW"));
 summary.appendChild(row("Site electrical limit",Math.round(profile.facility.electrical_limit_kw)+" kW"+(profile.facility.electrical_limit_declared?"":" (derived)")));
 if(extra&&extra.peak!=null){summary.appendChild(row("Peak available power",Math.round(extra.peak)+" kW"));
  summary.appendChild(row("Lowest available power",Math.round(extra.low)+" kW"))}
 var warn=document.getElementById("warnings"),notes=(profile.warnings||[]).concat((extra&&extra.warnings)||[]);
 warn.hidden=notes.length===0;warn.textContent=notes.join(" · ")}
async function send(){
 status.className="status";status.textContent="Validating declaration…";
 var response=await fetch("/api/v1/site-profile",{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify(collect())});
 var payload=await response.json();
 if(!response.ok){status.className="status error";status.textContent=payload.error||"The declaration was refused.";return null}
 status.className="status ok";status.textContent="Declaration saved. The scheduler will plan against this site.";
 showProfile(payload.profile);return payload.profile}
async function load(){
 var response=await fetch("/api/v1/site-profile");var payload=await response.json();
 if(!payload.configured){status.className="status";status.textContent="No declaration saved yet.";return}
 var p=payload.profile;
 document.getElementById("siteId").value=p.site.site_id;
 document.getElementById("siteName").value=p.site.name;
 document.getElementById("siteLat").value=p.site.latitude;
 document.getElementById("siteLon").value=p.site.longitude;
 document.getElementById("siteMeter").value=p.site.grid_connection_id||"";
 document.getElementById("siteTz").value=p.site.time_zone;
 document.getElementById("market").value=p.market;
 document.getElementById("location").value=p.location;
 document.getElementById("declaredBy").value=p.declared_by;
 document.getElementById("baseLoad").value=p.facility.base_load_kw;
 document.getElementById("pue").value=p.facility.pue;
 if(p.facility.night_pue!=null)document.getElementById("nightPue").value=p.facility.night_pue;
 if(p.facility.max_import_kw!=null)document.getElementById("maxImport").value=p.facility.max_import_kw;
 if(p.facility.electrical_limit_declared)document.getElementById("electricalLimit").value=p.facility.electrical_limit_kw;
 document.getElementById("facilityEvidence").value=p.facility.evidence;
 document.getElementById("priority").value=p.dispatch_priority;
 plants.replaceChildren();p.sources.forEach(addPlant);
 status.className="status";status.textContent="Loaded the saved declaration.";
 showProfile(p)}
async function preview(){
 var profile=await send();if(!profile)return;
 status.textContent="Saved. Fetching the forecast for each plant…";
 var response=await fetch("/api/v1/site-supply");var payload=await response.json();
 if(!response.ok){status.className="status error";status.textContent=payload.error||"Supply preview failed.";return}
 showProfile(profile,{peak:payload.peak_available_kw,low:payload.lowest_available_kw,
  warnings:payload.warnings});
 status.className="status ok";
 status.textContent="Forecast applied. Peak available power "+Math.round(payload.peak_available_kw)+" kW against an import limit of "+(profile.facility.max_import_kw||0)+" kW.";}
document.getElementById("addPlant").addEventListener("click",function(){addPlant()});
document.getElementById("save").addEventListener("click",send);
document.getElementById("preview").addEventListener("click",preview);
document.getElementById("reload").addEventListener("click",load);
addPlant({name:"Rooftop array",kind:"solar",capacity_kw:1800,latitude:51.509,longitude:-0.13});
load();})();
</script></body></html>"""


def render() -> str:
    """Return the self-contained declaration page."""
    page = _PAGE.replace("__THEME_BOOTSTRAP__", THEME_BOOTSTRAP)
    page = page.replace("__THEME_CSS__", THEME_CSS)
    if "__THEME_CONTROL__" not in page:
        raise AssertionError("theme control anchor missing from site page")
    return page.replace("__THEME_CONTROL__", THEME_CONTROL)
