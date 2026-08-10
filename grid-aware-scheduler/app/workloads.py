"""Interactive capacity-aware AI workload queue."""
from __future__ import annotations

import html
import json
from urllib.parse import urlencode

from app.markets import MarketContext
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS


def _location_options(context: MarketContext) -> str:
    return "".join(
        f'<option value="{html.escape(choice.key)}"'
        f'{" selected" if choice.key == context.location_key else ""}>'
        f'{html.escape(choice.name)} · {html.escape(choice.detail)}</option>'
        for choice in context.locations
    )


def _job_card(job: dict, index: int) -> str:
    units = "".join(
        f'<option value="{unit}"{" selected" if unit == job["unit"] else ""}>'
        f'{label}</option>'
        for unit, label in (
            ("tokens", "Tokens"),
            ("images", "Images"),
            ("audio_seconds", "Audio seconds"),
            ("samples", "Samples"),
            ("training_examples", "Training examples"),
            ("optimizer_steps", "Optimiser steps"),
        )
    )
    run_modes = "".join(
        f'<option value="{value}"{" selected" if value == job["run_mode"] else ""}>{label}</option>'
        for value, label in (
            ("inference", "Inference"),
            ("evaluation", "Evaluation"),
            ("fine_tuning", "Fine-tuning"),
            ("training", "Training"),
        )
    )
    precisions = "".join(
        f'<option value="{value}"{" selected" if value == job["precision"] else ""}>{value}</option>'
        for value in ("int4", "int8", "fp16", "bf16", "fp32")
    )
    compute_units = "".join(
        f'<option value="{value}"{" selected" if value == job["compute_unit"] else ""}>{label}</option>'
        for value, label in (
            ("cpu", "CPU"),
            ("gpu", "GPU"),
            ("neural_engine", "Neural Engine"),
            ("cpu_gpu", "CPU + GPU"),
            ("all", "All eligible units"),
        )
    )
    return f"""<article class="job" data-job>
      <div class="job-head"><div><span class="eyebrow">Queue item {index + 1}</span>
      <input data-field="job_id" aria-label="Job ID" value="{html.escape(job['id'])}"></div>
      <button type="button" class="remove" aria-label="Remove job">Remove</button></div>
      <div class="fields">
        <label>Workflow ID<input data-field="workflow_id" value="{html.escape(job['workflow_id'])}"></label>
        <label>Stage name<input data-field="stage_name" value="{html.escape(job['stage_name'])}"></label>
        <label>Depends on stage IDs<input data-field="depends_on" value="{html.escape(', '.join(job['depends_on']))}" placeholder="stage-a, stage-b"></label>
        <label>Workload class<input data-field="label" value="{html.escape(job['label'])}"></label>
        <label>Run mode<select data-field="run_mode">{run_modes}</select></label>
        <label>Model / deployment<input data-field="model_id" value="{html.escape(job['model_id'])}"></label>
        <label>Model version<input data-field="model_version" value="{html.escape(job['model_version'])}"></label>
        <label>Precision<select data-field="precision">{precisions}</select></label>
        <label>Compute unit<select data-field="compute_unit">{compute_units}</select></label>
        <label>Execution hardware<input data-field="hardware" value="{html.escape(job['hardware'])}"></label>
        <label>Useful work<input data-field="work_amount" type="number" min="0.000001" step="any" value="{job['amount']}"></label>
        <label>Work unit<select data-field="work_unit">{units}</select></label>
        <label>Runtime · hours<input data-field="runtime_hours" type="number" min="0.000001" step="0.05" value="{job['runtime']}"></label>
        <label>IT power · kW<input data-field="it_power_kw" type="number" min="0" step="0.001" value="{job['power']}"></label>
        <label>PUE<input data-field="pue" type="number" min="1" max="5" step="0.01" value="{job['pue']}"></label>
        <label>Memory required · GB<input data-field="memory_required_gb" type="number" min="0" step="0.1" value="{job['memory_required']}"></label>
        <label>Memory available · GB<input data-field="memory_available_gb" type="number" min="0" step="0.1" value="{job['memory_available']}"></label>
        <label>Quality score · 0 to 1<input data-field="quality_score" type="number" min="0" max="1" step="0.01" value="{job['quality']}"></label>
        <label>Minimum quality<input data-field="minimum_quality" type="number" min="0" max="1" step="0.01" value="{job['minimum']}"></label>
        <label>Deadline · hours<input data-field="deadline_hours" type="number" min="0.01" step="0.5" value="{job['deadline']}"></label>
        <label>Operator utility<input data-field="utility" type="number" min="0.000001" step="0.5" value="{job['utility']}"></label>
        <label>Evidence state<select data-field="provenance"><option value="ESTIMATED">Estimated scenario</option><option value="MEASURED">Measured</option></select></label>
        <label>Evaluation suite<input data-field="evaluation_suite" value="scenario-eval"></label>
        <label>Suite version<input data-field="evaluation_version" value="1.0"></label>
        <label class="check"><input data-field="mandatory" type="checkbox" checked>Mandatory workload</label>
      </div>
    </article>"""


def render(context: MarketContext) -> str:
    common = {
        "model_version": "scenario-1", "memory_available": 8.0,
        "pue": 1.0, "deadline": 24,
    }
    templates = {
        "language": [
            {**common, "id": "language-prepare", "workflow_id": "language-evaluation",
             "stage_name": "Prepare evaluation batch", "depends_on": [],
             "label": "Language data preparation", "run_mode": "evaluation",
             "model_id": "data-pipeline", "precision": "fp32", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 1000, "unit": "samples",
             "runtime": 0.5, "power": 0.012, "quality": 1.0,
             "memory_required": 0.5, "minimum": 1.0, "utility": 2},
            {**common, "id": "language-infer", "workflow_id": "language-evaluation",
             "stage_name": "Run model inference", "depends_on": ["language-prepare"],
             "label": "Language inference", "run_mode": "inference",
             "model_id": "reference-language-model", "precision": "int4",
             "compute_unit": "gpu", "hardware": "Apple M2 GPU scenario",
             "amount": 10000, "unit": "tokens", "runtime": 1.0, "power": 0.03,
             "quality": 0.82, "memory_required": 2.0, "minimum": 0.75, "utility": 5},
            {**common, "id": "language-score", "workflow_id": "language-evaluation",
             "stage_name": "Score response quality", "depends_on": ["language-infer"],
             "label": "Language quality evaluation", "run_mode": "evaluation",
             "model_id": "quality-evaluator", "precision": "fp16", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 1000, "unit": "samples",
             "runtime": 0.5, "power": 0.015, "quality": 0.90,
             "memory_required": 1.0, "minimum": 0.85, "utility": 3},
        ],
        "vision": [
            {**common, "id": "vision-prepare", "workflow_id": "vision-validation",
             "stage_name": "Decode and normalise images", "depends_on": [],
             "label": "Vision preprocessing", "run_mode": "evaluation",
             "model_id": "image-pipeline", "precision": "fp16", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 5000, "unit": "images",
             "runtime": 0.5, "power": 0.014, "quality": 1.0,
             "memory_required": 0.8, "minimum": 1.0, "utility": 2},
            {**common, "id": "vision-run", "workflow_id": "vision-validation",
             "stage_name": "Run Core ML evaluation", "depends_on": ["vision-prepare"],
             "label": "Vision model evaluation", "run_mode": "evaluation",
             "model_id": "reference-vision-model", "precision": "fp16",
             "compute_unit": "neural_engine", "hardware": "Apple M2 Neural Engine scenario",
             "amount": 5000, "unit": "images", "runtime": 1.0, "power": 0.02,
             "quality": 0.90, "memory_required": 0.5, "minimum": 0.85, "utility": 5},
            {**common, "id": "vision-report", "workflow_id": "vision-validation",
             "stage_name": "Aggregate accuracy report", "depends_on": ["vision-run"],
             "label": "Vision metric aggregation", "run_mode": "evaluation",
             "model_id": "metric-pipeline", "precision": "fp32", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 5000, "unit": "samples",
             "runtime": 0.5, "power": 0.01, "quality": 0.90,
             "memory_required": 0.4, "minimum": 0.85, "utility": 2},
        ],
        "speech": [
            {**common, "id": "speech-prepare", "workflow_id": "speech-regression",
             "stage_name": "Prepare audio segments", "depends_on": [],
             "label": "Speech preprocessing", "run_mode": "evaluation",
             "model_id": "audio-pipeline", "precision": "fp16", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 7200, "unit": "audio_seconds",
             "runtime": 0.5, "power": 0.012, "quality": 1.0,
             "memory_required": 0.6, "minimum": 1.0, "utility": 2},
            {**common, "id": "speech-run", "workflow_id": "speech-regression",
             "stage_name": "Transcribe evaluation set", "depends_on": ["speech-prepare"],
             "label": "Speech transcription", "run_mode": "inference",
             "model_id": "reference-speech-model", "precision": "int8", "compute_unit": "gpu",
             "hardware": "Apple M2 GPU scenario", "amount": 7200,
             "unit": "audio_seconds", "runtime": 1.5, "power": 0.025,
             "quality": 0.85, "memory_required": 1.2, "minimum": 0.80, "utility": 5},
            {**common, "id": "speech-score", "workflow_id": "speech-regression",
             "stage_name": "Calculate word error rate", "depends_on": ["speech-run"],
             "label": "Speech quality evaluation", "run_mode": "evaluation",
             "model_id": "wer-pipeline", "precision": "fp32", "compute_unit": "cpu",
             "hardware": "Apple M2 CPU scenario", "amount": 7200, "unit": "audio_seconds",
             "runtime": 0.5, "power": 0.01, "quality": 0.85,
             "memory_required": 0.4, "minimum": 0.80, "utility": 2},
        ],
    }
    defaults = templates["language"]
    cards = "".join(_job_card(job, index) for index, job in enumerate(defaults))
    template_cards = "".join(
        f'<template id="template-{key}">'
        + "".join(_job_card(job, index) for index, job in enumerate(jobs))
        + "</template>"
        for key, jobs in templates.items()
    )
    price_points = [point for point in context.series if point.price is not None]
    carbon_points = [
        point for point in context.series if point.carbon_intensity is not None
    ]
    current = context.series[0]
    cheapest = min(price_points, key=lambda point: point.price)
    cleanest = min(carbon_points, key=lambda point: point.carbon_intensity)
    location_options = _location_options(context)
    shared = urlencode({"market": context.market_key, "location": context.location_key})
    page = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Operations · Grid-Aware Scheduler</title>__THEME_BOOTSTRAP__
<style>
:root{color-scheme:light dark;--bg:#f2f2f7;--card:#fff;--text:#09090b;--text-2:#66666d;
--text-3:#a4a4aa;--sep:#dedee4;--blue:#007aff;--green:#248a3d;--orange:#b35300;
--red:#c7261b;--shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.06)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#000;--card:#1c1c1e;
--text:#f5f5f7;--text-2:#aaaab2;--text-3:#66666c;--sep:#38383d;--blue:#0a84ff;
--green:#32a852;--orange:#ff9f0a;--red:#ff6961;--shadow:none}}
:root[data-theme="dark"]{--bg:#000;--card:#1c1c1e;--text:#f5f5f7;--text-2:#aaaab2;
--text-3:#66666c;--sep:#38383d;--blue:#0a84ff;--green:#32a852;--orange:#ff9f0a;
--red:#ff6961;--shadow:none}*{box-sizing:border-box}body{margin:0;padding:0 24px 72px;
background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,
"SF Pro Text",Helvetica,Arial,sans-serif}.wrap{max-width:1240px;margin:auto}header{padding:52px 0 26px}
h1{font-size:40px;line-height:1.08;letter-spacing:-.035em;margin:0 0 6px}.sub{font-size:17px;
color:var(--text-2);margin:0;max-width:780px}nav{display:flex;gap:8px;margin-top:16px;overflow:auto}
nav a{font-size:13px;font-weight:600;text-decoration:none;padding:6px 14px;border-radius:999px;
white-space:nowrap;color:var(--text-2);background:color-mix(in srgb,var(--text) 5%,transparent)}
nav a.on{background:var(--blue);color:#fff}.card,.job{background:var(--card);border-radius:18px;
box-shadow:var(--shadow)}.card{padding:22px 24px;margin-bottom:18px}.card h2{font-size:20px;margin:0 0 4px}
.note{color:var(--text-2);margin:0 0 17px}.eyebrow{color:var(--text-2);font-size:10px;
font-weight:700;letter-spacing:.075em;text-transform:uppercase}.signal{display:flex;gap:9px;flex-wrap:wrap;
margin:14px 0 0}.pill{padding:5px 9px;border-radius:999px;background:color-mix(in srgb,var(--blue) 9%,transparent);
color:var(--blue);font-size:11px}.warning{border:1px solid color-mix(in srgb,var(--orange) 40%,var(--sep));
background:color-mix(in srgb,var(--orange) 6%,var(--card));padding:13px 15px;border-radius:12px;
color:var(--orange);font-size:12px;margin-top:16px}.controls,.fields{display:grid;gap:13px}
.controls{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.fields{grid-template-columns:repeat(5,minmax(140px,1fr))}
label{display:flex;flex-direction:column;gap:5px;color:var(--text-2);font-size:11px;font-weight:600}
input,select,button{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--sep);
border-radius:10px;padding:9px 10px}input:focus,select:focus,button:focus-visible{outline:3px solid color-mix(in srgb,var(--blue) 25%,transparent);outline-offset:1px}
button{cursor:pointer;font-weight:650}.primary{border:0;background:var(--blue);color:#fff;padding:11px 17px}
.actions{display:flex;gap:9px;flex-wrap:wrap;align-items:end}.actions label{min-width:140px}.job{padding:18px 20px;margin-bottom:12px;
border:1px solid transparent}.job:focus-within{border-color:color-mix(in srgb,var(--blue) 45%,var(--sep))}
.job-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.job-head>div{display:flex;align-items:center;gap:10px}.job-head input{font-size:16px;font-weight:700;
border:0;padding:2px 4px;background:transparent;min-width:240px}.remove{font-size:11px;color:var(--red);padding:6px 9px}
.check{flex-direction:row;align-items:center;align-self:end;padding-bottom:9px}.check input{width:16px;height:16px}
.queue-head{display:flex;align-items:end;justify-content:space-between;gap:16px;margin-bottom:14px}
.ops-card{padding:24px}.ops-head{display:flex;align-items:start;justify-content:space-between;gap:24px}
.ops-head h2{font-size:28px;letter-spacing:-.03em;margin:3px 0 7px}.ops-head .note{max-width:720px}
.ops-grid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:1px;background:var(--sep);
border-radius:14px;overflow:hidden;margin:18px 0}.ops-stat{background:var(--card);padding:15px 16px}
.ops-stat span{display:block;color:var(--text-2);font-size:10px;text-transform:uppercase;letter-spacing:.06em}
.ops-stat b{display:block;font-size:22px;letter-spacing:-.025em;margin-top:4px}.decision-chain{display:grid;
grid-template-columns:repeat(5,1fr);gap:8px}.chain-step{position:relative;padding:12px;border:1px solid var(--sep);
border-radius:11px}.chain-step:not(:last-child)::after{content:">";position:absolute;right:-8px;top:50%;
transform:translateY(-50%);z-index:1;color:var(--text-3);font-weight:800}.chain-step span{display:block;
color:var(--text-2);font-size:9px;text-transform:uppercase;letter-spacing:.06em}.chain-step b{display:block;
font-size:12px;margin-top:3px}.placement-card{margin-top:22px}.section-anchor{color:var(--blue);font-size:12px;
font-weight:650;text-decoration:none}.section-anchor:hover{text-decoration:underline}
.metrics{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:1px;background:var(--sep);
border-radius:14px;overflow:hidden;margin:16px 0}.metric{background:var(--card);padding:15px}.metric span{display:block;
font-size:10px;color:var(--text-2);text-transform:uppercase;letter-spacing:.06em}.metric b{display:block;
font-size:24px;letter-spacing:-.03em;margin-top:3px}.result{display:none}.result.show{display:block}
.table-wrap{overflow:auto;border:1px solid var(--sep);border-radius:13px}table{width:100%;border-collapse:collapse;
min-width:950px}th,td{text-align:left;padding:10px 11px;border-bottom:1px solid var(--sep);white-space:nowrap}
th{color:var(--text-2);font-size:10px;text-transform:uppercase;letter-spacing:.055em}tbody tr:last-child td{border-bottom:0}
.status{min-height:20px;color:var(--text-2);font-size:12px}.status.error{color:var(--red)}.result-note{font-size:12px;
color:var(--text-2)}.provenance{font-size:11px;color:var(--text-2);border-top:1px solid var(--sep);
padding-top:14px;margin-top:17px}.empty{padding:28px;text-align:center;color:var(--text-2)}
@media(max-width:980px){.fields{grid-template-columns:repeat(3,1fr)}.metrics,.ops-grid{grid-template-columns:repeat(3,1fr)}
.decision-chain{grid-template-columns:1fr 1fr}.chain-step::after{display:none}}
@media(max-width:650px){body{padding-left:14px;padding-right:14px}h1{font-size:32px}.fields{grid-template-columns:1fr 1fr}
.metrics,.ops-grid{grid-template-columns:1fr 1fr}.ops-head,.queue-head{align-items:stretch;flex-direction:column}
.decision-chain{grid-template-columns:1fr}.job-head>div{align-items:flex-start;flex-direction:column}.job-head input{min-width:0;width:100%}}
__THEME_CSS__</style></head><body><div class="wrap">__THEME_CONTROL__
<header><h1>AI Data Centre Operations</h1><p class="sub">Control workload admission, execution evidence, facility capacity and grid-aware placement from one operator surface.</p>
<nav><a class="on" href="/?__SHARED__">Operations</a><a href="/simulator?__SHARED__">Fleet Lab</a><a href="/planner?__SHARED__">Placement Lab</a><a href="/grid?__SHARED__">Sites &amp; Grid</a><a href="/decisions">Decisions</a></nav></header>
<section class="card ops-card"><div class="ops-head"><div><span class="eyebrow">Operator control plane</span><h2>Demand, evidence and service state</h2>
<p class="note">The optimiser begins with useful AI work and its quality and deadline obligations. Energy-market signals determine the best eligible placement afterwards.</p></div>
<a class="section-anchor" href="#placementPolicy">Edit site policy</a></div>
<div class="ops-grid"><div class="ops-stat"><span>Queued workloads</span><b id="oQueued">3</b></div>
<div class="ops-stat"><span>Mandatory</span><b id="oMandatory">3</b></div>
<div class="ops-stat"><span>Measured profiles</span><b id="oMeasured">0 / 3</b></div>
<div class="ops-stat"><span>Deadline range</span><b id="oDeadline">12–18 h</b></div>
<div class="ops-stat"><span>Decision state</span><b id="oDecision">Not run</b></div></div>
<div class="decision-chain"><div class="chain-step"><span>1 · Demand</span><b>Useful AI work</b></div>
<div class="chain-step"><span>2 · Quality</span><b>Eligible variants</b></div><div class="chain-step"><span>3 · Capacity</span><b>Fleet and facility</b></div>
<div class="chain-step"><span>4 · Placement</span><b>Site, time and grid</b></div><div class="chain-step"><span>5 · Evidence</span><b>Audited outcome</b></div></div>
<div class="warning">The starter queue is an estimated scenario, not measured Apple-device evidence. A recommendation remains a forecast until realised energy, price and carbon are scored.</div></section>
<section><div class="queue-head"><div><span class="eyebrow">AI workload demand</span><h2>Admission queue and service obligations</h2><p class="note">Define useful work, quality floor, deadline, execution variant and evidence state. Unlike work units remain separate; utility expresses operator priority.</p></div>
<div class="actions"><label>Example workflow<select id="workflowTemplate"><option value="language">Language evaluation</option><option value="vision">Vision validation</option><option value="speech">Speech regression</option></select></label><label>Completion window<select id="timeframe"><option value="6">6 hours</option><option value="12">12 hours</option><option value="24" selected>24 hours</option><option value="48">48 hours</option></select></label><button id="loadTemplate" type="button">Load example</button><button id="addJob" type="button">Add workload</button><button id="optimise" class="primary" type="button">Optimise queue</button></div></div>
<div id="jobs">__CARDS__</div>__TEMPLATE_CARDS__<p class="status" id="status">Ready. Review the scenario values before optimising.</p></section>
<section class="card"><div class="queue-head"><div><span class="eyebrow">Operational preflight</span><h2>Quality, memory, SLA and capacity readiness</h2>
<p class="note">This gate runs before price or carbon ranking. An execution option that fails quality, memory or deadline cannot become cheaper by moving it to another hour.</p></div></div>
<div class="ops-grid"><div class="ops-stat"><span>Quality eligible</span><b id="rEligible">3 / 3</b></div>
<div class="ops-stat"><span>Workloads at risk</span><b id="rRisk">0</b></div><div class="ops-stat"><span>Candidate facility power</span><b id="rPower">0.075 kW</b></div>
<div class="ops-stat"><span>Capacity headroom</span><b id="rHeadroom">9.925 kW</b></div><div class="ops-stat"><span>Execution variants</span><b id="rVariants">3</b></div></div>
<p class="status" id="readinessIssues">All current scenario variants pass their declared quality, memory and deadline gates.</p></section>
<section class="card placement-card" id="placementPolicy"><div class="queue-head"><div><span class="eyebrow">Placement constraints</span><h2>Facility, electricity price and carbon policy</h2>
<p class="note">These signals constrain where and when eligible AI work runs. They do not define the workload or its quality requirement.</p></div><a class="section-anchor" href="/grid?__SHARED__">Open full grid terminal</a></div>
<div class="controls"><label>Power market<select id="market"><option value="GB"__GB__>Great Britain</option><optgroup label="United States"><option value="CAISO"__CAISO__>California ISO</option><option value="NYISO"__NYISO__>New York ISO</option></optgroup></select></label>
<label>Data-centre grid location<select id="location">__LOCATIONS__</select></label><label>Facility power capacity · kW<input id="maxPower" type="number" min="0.000001" step="0.1" value="10"></label>
<label>Total electricity cost cap · __SYMBOL__ (0 = off)<input id="maxCost" type="number" min="0" step="0.01" value="0"></label>
<label>Total operational carbon cap · kg (0 = off)<input id="maxCarbon" type="number" min="0" step="0.01" value="0"></label></div>
<div class="signal"><span class="pill">__MARKET__</span><span class="pill">__LOCATION__</span><span class="pill">__MODE__</span><span class="pill">__POINTS__ half-hours</span></div>
<div class="ops-grid"><div class="ops-stat"><span>First interval price</span><b>__CURRENT_PRICE__</b></div><div class="ops-stat"><span>First interval carbon</span><b>__CURRENT_CARBON__</b></div><div class="ops-stat"><span>Lowest price in window</span><b>__LOW_PRICE__</b></div><div class="ops-stat"><span>Cleanest interval</span><b>__CLEAN_CARBON__</b></div><div class="ops-stat"><span>Signal resolution</span><b>30 min</b></div></div></section>
<section class="card result" id="result"><div class="queue-head"><div><span class="eyebrow">Exact capacity schedule</span><h2>Recommended run plan</h2><p class="note" id="resultSummary"></p></div>
<div class="actions"><button id="downloadJson" type="button">Plan JSON</button><button id="downloadCsv" type="button">Schedule CSV</button></div></div>
<div class="metrics"><div class="metric"><span>Scheduled jobs</span><b id="mJobs">0</b></div><div class="metric"><span>Utility completed</span><b id="mUtility">0</b></div><div class="metric"><span>Facility energy</span><b id="mEnergy">0</b></div><div class="metric"><span>Forecast cost</span><b id="mCost">0</b></div><div class="metric"><span>Forecast carbon</span><b id="mCarbon">0</b></div></div>
<div class="table-wrap"><table><thead><tr><th>Job</th><th>Model</th><th>Execution</th><th>Work</th><th>Start</th><th>Finish</th><th>Energy</th><th>Energy / unit</th><th>Cost</th><th>Carbon</th><th>Evidence</th></tr></thead><tbody id="schedule"></tbody></table><div class="empty" id="noSchedule" hidden>No jobs were scheduled.</div></div>
<p class="provenance" id="provenance"></p></section>
</div><script>
(function(){"use strict";var latest=null,jobCounter=3;
function q(root,name){return root.querySelector('[data-field="'+name+'"]')}
function value(root,name){return q(root,name).value.trim()}
function number(root,name){var n=Number(value(root,name));if(!Number.isFinite(n))throw new Error(name+" must be a number");return n}
function bindRemove(root){root.querySelector(".remove").addEventListener("click",function(){if(document.querySelectorAll("[data-job]").length===1){setStatus("At least one workload is required.",true);return}root.remove();updateOverview()});root.addEventListener("input",updateOverview);root.addEventListener("change",updateOverview)}
document.querySelectorAll("[data-job]").forEach(bindRemove);
function applyTimeframe(){var hours=document.getElementById("timeframe").value;document.querySelectorAll("[data-job]").forEach(function(root){q(root,"deadline_hours").value=hours});updateOverview()}
document.getElementById("timeframe").addEventListener("change",applyTimeframe);
document.getElementById("loadTemplate").addEventListener("click",function(){var key=document.getElementById("workflowTemplate").value,source=document.getElementById("template-"+key),fragment=source.content.cloneNode(true),jobs=document.getElementById("jobs");jobs.replaceChildren(fragment);document.querySelectorAll("[data-job]").forEach(bindRemove);jobCounter=document.querySelectorAll("[data-job]").length;applyTimeframe();setStatus("Example workflow loaded. Review its estimated execution and quality values before optimising.",false)});
function updateOverview(){var roots=Array.from(document.querySelectorAll("[data-job]")),deadlines=roots.map(function(root){return Number(q(root,"deadline_hours").value)}).filter(Number.isFinite),measured=roots.filter(function(root){return q(root,"provenance").value==="MEASURED"}).length,mandatory=roots.filter(function(root){return q(root,"mandatory").checked}).length,eligible=0,risks=[],facilityPower=0;roots.forEach(function(root){var id=q(root,"job_id").value||"Unnamed workload",quality=Number(q(root,"quality_score").value),minimum=Number(q(root,"minimum_quality").value),runtime=Number(q(root,"runtime_hours").value),deadline=Number(q(root,"deadline_hours").value),required=Number(q(root,"memory_required_gb").value),available=Number(q(root,"memory_available_gb").value),power=Number(q(root,"it_power_kw").value),pue=Number(q(root,"pue").value),issues=[];if(quality<minimum)issues.push("quality");if(required>available)issues.push("memory");if(runtime>deadline)issues.push("deadline");if(!issues.length)eligible+=1;else risks.push(id+": "+issues.join(", "));if(Number.isFinite(power)&&Number.isFinite(pue))facilityPower+=power*pue});var capacity=Number(document.getElementById("maxPower").value),headroom=capacity-facilityPower;document.getElementById("oQueued").textContent=roots.length;document.getElementById("oMandatory").textContent=mandatory;document.getElementById("oMeasured").textContent=measured+" / "+roots.length;document.getElementById("oDeadline").textContent=deadlines.length?(Math.min.apply(null,deadlines)+"–"+Math.max.apply(null,deadlines)+" h"):"Unavailable";document.getElementById("rEligible").textContent=eligible+" / "+roots.length;document.getElementById("rRisk").textContent=risks.length;document.getElementById("rPower").textContent=facilityPower.toFixed(3)+" kW";document.getElementById("rHeadroom").textContent=(Number.isFinite(headroom)?headroom.toFixed(3):"Unavailable")+" kW";document.getElementById("rVariants").textContent=roots.length;var issueNode=document.getElementById("readinessIssues");issueNode.textContent=risks.length?risks.join(" · "):(headroom<0?"Declared candidate power exceeds facility capacity.":"All current variants pass their declared quality, memory and deadline gates.");issueNode.className="status"+((risks.length||headroom<0)?" error":"")}
document.getElementById("addJob").addEventListener("click",function(){jobCounter+=1;var source=document.querySelector("[data-job]"),copy=source.cloneNode(true);q(copy,"job_id").value="workload-"+jobCounter;q(copy,"label").value="New workload";copy.querySelector(".eyebrow").textContent="Queue item "+jobCounter;bindRemove(copy);document.getElementById("jobs").appendChild(copy);updateOverview()});
function navigate(){var market=document.getElementById("market").value,location=document.getElementById("location").value;if(market!=="__MARKET_KEY__")location=market==="CAISO"?"sp15":market==="NYISO"?"nyc":"national";window.location.href="/?market="+encodeURIComponent(market)+"&location="+encodeURIComponent(location)}
document.getElementById("market").addEventListener("change",navigate);document.getElementById("location").addEventListener("change",navigate);
function collect(){var jobs=[];document.querySelectorAll("[data-job]").forEach(function(root,index){var id=value(root,"job_id"),provenance=value(root,"provenance"),dependencies=value(root,"depends_on").split(",").map(function(item){return item.trim()}).filter(Boolean);if(!id)throw new Error("Every workload needs a job ID");jobs.push({job_id:id,workflow_id:value(root,"workflow_id"),stage_name:value(root,"stage_name"),depends_on:dependencies,workload_class:value(root,"label"),run_mode:value(root,"run_mode"),deadline_hours:number(root,"deadline_hours"),work_amount:number(root,"work_amount"),work_unit:value(root,"work_unit"),utility:number(root,"utility"),minimum_quality:number(root,"minimum_quality"),mandatory:q(root,"mandatory").checked,require_measured_quality:provenance==="MEASURED",variants:[{candidate_key:id+"-variant-"+(index+1),model_id:value(root,"model_id"),model_version:value(root,"model_version"),precision:value(root,"precision"),compute_unit:value(root,"compute_unit"),hardware:value(root,"hardware"),runtime_hours:number(root,"runtime_hours"),it_power_kw:number(root,"it_power_kw"),pue:number(root,"pue"),memory_required_gb:number(root,"memory_required_gb"),memory_available_gb:number(root,"memory_available_gb"),quality_score:number(root,"quality_score"),quality_provenance:provenance,evaluation_suite:value(root,"evaluation_suite"),evaluation_version:value(root,"evaluation_version"),hardware_provenance:provenance}]})});var facility={max_power_kw:Number(document.getElementById("maxPower").value)};var cap=Number(document.getElementById("maxCost").value),carbon=Number(document.getElementById("maxCarbon").value);if(cap>0)facility.max_total_cost=cap;if(carbon>0)facility.max_total_carbon_kg=carbon;return{market:"__MARKET_KEY__",location:"__LOCATION_KEY__",facility:facility,jobs:jobs}}
function setStatus(message,error){var node=document.getElementById("status");node.textContent=message;node.className="status"+(error?" error":"")}
function money(value,currency){try{return new Intl.NumberFormat("en-GB",{style:"currency",currency:currency,maximumFractionDigits:3}).format(value)}catch(error){return Number(value).toFixed(3)+" "+currency}}
function when(value){return new Intl.DateTimeFormat("en-GB",{dateStyle:"medium",timeStyle:"short",timeZone:"UTC"}).format(new Date(value))+" UTC"}
function evidence(notes){var hit=(notes||[]).find(function(note){return note.indexOf("Quality provenance ")===0});return hit?hit.replace("Quality provenance ",""):"Unavailable"}
function render(result){latest=result;var box=document.getElementById("result"),body=document.getElementById("schedule");box.classList.add("show");body.replaceChildren();(result.assignments||[]).forEach(function(item){var tr=document.createElement("tr"),cells=[item.job_id,item.model_id+" · "+item.model_version,item.compute_unit+" · "+item.precision,Number(item.work_amount).toLocaleString("en-GB")+" "+item.work_unit,when(item.start),when(item.finish),Number(item.facility_energy_kwh).toFixed(3)+" kWh",Number(item.energy_wh_per_work_unit).toPrecision(3)+" Wh/"+item.work_unit,money(item.cost,item.currency),Number(item.carbon_kg).toFixed(3)+" kg",item.quality_provenance];cells.forEach(function(text){var td=document.createElement("td");td.textContent=text;tr.appendChild(td)});body.appendChild(tr)});document.getElementById("noSchedule").hidden=result.assignments.length>0;document.getElementById("mJobs").textContent=result.assignments.length;document.getElementById("mUtility").textContent=Number(result.completed_utility).toFixed(1);document.getElementById("mEnergy").textContent=Number(result.total_energy_kwh).toFixed(3)+" kWh";document.getElementById("mCost").textContent=money(result.total_cost,result.market.currency);document.getElementById("mCarbon").textContent=Number(result.total_carbon_kg).toFixed(3)+" kg";document.getElementById("oDecision").textContent="Scheduled";var work=Object.entries(result.completed_work).map(function(entry){return Number(entry[1]).toLocaleString("en-GB")+" "+entry[0]}).join(", ");document.getElementById("resultSummary").textContent=work+(result.unscheduled_job_ids.length?". Unscheduled: "+result.unscheduled_job_ids.join(", "):". Every requested job was scheduled.");document.getElementById("provenance").textContent=result.signal_mode+". "+result.market.provenance+" Exact search considered "+result.combinations_considered+" complete schedules from an upper bound of "+result.search_space_upper_bound+". Forecast values require realised outturn scoring before any savings claim.";box.scrollIntoView({behavior:"smooth",block:"start"})}
document.getElementById("optimise").addEventListener("click",async function(){var button=this;try{var payload=collect();button.disabled=true;document.getElementById("oDecision").textContent="Optimising";setStatus("Optimising every legal queue placement…",false);var response=await fetch("/api/v1/portfolio?market="+encodeURIComponent(payload.market)+"&location="+encodeURIComponent(payload.location),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),result=await response.json();if(!response.ok)throw new Error(result.error||"The queue could not be optimised");render(result);setStatus("Exact schedule ready. Inputs and grid values remain labelled by evidence state.",false)}catch(error){document.getElementById("oDecision").textContent="Blocked";setStatus(error.message,true)}finally{button.disabled=false}});
function download(name,type,text){var blob=new Blob([text],{type:type}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}
document.getElementById("downloadJson").addEventListener("click",function(){if(latest)download("workload-portfolio.json","application/json",JSON.stringify(latest,null,2))});document.getElementById("downloadCsv").addEventListener("click",function(){if(!latest)return;var head=["job_id","work_amount","work_unit","hardware","start","finish","facility_energy_kwh","cost","currency","carbon_kg"],rows=[head.join(",")];latest.assignments.forEach(function(item){rows.push(head.map(function(key){return JSON.stringify(item[key]??"")}).join(","))});download("workload-schedule.csv","text/csv",rows.join("\n"))});updateOverview();
})();</script></body></html>"""
    replacements = {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__THEME_CSS__": THEME_CSS,
        "__SHARED__": html.escape(shared),
        "__GB__": " selected" if context.market_key == "GB" else "",
        "__CAISO__": " selected" if context.market_key == "CAISO" else "",
        "__NYISO__": " selected" if context.market_key == "NYISO" else "",
        "__LOCATIONS__": location_options,
        "__SYMBOL__": html.escape(context.symbol),
        "__MARKET__": html.escape(context.market_name),
        "__LOCATION__": html.escape(context.location_name),
        "__MODE__": html.escape(context.signal_mode),
        "__POINTS__": str(len(context.series)),
        "__CARDS__": cards,
        "__TEMPLATE_CARDS__": template_cards,
        "__CURRENT_PRICE__": (
            f"{context.symbol}{current.price:.2f}/MWh"
            if current.price is not None else "Unavailable"
        ),
        "__CURRENT_CARBON__": (
            f"{current.carbon_intensity:.0f} gCO₂/kWh"
            if current.carbon_intensity is not None else "Unavailable"
        ),
        "__LOW_PRICE__": f"{context.symbol}{cheapest.price:.2f}/MWh",
        "__CLEAN_CARBON__": f"{cleanest.carbon_intensity:.0f} gCO₂/kWh",
        "__MARKET_KEY__": json.dumps(context.market_key)[1:-1],
        "__LOCATION_KEY__": json.dumps(context.location_key)[1:-1],
    }
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page
