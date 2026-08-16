"""Operator-facing audit journal for persisted scheduling decisions."""
from __future__ import annotations

from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS


def render() -> str:
    """Return a self-contained page backed by the versioned decision API."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decision Journal · Grid-Aware Scheduler</title>""" + THEME_BOOTSTRAP + """
<style>
:root{color-scheme:light dark;--bg:#f5f5f7;--card:#fff;--text:#111114;--muted:#65656b;
--line:#dedee3;--blue:#0066d6;--green:#187a38;--amber:#9a5b00;--red:#c53030;
--text-2:var(--muted);--sep:var(--line);--shadow:0 1px 3px rgba(0,0,0,.08);
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Helvetica,Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-size:14px}
header{height:58px;padding:0 max(22px,calc((100% - 1280px)/2));display:flex;align-items:center;
justify-content:space-between;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--card) 90%,transparent);
position:sticky;top:0;z-index:4;backdrop-filter:blur(18px)}h1{font-size:17px;margin:0;letter-spacing:-.02em}
body>.theme-control+header{padding-right:max(190px,calc((100% - 1280px)/2))}
nav{display:flex;gap:4px}nav a{color:var(--muted);text-decoration:none;padding:7px 10px;border-radius:8px}
nav a:hover,nav a.on{color:var(--text);background:var(--bg)}main{max-width:1280px;margin:auto;padding:34px 22px 60px}
.intro{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:22px}.intro h2{font-size:34px;
letter-spacing:-.04em;margin:0 0 6px}.intro p{margin:0;color:var(--muted);max-width:670px;line-height:1.45}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr));gap:10px;min-width:360px}
.metric,.card{background:var(--card);border:1px solid var(--line);border-radius:15px}.metric{padding:14px 16px}
.metric b{display:block;font-size:24px;letter-spacing:-.035em}.metric span{color:var(--muted);font-size:11px;
letter-spacing:-0.005em}.eyebrow{color:var(--muted);font-size:12.5px;font-weight:600;letter-spacing:-0.005em}
.toolbar{display:flex;gap:10px;padding:13px;margin-bottom:12px}
.pane{padding:20px}.pane h3{font-size:22px;letter-spacing:-.025em;margin:4px 0 2px}.pane .sub{color:var(--muted)}
pre.report{margin:14px 0 0;padding:14px;background:var(--bg);border:1px solid var(--line);border-radius:11px;
overflow-x:auto;white-space:pre;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
input,select,button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
input{flex:1;min-width:180px}button{cursor:pointer;background:var(--card)}button:hover{border-color:var(--blue)}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:900px}th,td{text-align:left;padding:12px 13px;
border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-size:11px;text-transform:uppercase;
letter-spacing:.05em}tbody tr{cursor:pointer}tbody tr:hover,tbody tr.active{background:color-mix(in srgb,var(--blue) 7%,transparent)}
.status{display:inline-flex;align-items:center;gap:6px}.dot{width:7px;height:7px;border-radius:50%;background:var(--amber)}
.status.scored .dot{background:var(--green)}.empty{padding:42px;text-align:center;color:var(--muted)}
.split{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(360px,.7fr);gap:12px}.detail{padding:20px;position:sticky;top:72px;
max-height:calc(100vh - 92px);overflow:auto}.detail h3{font-size:22px;letter-spacing:-.025em;margin:4px 0 2px}.detail .sub{color:var(--muted);margin-bottom:18px}
.selected{padding:15px;border:1px solid color-mix(in srgb,var(--green) 45%,var(--line));border-radius:12px;background:color-mix(in srgb,var(--green) 5%,var(--card))}
.selected h4{margin:2px 0 12px;font-size:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.datum span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.datum b{font-weight:600}
.section{margin-top:20px}.section h4{font-size:13px;font-weight:650;letter-spacing:-0.005em;color:var(--text);margin:0 0 10px}
.trace{margin:0;padding-left:18px;line-height:1.6}.actions{display:flex;gap:8px;margin-top:18px}.actions button{flex:1}.error{color:var(--red)}
@media(max-width:900px){.intro{align-items:stretch;flex-direction:column}.metrics{min-width:0}.split{grid-template-columns:1fr}.detail{position:static;max-height:none}}
@media(max-width:560px){header{height:auto;padding:12px 58px 12px 16px;align-items:flex-start;gap:8px;flex-direction:column}body>.theme-control+header{padding-right:58px}nav{width:100%;overflow:auto}.intro h2{font-size:29px}.metrics{grid-template-columns:1fr}.toolbar{flex-wrap:wrap}.grid{grid-template-columns:1fr}}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#000;--card:#1c1c1e;--text:#f5f5f7;--muted:#a1a1aa;--line:#343438;--blue:#0a84ff;--green:#32a852;--amber:#ff9f0a;--shadow:none}}
:root[data-theme="dark"]{--bg:#000;--card:#1c1c1e;--text:#f5f5f7;--muted:#a1a1aa;--line:#343438;--blue:#0a84ff;--green:#32a852;--amber:#ff9f0a;--shadow:none}
""" + THEME_CSS + """
</style></head><body>""" + THEME_CONTROL + """
<header><h1>AI Data Centre Operations</h1><nav><a href="/">Operations</a><a href="/simulator">Fleet Lab</a><a href="/planner">Placement Lab</a><a href="/grid">Sites &amp; Grid</a><a class="on" href="/decisions">Decisions</a></nav></header>
<main><div class="intro"><div><div class="eyebrow">Audit and evidence</div><h2>Decision journal</h2><p>Every saved recommendation is immutable. Open a row to inspect its constraints, selected placement, signal provenance and realised score.</p></div>
<div class="metrics"><div class="metric"><b id="total">0</b><span>Saved</span></div><div class="metric"><b id="scored">0</b><span>Scored</span></div><div class="metric"><b id="pending">0</b><span>Awaiting outturn</span></div></div></div>
<div class="toolbar card"><input id="search" type="search" placeholder="Search ID, model, hardware or location" aria-label="Search decisions"><select id="status" aria-label="Filter by status"><option value="all">All evidence states</option><option value="scored">Scored</option><option value="awaiting_outturn">Awaiting outturn</option></select><button id="refresh" type="button">Refresh</button><button id="report" type="button">Pilot report</button></div>
<div class="split"><div class="col"><section class="card table-wrap" id="journal"><table><thead><tr><th>Created</th><th>Market</th><th>Workload</th><th>Placement</th><th>Scheduled</th><th>Forecast cost</th><th>Forecast carbon</th><th>Evidence</th></tr></thead><tbody id="rows"></tbody></table><div class="empty" id="empty">Loading decisions…</div></section>
<section class="card pane" id="reportPane" hidden></section></div>
<aside class="card detail" id="detail"><div class="eyebrow">Decision detail</div><h3>Select a decision</h3><p class="sub">The complete record opens here without modifying the original recommendation.</p></aside></div></main>
<script>
(function(){"use strict";
var decisions=[],active=null;var rows=document.getElementById("rows"),empty=document.getElementById("empty"),detail=document.getElementById("detail");
function node(tag,text,cls){var el=document.createElement(tag);if(text!==undefined&&text!==null)el.textContent=String(text);if(cls)el.className=cls;return el}
function money(value,currency){if(value===null||value===undefined)return "Unavailable";try{return new Intl.NumberFormat("en-GB",{style:"currency",currency:currency||"GBP",maximumFractionDigits:2}).format(value)}catch(e){return Number(value).toFixed(2)+" "+(currency||"")}}
function when(value){if(!value)return "Unavailable";var d=new Date(value);return isNaN(d)?value:new Intl.DateTimeFormat("en-GB",{dateStyle:"medium",timeStyle:"short"}).format(d)}
function statusLabel(value){return value==="scored"?"Scored":"Awaiting outturn"}
function addCell(tr,text){tr.appendChild(node("td",text))}
function renderRows(){var q=document.getElementById("search").value.trim().toLowerCase(),status=document.getElementById("status").value;
rows.replaceChildren();var filtered=decisions.filter(function(d){var hay=[d.id,d.market,d.location,d.model_key,d.hardware,d.task].join(" ").toLowerCase();return(!q||hay.includes(q))&&(status==="all"||d.status===status)});
filtered.forEach(function(d){var tr=node("tr");tr.tabIndex=0;tr.dataset.id=d.id;if(d.id===active)tr.className="active";addCell(tr,when(d.created_at));addCell(tr,d.market+" · "+d.location);addCell(tr,(d.model_key||"Unknown")+" · "+(d.task||"task"));addCell(tr,d.hardware||"Unavailable");addCell(tr,when(d.start));addCell(tr,money(d.cost,d.currency));addCell(tr,d.carbon_kg===null||d.carbon_kg===undefined?"Unavailable":Number(d.carbon_kg).toFixed(3)+" kg");var td=node("td"),s=node("span",null,"status "+d.status);s.appendChild(node("i",null,"dot"));s.appendChild(node("span",statusLabel(d.status)));td.appendChild(s);tr.appendChild(td);
function open(){openDecision(d.id)}tr.addEventListener("click",open);tr.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();open()}});rows.appendChild(tr)});
empty.hidden=filtered.length>0;empty.textContent=decisions.length?"No decisions match this filter.":"No audited decisions yet. Save one from Planner."}
function datum(label,value){var box=node("div",null,"datum");box.appendChild(node("span",label));box.appendChild(node("b",value));return box}
function section(title){var box=node("div",null,"section");box.appendChild(node("h4",title));return box}
async function openDecision(id){active=id;renderRows();detail.replaceChildren(node("p","Loading decision…","sub"));try{var response=await fetch("/api/v1/decisions/"+encodeURIComponent(id));if(!response.ok)throw new Error("Decision could not be loaded");var payload=await response.json(),d=payload.decision,r=d.response||{},selected=r.selected||{},request=d.request||{},workload=request.workload||{},planning=request.planning||{};detail.replaceChildren();detail.appendChild(node("div","Audited decision","eyebrow"));detail.appendChild(node("h3",selected.hardware||"Placement"));detail.appendChild(node("p",d.id,"sub"));
var pick=node("div",null,"selected");pick.appendChild(node("div","Selected placement","eyebrow"));pick.appendChild(node("h4",(r.market?r.market.name+" · ":d.market+" · ")+(selected.location||d.location)));var g=node("div",null,"grid");g.appendChild(datum("Start",when(selected.start)));g.appendChild(datum("Finish",when(selected.finish)));g.appendChild(datum("Forecast cost",money(selected.cost,selected.currency)));g.appendChild(datum("Forecast carbon",selected.carbon_kg==null?"Unavailable":Number(selected.carbon_kg).toFixed(3)+" kg"));g.appendChild(datum("Facility energy",selected.facility_energy_kwh==null?"Unavailable":Number(selected.facility_energy_kwh).toFixed(2)+" kWh"));g.appendChild(datum("Pareto frontier",selected.pareto?"Yes":"No"));pick.appendChild(g);detail.appendChild(pick);
var inp=section("Constraints and workload"),ig=node("div",null,"grid");ig.appendChild(datum("Model",workload.model_key||"Unavailable"));ig.appendChild(datum("Task",workload.task||"Unavailable"));ig.appendChild(datum("Accelerators",workload.accelerator_count||"Unavailable"));ig.appendChild(datum("Deadline",planning.deadline_hours==null?"Unavailable":planning.deadline_hours+" hours"));ig.appendChild(datum("Cost weight",planning.cost_weight==null?"0":planning.cost_weight));ig.appendChild(datum("Carbon weight",planning.carbon_weight==null?"0":planning.carbon_weight));inp.appendChild(ig);detail.appendChild(inp);
var prov=section("Evidence boundary"),pg=node("div",null,"grid");pg.appendChild(datum("Signal mode",d.signal_mode));pg.appendChild(datum("Signal points",(d.signals||[]).length));pg.appendChild(datum("Algorithm",r.algorithm||"Unavailable"));pg.appendChild(datum("Feasible placements",r.feasible_count==null?"Unavailable":r.feasible_count));prov.appendChild(pg);detail.appendChild(prov);
var scored=section("Realised outcome");if(d.score){var sr=d.score.result||{},sg=node("div",null,"grid");sg.appendChild(datum("Scored",when(d.score.scored_at)));sg.appendChild(datum("Realised cost saved",money(sr.cost_saved,selected.currency)));sg.appendChild(datum("Carbon saved",sr.carbon_saved_kg==null?"Unavailable":Number(sr.carbon_saved_kg).toFixed(3)+" kg"));sg.appendChild(datum("Cost regret",sr.cost_regret==null?"Unavailable":money(sr.cost_regret,selected.currency)));scored.appendChild(sg)}else{scored.appendChild(node("p","Awaiting a realised price and carbon series. The forecast decision remains unchanged.","sub"))}detail.appendChild(scored);
var actions=node("div",null,"actions"),copy=node("button","Copy decision ID"),download=node("button","Download JSON");copy.addEventListener("click",function(){navigator.clipboard.writeText(d.id);copy.textContent="Copied"});download.addEventListener("click",function(){var blob=new Blob([JSON.stringify(d,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="decision-"+d.id+".json";a.click();URL.revokeObjectURL(url)});actions.appendChild(copy);actions.appendChild(download);detail.appendChild(actions)}catch(error){detail.replaceChildren(node("p",error.message,"error"))}}
async function load(){empty.hidden=false;empty.textContent="Loading decisions…";try{var response=await fetch("/api/v1/decisions?limit=200");if(!response.ok)throw new Error("Decision journal could not be loaded");var payload=await response.json();decisions=payload.decisions||[];document.getElementById("total").textContent=decisions.length;var scored=decisions.filter(function(d){return d.status==="scored"}).length;document.getElementById("scored").textContent=scored;document.getElementById("pending").textContent=decisions.length-scored;renderRows();if(decisions.length)openDecision(decisions[0].id)}catch(error){empty.hidden=false;empty.textContent=error.message;empty.className="empty error"}}
var journal=document.getElementById("journal"),reportPane=document.getElementById("reportPane");
function closeReport(){reportPane.hidden=true;journal.hidden=false}
async function showReport(){journal.hidden=true;reportPane.hidden=false;reportPane.replaceChildren(node("p","Building pilot report…","sub"));try{var response=await fetch("/api/v1/pilot-report?format=text");if(!response.ok)throw new Error("Pilot report could not be built");var text=await response.text();reportPane.replaceChildren();reportPane.appendChild(node("div","Shadow-mode pilot","eyebrow"));reportPane.appendChild(node("h3","Pilot report"));reportPane.appendChild(node("p","Aggregated from scored decisions only. Recommendations were recorded, never executed.","sub"));reportPane.appendChild(node("pre",text,"report"));
var actions=node("div",null,"actions"),back=node("button","Back to journal"),copy=node("button","Copy report"),download=node("button","Download text");back.addEventListener("click",closeReport);copy.addEventListener("click",function(){navigator.clipboard.writeText(text);copy.textContent="Copied"});download.addEventListener("click",function(){var blob=new Blob([text],{type:"text/plain"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="pilot-report.txt";a.click();URL.revokeObjectURL(url)});actions.appendChild(back);actions.appendChild(copy);actions.appendChild(download);reportPane.appendChild(actions)}catch(error){reportPane.replaceChildren(node("p",error.message,"error"),(function(){var b=node("button","Back to journal");b.addEventListener("click",closeReport);return b})())}}
document.getElementById("search").addEventListener("input",renderRows);document.getElementById("status").addEventListener("change",renderRows);document.getElementById("refresh").addEventListener("click",load);document.getElementById("report").addEventListener("click",showReport);load()})();
</script></body></html>"""
