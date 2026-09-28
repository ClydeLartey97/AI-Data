"""The site's hardware: what is connected, what it reads live, what is measured.

These sections used to sit on the Overview, which meant the facts about a site
were split across two pages. They live on Site setup now, beside the rest of
what the operator declares once. The markup, script and styles are kept
together here so the page that shows them only has to include three strings.
"""
from __future__ import annotations

FLEET_CSS = r"""
.fleet{margin-top:12px}.fleet .queue-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}
.fleet .queue-head h2{font-size:17px;margin:2px 0 4px;letter-spacing:-.02em}.fleet .actions{display:flex;gap:8px;flex-wrap:wrap;margin:0}
.fleet table{width:100%;border-collapse:collapse;min-width:950px}.fleet th,.fleet td{text-align:left;padding:10px 11px;border-bottom:1px solid var(--sep);white-space:nowrap}
.fleet th{color:var(--text-2);font-size:10px;text-transform:uppercase;letter-spacing:.055em}.fleet tbody tr:last-child td{border-bottom:0}
.fleet .status{color:var(--text-2);font-size:12px;margin:12px 0 0}.fleet .scope-note{margin:12px 0 0;color:var(--text-2);font-size:11px}
""" + r""".ops-grid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:1px;background:var(--sep);
border-radius:14px;overflow:hidden;margin:18px 0}
.ops-stat{background:var(--card);padding:15px 16px}
.ops-stat span{display:block;color:var(--text-2);font-size:11.5px;letter-spacing:-0.005em}
.ops-stat b{display:block;font-size:22px;letter-spacing:-.025em;margin-top:4px}
.table-wrap{overflow:auto;border:1px solid var(--sep);border-radius:13px}
.hero-devices{display:flex;flex-wrap:wrap;gap:12px}
.hero-device{flex:1 1 250px;max-width:400px;background:var(--card);border:1px solid var(--sep);border-radius:13px;padding:14px 15px}
.hero-device .dev-name{font-size:14px;font-weight:650;letter-spacing:-0.01em;margin-bottom:1px}
.hero-device .dev-kind{font-size:10px;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:11px}
.hero-device dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;margin:0;font-size:11.5px;align-items:baseline}
.hero-device dt{color:var(--text-3);white-space:nowrap}
.hero-device dd{margin:0;font-variant-numeric:tabular-nums;min-width:0}
.hero-skeleton{flex:1 1 250px;max-width:400px;height:118px;border-radius:13px;border:1px solid var(--sep);background:linear-gradient(90deg,color-mix(in srgb,var(--text-3) 7%,transparent),color-mix(in srgb,var(--text-3) 13%,transparent),color-mix(in srgb,var(--text-3) 7%,transparent));background-size:200% 100%;animation:heroShimmer 1.3s ease-in-out infinite}
@keyframes heroShimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.hero-skeleton{animation:none}}
.hero-note{margin:18px 0 0;font-size:11px;color:var(--text-3);line-height:1.6}
.hero-note b{color:var(--text-2);font-weight:640}
.scan-panel{margin:16px 0 4px;border:1px solid var(--sep);border-radius:12px;padding:15px 16px;background:color-mix(in srgb,var(--blue) 6%,transparent)}
.scan-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.scan-head b{font-size:13px}
.scan-head span{font-size:11px;color:var(--text-3)}
.scan-grid{display:flex;flex-wrap:wrap;gap:11px}
.scan-card{flex:1 1 300px;max-width:460px;border:1px solid var(--sep);border-radius:10px;padding:12px 13px;background:var(--card)}
.scan-card h4{margin:0 0 2px;font-size:13px;font-weight:650}
.scan-card .sub{font-size:10px;color:var(--text-3);margin-bottom:9px}
.scan-row{display:flex;gap:8px;align-items:baseline;font-size:11px;margin-bottom:5px;line-height:1.45}
.scan-row .k{color:var(--text-3);flex:none;width:74px}
.scan-row .v{color:var(--text);font-variant-numeric:tabular-nums;min-width:0}
.tag{display:inline-block;padding:1px 6px;border-radius:999px;font-size:9px;font-weight:700;letter-spacing:.04em;margin-left:5px;vertical-align:1px}
.tag-measured{background:color-mix(in srgb,var(--green) 16%,transparent);color:var(--green)}
.tag-published{background:color-mix(in srgb,var(--blue) 16%,transparent);color:var(--blue)}
.tag-spec,.tag-estimated{background:color-mix(in srgb,var(--orange) 14%,transparent);color:var(--orange)}
.tag-unavailable{background:color-mix(in srgb,var(--text-3) 16%,transparent);color:var(--text-3)}
.live-head{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:18px 0 12px}
.live-head h3{margin:0;font-size:14px;font-weight:650}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--text-3);flex:none}
.live-dot[data-state="live"]{background:var(--green);animation:livePulse 2s ease-in-out infinite}
.live-dot[data-state="error"]{background:var(--orange);animation:none}
@keyframes livePulse{0%,100%{opacity:1}50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.live-dot[data-state="live"]{animation:none}}
.live-state{font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
.live-note{font-size:11px;color:var(--text-3);flex:1 1 260px;min-width:0}
.live-grid{display:flex;flex-wrap:wrap;gap:12px}
.live-card{flex:1 1 280px;max-width:420px;border:1px solid var(--sep);border-radius:10px;padding:14px 15px;background:color-mix(in srgb,var(--text-3) 7%,transparent)}
.live-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:3px}
.live-card b{font-size:13px}
.live-card .kind{font-size:10px;color:var(--text-3)}
.live-card .spec{font-size:11px;color:var(--text-3);margin-bottom:11px}
.meter{margin-bottom:10px}
.meter-top{display:flex;justify-content:space-between;gap:8px;font-size:11px;margin-bottom:4px}
.meter-top span{color:var(--text-2)}
.meter-top b{font-weight:600;font-variant-numeric:tabular-nums}
.meter-track{height:5px;border-radius:3px;background:color-mix(in srgb,var(--text-3) 22%,transparent);overflow:hidden}
.meter-fill{height:100%;border-radius:3px;background:var(--blue);transition:width .45s ease}
.meter-fill[data-load="warn"]{background:var(--orange)}
.meter-fill[data-load="high"]{background:var(--red)}
.live-extra{display:flex;flex-wrap:wrap;gap:5px 14px;font-size:11px;color:var(--text-3);font-variant-numeric:tabular-nums}
.evidence-registry table{min-width:1050px}
.evidence-state{display:inline-flex;padding:3px 7px;border-radius:999px;background:color-mix(in srgb,var(--green) 10%,transparent);color:var(--green);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.evidence-empty{padding:22px;color:var(--text-2);text-align:center}
.evidence-caution{color:var(--orange)}
.collector-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:12px}
.collector-card{border:1px solid var(--sep);border-radius:12px;padding:13px}
.collector-card b{display:block;margin:4px 0}
.collector-card p{margin:0;color:var(--text-2);font-size:11px}
.collector-card code{font-size:10px;color:var(--text-2)}"""

FLEET_HTML = r"""<section class="card fleet" id="hardware"><div class="queue-head"><div><span class="eyebrow">This machine</span><h2 id="heroTitle">Reading this machine…</h2><p class="note" id="heroSub">Identifying every reachable device and what it can do.</p></div></div>
<div class="hero-devices" id="heroDevices"></div>
<p class="hero-note">Every figure carries the source it came from. <b>MEASURED</b> was reproduced on this hardware, <b>PUBLISHED</b> was measured by an audited third party, <b>SPEC</b> and <b>ESTIMATED</b> are not measurements at all.</p></section>
<section class="card fleet evidence-registry" id="inventoryCard"><div class="queue-head"><div><span class="eyebrow">Facility discovery</span><h2>Connected hardware</h2><p class="note">See the devices this installation can reach and which readings are measured. Performance becomes measured only after calibration.</p></div><div class="actions"><button id="runScan" class="primary" type="button">Scan this machine</button><button id="refreshInventory" type="button">Refresh inventory</button></div></div>
<div class="scan-panel" id="scanPanel" hidden><div class="scan-head"><b id="scanTitle">Scan complete</b><span id="scanSources"></span></div><div class="scan-grid" id="scanDevices"></div></div>
<div class="live-head"><h3>This host, live</h3><span class="live-dot" id="liveDot" data-state="connecting"></span><span class="live-state" id="liveState">Connecting</span><span class="live-note">Free capacity is the feasibility input: installed memory decides what could fit, available memory decides what fits now.</span></div>
<div class="live-grid" id="liveDevices"><p class="evidence-empty">Waiting for the first reading.</p></div>
<div class="ops-grid"><div class="ops-stat"><span>Snapshots recorded</span><b id="iSnapshotCount">0</b></div><div class="ops-stat"><span>Devices in latest</span><b id="iDeviceCount">0</b></div><div class="ops-stat"><span>Warnings in latest</span><b id="iWarningCount">0</b></div><div class="ops-stat"><span>Access tier</span><b>Read-only GET</b></div><div class="ops-stat"><span>Discovery state</span><b id="iState">Loading</b></div></div>
<div class="table-wrap"><table><thead><tr><th>Device</th><th>Identity</th><th>Memory</th><th>Live power</th><th>Power scope</th><th>Performance</th><th>Source</th><th>Fleet ID</th></tr></thead><tbody id="inventoryRows"></tbody></table><p class="evidence-empty" id="inventoryEmpty">No discovery snapshot yet.</p></div>
<p class="status" id="inventoryStatus">Discovery reads declared endpoints only; it never scans, never writes and never stores a raw serial number.</p></section>
<section class="card fleet evidence-registry" id="evidenceRegistry"><div class="queue-head"><div><span class="eyebrow">Measured workload evidence</span><h2>Measured workload profiles</h2><p class="note">Three or more matching runs create a trusted profile. Selecting one replaces editable runtime, power, model and quality values with the measurement.</p></div><div class="actions"><button id="runProbe" type="button">Verify local MLX</button><button id="refreshEvidence" type="button">Refresh evidence</button></div></div>
<div class="ops-grid"><div class="ops-stat"><span>Immutable observations</span><b id="eObservationCount">0</b></div><div class="ops-stat"><span>Ready profiles</span><b id="eProfileCount">0</b></div><div class="ops-stat"><span>Pending fingerprints</span><b id="ePendingCount">0</b></div><div class="ops-stat"><span>Minimum repeats</span><b>3</b></div><div class="ops-stat"><span>Registry state</span><b id="eRegistryState">Loading</b></div></div>
<div class="table-wrap"><table><thead><tr><th>Profile</th><th>Workload</th><th>Model</th><th>Device</th><th>Samples</th><th>Work rate</th><th>Average power</th><th>Energy method</th><th>Variation</th><th>Comparison scope</th></tr></thead><tbody id="evidenceProfiles"></tbody></table><p class="evidence-empty" id="evidenceEmpty">No schedulable measured profile yet. Run the benchmark collector three times with one exact fingerprint and a valid energy measurement.</p></div>
<div class="collector-list" id="evidenceCollectors"><div class="collector-card"><span class="evidence-state">Loading</span><b>Reference workload registry</b><p>Checking locally installed collectors.</p></div></div>
<p class="status" id="eProbeStatus">Performance probe not run in this session. A probe never creates energy evidence.</p>
<p class="scope-note">Apple subsystem estimates may optimise configurations on the same device only. Cross-device energy ranking requires a calibrated external meter. Runtime or Energy Impact alone never becomes watt-hours.</p></section>"""

FLEET_JS = r"""(function(){"use strict";var EVIDENCE_PROFILES={};
function addCells(row,values){values.forEach(function(text){var cell=document.createElement("td");cell.textContent=text;row.appendChild(cell)})}
function renderEvidenceRegistry(payload){var summary=payload.summary||{},profiles=payload.profiles||[],body=document.getElementById("evidenceProfiles"),collectors=document.getElementById("evidenceCollectors");EVIDENCE_PROFILES={};profiles.forEach(function(profile){EVIDENCE_PROFILES[profile.profile_id]=profile});document.getElementById("eObservationCount").textContent=Number(summary.observation_count||0);document.getElementById("eProfileCount").textContent=Number(summary.profile_count||0);document.getElementById("ePendingCount").textContent=Number(summary.pending_fingerprint_count||0);document.getElementById("eRegistryState").textContent=profiles.length?"Ready":"Awaiting runs";body.replaceChildren();profiles.forEach(function(profile){var row=document.createElement("tr"),variation="Throughput ±"+(Number(profile.throughput_relative_mad)*100).toFixed(1)+"% · energy ±"+(Number(profile.energy_relative_mad)*100).toFixed(1)+"%";addCells(row,[profile.profile_id,profile.workload_class+" · "+profile.run_mode,profile.model_id+" · "+profile.model_version+" · "+profile.precision,profile.device_key+" · "+profile.compute_unit,String(profile.sample_count),Number(profile.work_rate_per_second).toFixed(2)+" "+profile.work_unit+"/s",Number(profile.average_it_power_watts).toFixed(2)+" W",profile.energy_method+" · "+profile.energy_scope,variation,profile.cross_device_comparable?"Cross-device":"Same device only"]);body.appendChild(row)});collectors.replaceChildren();(payload.collectors||[]).forEach(function(item){var card=document.createElement("div"),state=document.createElement("span"),name=document.createElement("b"),detail=document.createElement("p"),version=document.createElement("code");card.className="collector-card";state.className="evidence-state";state.textContent=item.status==="runner_ready"?"Runner ready":item.status;name.textContent=item.name;detail.textContent=item.item_count+" public evaluation items · "+item.quality_metric+" · requires pinned model, valid energy and three repeats.";version.textContent=item.evaluation_suite_version;card.append(state,name,detail,version);collectors.appendChild(card)});document.getElementById("evidenceEmpty").hidden=profiles.length>0}
async function loadEvidenceProfiles(){document.getElementById("eRegistryState").textContent="Loading";try{var response=await fetch("/api/v1/evidence/profiles",{cache:"no-store"}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Evidence registry unavailable");renderEvidenceRegistry(payload)}catch(error){document.getElementById("eRegistryState").textContent="Unavailable";document.getElementById("evidenceEmpty").hidden=false;document.getElementById("evidenceEmpty").textContent=error.message}}
async function runEvidenceProbe(){var button=document.getElementById("runProbe"),status=document.getElementById("eProbeStatus");button.disabled=true;status.textContent="Running a short local MLX performance probe…";try{var response=await fetch("/api/v1/evidence/probe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({matrix_size:256,iterations:5})}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"MLX probe failed");var probe=payload.probe;status.textContent="MLX execution verified: "+Number(probe.operations_per_second).toLocaleString("en-GB",{maximumFractionDigits:0})+" operations/s. Performance only; no task quality or watt-hours, so no scheduler profile was created."}catch(error){status.textContent=error.message}finally{button.disabled=false}}
function tag(text){var s=document.createElement("span");s.className="tag tag-"+String(text||"unavailable").toLowerCase();s.textContent=text||"UNAVAILABLE";return s}
function heroDefinition(term,value,provenance){var dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=term;dd.textContent=value;if(provenance)dd.appendChild(tag(provenance));return [dt,dd]}
function heroDevice(device){var card=document.createElement("div"),name=document.createElement("div"),kind=document.createElement("div"),list=document.createElement("dl"),id=device.identity||{},live=device.live||{},prov=device.provenance||{};
card.className="hero-device";name.className="dev-name";name.textContent=device.name+(device.count>1?" ×"+device.count:"");kind.className="dev-kind";kind.textContent=device.kind+" · "+device.source;card.append(name,kind);
var capacity=[];if(id.memory_total_gb)capacity.push(id.memory_total_gb+" GB");if(id.memory_gb)capacity.push(id.memory_gb+" GB");if(id.gpu_cores)capacity.push(id.gpu_cores+" GPU cores");if(id.cpu_cores)capacity.push(id.cpu_cores+" CPU");
if(capacity.length)list.append.apply(list,heroDefinition("capacity",capacity.join(" · "),prov.identity));
if(live.memory_available_gb!=null)list.append.apply(list,heroDefinition("free now",live.memory_available_gb+" GB",prov.occupancy));
if(live.power_watts!=null)list.append.apply(list,heroDefinition("drawing",live.power_watts+" W",prov.power));
var best=(device.published||[])[0];
if(best)list.append.apply(list,heroDefinition("throughput",Number(best.per_accelerator).toLocaleString("en-GB",{maximumFractionDigits:0})+" "+best.units+"/acc",'PUBLISHED'));
else if(device.measured){var k=Object.keys(device.measured)[0];list.append.apply(list,heroDefinition("ceiling",device.measured[k].median+(k.indexOf("bandwidth")>=0?" GB/s":" GFLOP/s"),'MEASURED'))}
else list.append.apply(list,heroDefinition("throughput","not established",'UNAVAILABLE'));
card.appendChild(list);return card}
async function loadHero(){var host=document.getElementById("heroDevices");host.replaceChildren();for(var i=0;i<2;i++){var s=document.createElement("div");s.className="hero-skeleton";host.appendChild(s)}
try{var response=await fetch("/api/v1/scan",{cache:"no-store"}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Scan unavailable");var report=payload.scan,devices=report.devices||[];host.replaceChildren();devices.forEach(function(d){host.appendChild(heroDevice(d))});
var measured=devices.filter(function(d){return d.measured}).length,published=devices.filter(function(d){return (d.published||[]).length}).length;
document.getElementById("heroTitle").textContent=devices.length+" device"+(devices.length===1?"":"s")+" found";
document.getElementById("heroSub").textContent=(measured?measured+" with a ceiling measured on this hardware. ":"")+(published?published+" matched to audited third-party results. ":"")+"Scanned in one pass across local detection, live telemetry, facility discovery, the device catalogue and published benchmarks.";
}catch(error){host.replaceChildren();document.getElementById("heroTitle").textContent="Could not read this facility";document.getElementById("heroSub").textContent=error.message}}
function scanRow(key,value,provenance){var row=document.createElement("div"),k=document.createElement("span"),v=document.createElement("span");row.className="scan-row";k.className="k";k.textContent=key;v.className="v";v.textContent=value;row.append(k,v);if(provenance)v.appendChild(tag(provenance));return row}
function scanCard(device){var card=document.createElement("div"),title=document.createElement("h4"),sub=document.createElement("div");card.className="scan-card";title.textContent=device.name+(device.count>1?" ×"+device.count:"");sub.className="sub";sub.textContent=device.kind+" · "+device.source;card.append(title,sub);
var id=device.identity||{},live=device.live||{},prov=device.provenance||{};
var installed=[];if(id.cpu_cores)installed.push(id.cpu_cores+" CPU");if(id.gpu_cores)installed.push(id.gpu_cores+" GPU cores");if(id.memory_total_gb)installed.push(id.memory_total_gb+" GB");if(id.memory_gb)installed.push(id.memory_gb+" GB");if(id.storage_total_gb)installed.push(Math.round(id.storage_total_gb)+" GB disk");
if(installed.length)card.appendChild(scanRow("installed",installed.join(" · "),prov.identity));
var now=[];if(live.memory_available_gb!=null)now.push(live.memory_available_gb+" GB free");if(live.gpu_percent!=null)now.push("GPU "+live.gpu_percent+"%");if(live.cpu_percent!=null)now.push("CPU "+live.cpu_percent+"%");if(live.power_watts!=null)now.push(live.power_watts+" W");
if(now.length)card.appendChild(scanRow("right now",now.join(" · "),prov.occupancy||prov.power));
if(device.measured)card.appendChild(scanRow("ceiling",Object.keys(device.measured).map(function(k){var u=k.indexOf("bandwidth")>=0?" GB/s":k.indexOf("gflops")>=0?" GFLOP/s":"";return device.measured[k].median+u+" "+k.replace(/_(gflops|gbs)$/,"").replace(/_/g," ")}).join(" · "),"MEASURED"));
(device.published||[]).slice(0,2).forEach(function(p){card.appendChild(scanRow("throughput",Number(p.per_accelerator).toLocaleString("en-GB",{maximumFractionDigits:0})+" "+p.units+"/acc · "+p.model+" "+p.scenario+" (n="+p.submissions+")","PUBLISHED"))});
if(device.catalogue)card.appendChild(scanRow("catalogue",device.catalogue.key+" · peak "+device.catalogue.peak_tflops_bf16+" TFLOPS",device.catalogue.provenance));
if(!device.measured&&!(device.published||[]).length)card.appendChild(scanRow("throughput","not benchmarked here, no published match","UNAVAILABLE"));
return card}
async function runScan(){var button=document.getElementById("runScan"),panel=document.getElementById("scanPanel"),host=document.getElementById("scanDevices");button.disabled=true;var was=button.textContent;button.textContent="Scanning…";try{var response=await fetch("/api/v1/scan",{cache:"no-store"}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Scan failed");var report=payload.scan;panel.hidden=false;host.replaceChildren();(report.devices||[]).forEach(function(d){host.appendChild(scanCard(d))});document.getElementById("scanTitle").textContent=(report.devices||[]).length+" device record(s) found";document.getElementById("scanSources").textContent=Object.keys(report.sources||{}).map(function(k){return k.replace(/_/g," ")+": "+report.sources[k]}).join(" · ");}catch(error){panel.hidden=false;document.getElementById("scanTitle").textContent=error.message;document.getElementById("scanDevices").replaceChildren()}finally{button.disabled=false;button.textContent=was}}
var LIVE_SOURCE=null,LIVE_SEEN=0;
function meter(label,used,total,unit,detail){var pct=total>0?Math.min(100,Math.max(0,used/total*100)):0,wrap=document.createElement("div"),top=document.createElement("div"),name=document.createElement("span"),value=document.createElement("b"),track=document.createElement("div"),fill=document.createElement("div");wrap.className="meter";top.className="meter-top";track.className="meter-track";fill.className="meter-fill";name.textContent=label;value.textContent=detail;fill.style.width=pct.toFixed(1)+"%";fill.setAttribute("data-load",pct>=90?"high":pct>=75?"warn":"ok");top.append(name,value);track.appendChild(fill);wrap.append(top,track);return wrap}
function liveCard(device){var card=document.createElement("div"),head=document.createElement("div"),name=document.createElement("b"),kind=document.createElement("span"),spec=document.createElement("div"),live=device.live||{},stat=device.static||{},extra=document.createElement("div");card.className="live-card";card.id="live-"+device.id;head.className="live-top";name.textContent=device.name;kind.className="kind";kind.textContent=device.kind;spec.className="spec";
var specs=[];if(stat.cpu_cores)specs.push(stat.cpu_cores+" CPU cores");if(stat.gpu_cores)specs.push(stat.gpu_cores+" GPU cores");if(stat.memory_total_gb)specs.push(stat.memory_total_gb.toFixed(1)+" GB memory");if(stat.storage_total_gb)specs.push(Math.round(stat.storage_total_gb)+" GB storage");spec.textContent=specs.join(" · ")||"Installed specification unavailable";
head.append(name,kind);card.append(head,spec);
if(live.memory_available_gb!=null&&stat.memory_total_gb)card.appendChild(meter("Memory free",stat.memory_total_gb-live.memory_available_gb,stat.memory_total_gb,"GB",live.memory_available_gb.toFixed(2)+" of "+stat.memory_total_gb.toFixed(1)+" GB free"));
else if(live.memory_available_gb!=null&&stat.memory_total_gb==null)card.appendChild(meter("Memory free",0,1,"GB",live.memory_available_gb.toFixed(2)+" GB free"));
if(live.storage_free_gb!=null&&stat.storage_total_gb)card.appendChild(meter("Storage free",stat.storage_total_gb-live.storage_free_gb,stat.storage_total_gb,"GB",Math.round(live.storage_free_gb)+" of "+Math.round(stat.storage_total_gb)+" GB free"));
if(live.gpu_percent!=null)card.appendChild(meter("GPU busy",live.gpu_percent,100,"%",live.gpu_percent.toFixed(0)+"%"));
if(live.cpu_percent!=null)card.appendChild(meter("CPU busy",live.cpu_percent,100,"%",live.cpu_percent.toFixed(1)+"%"));
extra.className="live-extra";var bits=[];if(live.gpu_memory_in_use_gb!=null)bits.push("GPU memory in use "+live.gpu_memory_in_use_gb.toFixed(2)+" GB");if(live.gpu_memory_allocated_gb!=null)bits.push("allocated "+live.gpu_memory_allocated_gb.toFixed(2)+" GB");if(live.swap_used_gb!=null)bits.push("swap "+live.swap_used_gb.toFixed(2)+" GB");if(live.power_watts!=null)bits.push(live.power_watts.toFixed(0)+" W board");if(live.temperature_c!=null)bits.push(live.temperature_c.toFixed(0)+"°C");bits.forEach(function(text){var span=document.createElement("span");span.textContent=text;extra.appendChild(span)});if(bits.length)card.appendChild(extra);
return card}
function renderTelemetry(payload){var host=document.getElementById("liveDevices"),devices=payload.devices||[];host.replaceChildren();if(!devices.length){var empty=document.createElement("p");empty.className="evidence-empty";empty.textContent="No local device reported telemetry.";host.appendChild(empty);return}devices.forEach(function(device){host.appendChild(liveCard(device))});LIVE_SEEN+=1;var stamp=new Date(payload.observed_at);document.getElementById("liveState").textContent="Updated "+stamp.toLocaleTimeString()+" · "+LIVE_SEEN+" readings";document.getElementById("liveDot").setAttribute("data-state","live");if((payload.warnings||[]).length)document.getElementById("liveState").textContent+=" · "+payload.warnings.join("; ")}
function startTelemetry(){if(!window.EventSource){document.getElementById("liveState").textContent="Live updates unsupported in this browser";return}LIVE_SOURCE=new EventSource("/api/v1/telemetry/stream?interval=2");LIVE_SOURCE.onmessage=function(event){try{renderTelemetry(JSON.parse(event.data))}catch(error){document.getElementById("liveState").textContent=error.message}};LIVE_SOURCE.onerror=function(){document.getElementById("liveDot").setAttribute("data-state","error");document.getElementById("liveState").textContent="Reconnecting"}}
function shortDigest(digest){return digest?String(digest).slice(0,12):"—"}
function renderInventory(payload){var summary=payload.summary||{},snapshot=payload.snapshot,body=document.getElementById("inventoryRows"),empty=document.getElementById("inventoryEmpty"),status=document.getElementById("inventoryStatus");document.getElementById("iSnapshotCount").textContent=Number(summary.snapshot_count||0);document.getElementById("iDeviceCount").textContent=Number(summary.latest_device_count||0);document.getElementById("iWarningCount").textContent=Number(summary.latest_warning_count||0);document.getElementById("iState").textContent=payload.configured?(snapshot?"Snapshot ready":"Configured, not yet run"):"Not configured";body.replaceChildren();if(!snapshot||!(snapshot.devices||[]).length){empty.hidden=false;empty.textContent=payload.configured?"No discovery snapshot yet. Run discovery to walk the declared endpoints read-only.":"No discovery configuration. Declare read-only endpoints in data/discovery.json per docs/discovery.md.";return}empty.hidden=true;snapshot.devices.forEach(function(device){var row=document.createElement("tr");addCells(row,[device.name,device.identity_provenance,device.memory_gb!=null?Number(device.memory_gb).toFixed(0)+" GiB · "+device.memory_provenance:"Unavailable",device.live_power_watts!=null?Number(device.live_power_watts).toFixed(0)+" W · "+device.power_provenance:"Unavailable",device.power_scope||"—",device.performance_provenance,device.source,shortDigest(device.device_digest)]);body.appendChild(row)});status.textContent=(snapshot.warnings||[]).length?"Warnings: "+snapshot.warnings.join(" · "):"Snapshot #"+snapshot.snapshot_id+" recorded "+snapshot.recorded_at+". Identity is a keyed digest; no raw serial number is stored."}
async function loadInventory(){document.getElementById("iState").textContent="Loading";try{var response=await fetch("/api/v1/inventory",{cache:"no-store"}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Inventory unavailable");renderInventory(payload)}catch(error){document.getElementById("iState").textContent="Unavailable";var empty=document.getElementById("inventoryEmpty");empty.hidden=false;empty.textContent=error.message}}
async function runDiscovery(){var button=document.getElementById("refreshInventory"),status=document.getElementById("inventoryStatus");button.disabled=true;status.textContent="Walking declared endpoints read-only…";try{var response=await fetch("/api/v1/inventory/refresh",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({})}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Discovery failed");renderInventory({configured:true,summary:payload.summary,snapshot:payload.snapshot})}catch(error){status.textContent=error.message}finally{button.disabled=false}}
document.getElementById("refreshEvidence").addEventListener("click",loadEvidenceProfiles);document.getElementById("runProbe").addEventListener("click",runEvidenceProbe);document.getElementById("refreshInventory").addEventListener("click",runDiscovery);document.getElementById("runScan").addEventListener("click",runScan);startTelemetry();loadHero();loadEvidenceProfiles();loadInventory();
})();"""
