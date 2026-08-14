"""Interactive capacity-aware AI workload queue."""
from __future__ import annotations

import html
import json
from urllib.parse import urlencode

from app.markets import MarketContext
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS


def _location_options(context: MarketContext) -> str:
    options = "".join(
        f'<option value="{html.escape(choice.key)}"'
        f'{" selected" if choice.key == context.location_key else ""}>'
        f'{html.escape(choice.name)} · {html.escape(choice.detail)}</option>'
        for choice in context.locations
    )
    if context.location_key not in {choice.key for choice in context.locations}:
        options = (
            f'<option value="{html.escape(context.location_key)}" selected>'
            f'Custom PNode · {html.escape(context.location_name)}</option>'
            + options
        )
    return options


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
      <div class="field-groups">
        <details class="field-group" open>
          <summary><h4>Identity &amp; workflow</h4></summary>
          <div class="fields">
            <label>Workflow ID<input data-field="workflow_id" value="{html.escape(job['workflow_id'])}"></label>
            <label>Stage name<input data-field="stage_name" value="{html.escape(job['stage_name'])}"></label>
            <label>Depends on stage IDs<input data-field="depends_on" value="{html.escape(', '.join(job['depends_on']))}" placeholder="stage-a, stage-b"></label>
            <label>Workload class<input data-field="label" value="{html.escape(job['label'])}"></label>
            <label>Run mode<select data-field="run_mode">{run_modes}</select></label>
          </div>
        </details>
        <details class="field-group" open>
          <summary><h4>Execution</h4></summary>
          <div class="fields">
            <label>Model / deployment<input data-field="model_id" value="{html.escape(job['model_id'])}"></label>
            <label>Model version<input data-field="model_version" value="{html.escape(job['model_version'])}"></label>
            <label>Precision<select data-field="precision">{precisions}</select></label>
            <label>Compute unit<select data-field="compute_unit">{compute_units}</select></label>
            <label>Execution hardware<input data-field="hardware" value="{html.escape(job['hardware'])}"></label>
            <label>Useful work<input data-field="work_amount" type="number" min="0.000001" step="any" value="{job['amount']}"></label>
            <label>Work unit<select data-field="work_unit">{units}</select></label>
            <label>Runtime · hours<input data-field="runtime_hours" type="number" min="0.000001" step="0.05" value="{job['runtime']}"></label>
            <label>Checkpoint chunks<input data-field="checkpoint_count" type="number" min="1" max="24" step="1" value="{job.get('checkpoint_count', 1)}"></label>
          </div>
        </details>
        <details class="field-group">
          <summary><h4>Resources</h4></summary>
          <div class="fields">
            <label>IT power · kW<input data-field="it_power_kw" type="number" min="0" step="0.001" value="{job['power']}"></label>
            <label>PUE<input data-field="pue" type="number" min="1" max="5" step="0.01" value="{job['pue']}"></label>
            <label>Memory required · GB<input data-field="memory_required_gb" type="number" min="0" step="0.1" value="{job['memory_required']}"></label>
            <label>Memory available · GB<input data-field="memory_available_gb" type="number" min="0" step="0.1" value="{job['memory_available']}"></label>
          </div>
        </details>
        <details class="field-group" open>
          <summary><h4>Quality &amp; schedule</h4></summary>
          <div class="fields">
            <label>Quality score · 0 to 1<input data-field="quality_score" type="number" min="0" max="1" step="0.01" value="{job['quality']}"></label>
            <label>Minimum quality<input data-field="minimum_quality" type="number" min="0" max="1" step="0.01" value="{job['minimum']}"></label>
            <label>Deadline · hours<input data-field="deadline_hours" type="number" min="0.01" step="0.5" value="{job['deadline']}"></label>
            <label>Operator utility<input data-field="utility" type="number" min="0.000001" step="0.5" value="{job['utility']}"></label>
            <label class="check"><input data-field="mandatory" type="checkbox" checked>Mandatory workload</label>
          </div>
        </details>
        <details class="field-group">
          <summary><h4>Evidence</h4></summary>
          <div class="fields">
            <label>Governed evidence profile<select data-field="evidence_profile_id"><option value="">Estimated / manual inputs</option></select></label>
            <label class="check"><input data-field="auto_evidence_profiles" type="checkbox">Compare every compatible governed profile</label>
            <label>Evidence state<select data-field="provenance"><option value="ESTIMATED">Estimated scenario</option><option value="MEASURED">Measured</option></select></label>
            <label>Evaluation suite<input data-field="evaluation_suite" value="scenario-eval"></label>
            <label>Suite version<input data-field="evaluation_version" value="1.0"></label>
          </div>
        </details>
      </div>
    </article>"""


def render(context: MarketContext) -> str:
    common = {
        "model_version": "scenario-1", "memory_available": 8.0,
        "pue": 1.0, "deadline": 12,
    }
    templates = {
        "generation": [
            {**common, "id": "train-prepare", "workflow_id": "generation-aware-training",
             "stage_name": "Prepare and validate training shard", "depends_on": [],
             "label": "Training data preparation", "run_mode": "training",
             "model_id": "training-data-pipeline", "precision": "fp32",
             "compute_unit": "cpu", "hardware": "CPU preparation pool scenario",
             "amount": 250000, "unit": "training_examples", "runtime": 0.5,
             "power": 5.0, "quality": 1.0, "memory_required": 64.0,
             "memory_available": 128.0, "minimum": 1.0, "utility": 2},
            {**common, "id": "train-accelerator", "workflow_id": "generation-aware-training",
             "stage_name": "Run accelerator-heavy training stage",
             "depends_on": ["train-prepare"], "label": "Model training",
             "run_mode": "training", "model_id": "quality-qualified-training-model",
             "precision": "bf16", "compute_unit": "gpu",
             "hardware": "50 kW accelerator allocation scenario",
             "amount": 4000, "unit": "optimizer_steps", "runtime": 2.0,
             "power": 50.0, "quality": 0.92, "memory_required": 320.0,
             "memory_available": 640.0, "minimum": 0.90, "utility": 10},
            {**common, "id": "train-evaluate", "workflow_id": "generation-aware-training",
             "stage_name": "Evaluate checkpoint and release evidence",
             "depends_on": ["train-accelerator"], "label": "Checkpoint evaluation",
             "run_mode": "evaluation", "model_id": "training-evaluation-suite",
             "precision": "fp16", "compute_unit": "gpu",
             "hardware": "Evaluation accelerator pool scenario",
             "amount": 10000, "unit": "samples", "runtime": 0.5,
             "power": 10.0, "quality": 0.92, "memory_required": 80.0,
             "memory_available": 160.0, "minimum": 0.90, "utility": 4},
        ],
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
    defaults = templates["generation"]
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
    source_defaults = (
        ("solar", "Solar", 120, 0.90, 0, 0, "dedicated_wire", True, True, False),
        ("wind", "Wind", 10, 0.85, 0, 0, "dedicated_wire", True, True, False),
        ("hydro", "Hydro", 0, 0.95, 5, 0, "dedicated_wire", True, True, True),
        ("nuclear", "Nuclear", 0, 0.98, 10, 0, "dedicated_wire", False, True, False),
        ("geothermal", "Geothermal", 0, 0.95, 20, 38, "dedicated_wire", True, False, False),
        ("biomass", "Biomass", 0, 0.90, 35, 230, "dedicated_wire", True, False, True),
        ("gas", "Gas", 0, 1.0, 70, 400, "onsite", False, False, True),
        ("coal", "Coal", 0, 1.0, 80, 900, "onsite", False, False, True),
        ("oil", "Oil", 0, 1.0, 100, 700, "onsite", False, False, True),
        ("other", "Other", 0, 0.90, 50, 300, "onsite", False, False, True),
    )
    source_rows = "".join(
        f'''<tr data-energy-source data-kind="{kind}" data-name="{name}"
        data-renewable="{str(renewable).lower()}" data-carbon-free="{str(carbon_free).lower()}"
        data-dispatchable="{str(dispatchable).lower()}"><td><span class="source-dot source-{kind}"></span><b>{name}</b><small>{"Renewable" if renewable else "Non-renewable"}</small></td>
        <td><input data-energy="capacity" aria-label="{name} capacity in kilowatts" type="number" min="0" step="0.1" value="{capacity:g}"></td>
        <td><input data-energy="confidence" aria-label="{name} forecast confidence" type="number" min="0" max="1" step="0.01" value="{confidence:g}"></td>
        <td><input data-energy="cost" aria-label="{name} marginal cost per megawatt-hour" type="number" step="0.01" value="{cost:g}"></td>
        <td><input data-energy="carbon" aria-label="{name} carbon intensity" type="number" min="0" step="1" value="{carbon:g}"></td>
        <td><select data-energy="delivery" aria-label="{name} delivery type">
        <option value="onsite"{" selected" if delivery == "onsite" else ""}>On site</option>
        <option value="dedicated_wire"{" selected" if delivery == "dedicated_wire" else ""}>Dedicated wire</option>
        <option value="contractual">Contractual only</option></select></td>
        <td><input data-energy="latitude" aria-label="{name} latitude" inputmode="decimal" placeholder="Optional"></td>
        <td><input data-energy="longitude" aria-label="{name} longitude" inputmode="decimal" placeholder="Optional"></td>
        <td><input data-energy="loss" aria-label="{name} delivery loss percent" type="number" min="0" max="99.9999" step="0.01" value="0"></td>
        <td><input data-energy="connection" aria-label="{name} grid connection ID" value="scenario-{kind}"></td></tr>'''
        for (kind, name, capacity, confidence, cost, carbon, delivery,
             renewable, carbon_free, dispatchable) in source_defaults
    )
    grid_timestamps = json.dumps([
        point.timestamp.isoformat() for point in context.series
    ])
    custom_node = "" if not context.allows_custom_node else """
    <label>Custom CAISO pricing node<div class="joined"><input id="customNode"
    placeholder="Exact CAISO PNode ID"><button id="loadNode" type="button">Load</button></div></label>"""
    price_scope = {
        "CAISO": "Pricing node",
        "NYISO": "NYISO zone",
    }.get(context.market_key, "GB national")
    carbon_scope = {
        "CAISO": "CAISO balancing area",
        "NYISO": "NYISO balancing area",
    }.get(
        context.market_key,
        "GB national" if context.location_key == "national" else "GB grid region",
    )
    native_resolution = 60 if context.market_key in {"CAISO", "NYISO"} else 30
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
color:var(--text-2);margin:0;max-width:780px}nav{display:inline-flex;gap:2px;margin-top:16px;overflow:auto;padding:3px;border-radius:11px;
background:color-mix(in srgb,var(--text) 5%,transparent)}
nav a{font-size:13px;font-weight:600;text-decoration:none;padding:6px 14px;border-radius:8px;
white-space:nowrap;color:var(--text-2);transition:background .15s ease,color .15s ease}
nav a:hover{color:var(--text)}
nav a.on{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.10)}.card,.job{background:var(--card);border-radius:18px;
box-shadow:var(--shadow)}.card{padding:22px 24px;margin-bottom:18px}.card h2{font-size:20px;margin:0 0 4px}
.note{color:var(--text-2);margin:0 0 17px}.eyebrow{color:var(--text-2);font-size:12px;
font-weight:600;letter-spacing:-0.005em}.signal{display:flex;gap:9px;flex-wrap:wrap;
margin:14px 0 0}.pill{padding:5px 9px;border-radius:999px;background:color-mix(in srgb,var(--blue) 9%,transparent);
color:var(--blue);font-size:11px}.warning{border:1px solid color-mix(in srgb,var(--orange) 40%,var(--sep));
background:color-mix(in srgb,var(--orange) 6%,var(--card));padding:13px 15px;border-radius:12px;
color:var(--orange);font-size:12px;margin-top:16px}.controls,.fields{display:grid;gap:13px}
.controls{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.fields{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.field-groups{display:flex;flex-direction:column;gap:2px}
.field-group{border-top:1px solid var(--sep);padding:12px 0}
.field-group:first-child{border-top:0;padding-top:0}
.field-group summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:7px;user-select:none}
.field-group summary::-webkit-details-marker{display:none}
.field-group summary::before{content:"";width:7px;height:7px;flex:none;border-right:1.6px solid var(--text-3);
border-bottom:1.6px solid var(--text-3);transform:rotate(-45deg);transition:transform .15s ease;margin-left:2px}
.field-group[open]>summary::before{transform:rotate(45deg)}
.field-group summary h4{margin:0;font-size:12.5px;font-weight:650;color:var(--text-2);letter-spacing:-0.005em}
.field-group summary:hover h4{color:var(--text)}
.field-group>.fields{margin-top:12px}
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
.ops-stat span{display:block;color:var(--text-2);font-size:11.5px;letter-spacing:-0.005em}
.ops-stat b{display:block;font-size:22px;letter-spacing:-.025em;margin-top:4px}.decision-chain{display:grid;
grid-template-columns:repeat(5,1fr);gap:1px;background:var(--sep);border-radius:12px;overflow:hidden}
.chain-step{position:relative;padding:12px 14px;background:var(--card)}.chain-step span{display:block;
color:var(--text-2);font-size:11px;letter-spacing:-0.005em}.chain-step b{display:block;
font-size:12.5px;margin-top:3px;font-weight:600}.placement-card{margin-top:22px}.section-anchor{color:var(--blue);font-size:12px;
font-weight:650;text-decoration:none}.section-anchor:hover{text-decoration:underline}
.metrics{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:1px;background:var(--sep);
border-radius:14px;overflow:hidden;margin:16px 0}.metric{background:var(--card);padding:15px}.metric span{display:block;
font-size:11.5px;color:var(--text-2);letter-spacing:-0.005em}.metric b{display:block;
font-size:24px;letter-spacing:-.03em;margin-top:3px}.result{display:none}.result.show{display:block}
.table-wrap{overflow:auto;border:1px solid var(--sep);border-radius:13px}table{width:100%;border-collapse:collapse;
min-width:950px}th,td{text-align:left;padding:10px 11px;border-bottom:1px solid var(--sep);white-space:nowrap}
th{color:var(--text-2);font-size:10px;text-transform:uppercase;letter-spacing:.055em}tbody tr:last-child td{border-bottom:0}
.status{min-height:20px;color:var(--text-2);font-size:12px}.status.error{color:var(--red)}.result-note{font-size:12px;
color:var(--text-2)}.provenance{font-size:11px;color:var(--text-2);border-top:1px solid var(--sep);
padding-top:14px;margin-top:17px}.empty{padding:28px;text-align:center;color:var(--text-2)}
.energy-layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(270px,.75fr);gap:16px;align-items:start}
.energy-table{min-width:1420px}.energy-table td:first-child{min-width:128px}.energy-table input,.energy-table select{width:100%;min-width:90px;padding:7px 8px}
.energy-table small{display:block;color:var(--text-3);font-size:9px;font-weight:500}.source-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--text-3)}
.source-solar{background:#ff9f0a}.source-wind{background:#5ac8fa}.source-hydro{background:#007aff}.source-nuclear{background:#af52de}.source-geothermal{background:#ff453a}.source-biomass{background:#34c759}.source-gas{background:#8e8e93}.source-coal{background:#3a3a3c}.source-oil{background:#a2845e}.source-other{background:#ff2d55}
.supply-side{display:grid;gap:12px;position:sticky;top:16px}.mini-controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}.chart-card{border:1px solid var(--sep);border-radius:14px;padding:14px;background:color-mix(in srgb,var(--card) 97%,var(--blue));min-width:0}
.chart-card h3{font-size:14px;margin:0 70px 2px 0}.chart-card .note{font-size:10px;margin-bottom:8px}.chart-card svg{height:190px}.result-charts{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0}.result-charts svg{height:220px}
.energy-callout{border:1px solid color-mix(in srgb,var(--green) 35%,var(--sep));background:color-mix(in srgb,var(--green) 6%,var(--card));padding:14px;border-radius:12px;color:var(--text-2);font-size:12px}.energy-callout b{display:block;color:var(--text);font-size:15px;margin-bottom:3px}
.badge{display:inline-flex;padding:3px 7px;border-radius:999px;background:color-mix(in srgb,var(--orange) 10%,transparent);color:var(--orange);font-size:9px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.source-results{margin-top:14px}.source-results table{min-width:720px}
.comparison{border:1px solid color-mix(in srgb,var(--blue) 32%,var(--sep));background:color-mix(in srgb,var(--blue) 5%,var(--card));border-radius:14px;padding:16px;margin:15px 0}.comparison h3{margin:0 0 4px;font-size:16px}.comparison .note{margin-bottom:12px}.comparison-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.comparison-stat{background:var(--card);border-radius:10px;padding:10px}.comparison-stat span{display:block;color:var(--text-2);font-size:10.5px;letter-spacing:-0.005em}.comparison-stat b{display:block;margin-top:3px;font-size:16px}
.site-policy{border:1px solid var(--sep);border-radius:14px;padding:16px;margin-bottom:16px}.site-policy h3{font-size:15px;margin:0 0 3px}.site-policy .note{font-size:11px;margin-bottom:12px}.joined{display:flex;gap:6px}.joined input{min-width:0;flex:1}.joined button{white-space:nowrap}.scope-note{margin:12px 0 0;color:var(--text-2);font-size:11px}.scope-note b{color:var(--text)}
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
.evidence-registry table{min-width:1050px}.evidence-state{display:inline-flex;padding:3px 7px;border-radius:999px;background:color-mix(in srgb,var(--green) 10%,transparent);color:var(--green);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}.evidence-empty{padding:22px;color:var(--text-2);text-align:center}.evidence-caution{color:var(--orange)}
.collector-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:12px}.collector-card{border:1px solid var(--sep);border-radius:12px;padding:13px}.collector-card b{display:block;margin:4px 0}.collector-card p{margin:0;color:var(--text-2);font-size:11px}.collector-card code{font-size:10px;color:var(--text-2)}
@media(max-width:980px){.fields{grid-template-columns:repeat(3,1fr)}.metrics,.ops-grid{grid-template-columns:repeat(3,1fr)}
.decision-chain{grid-template-columns:1fr 1fr}.energy-layout{grid-template-columns:1fr}.supply-side{position:static}.result-charts{grid-template-columns:1fr}.comparison-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:650px){body{padding-left:14px;padding-right:14px}h1{font-size:32px}.fields{grid-template-columns:1fr 1fr}
.metrics,.ops-grid{grid-template-columns:1fr 1fr}.ops-head,.queue-head{align-items:stretch;flex-direction:column}
.decision-chain{grid-template-columns:1fr}.job-head>div{align-items:flex-start;flex-direction:column}.job-head input{min-width:0;width:100%}}
__PANEL_CSS____THEME_CSS__</style></head><body><div class="wrap">__THEME_CONTROL__
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
<div class="actions"><label>Example workflow<select id="workflowTemplate"><option value="generation">Generation-aware AI training</option><option value="language">Language evaluation</option><option value="vision">Vision validation</option><option value="speech">Speech regression</option></select></label><label>Completion window<select id="timeframe"><option value="6">6 hours</option><option value="12" selected>12 hours</option><option value="24">24 hours</option><option value="48">48 hours</option></select></label><button id="loadTemplate" type="button">Load example</button><button id="addJob" type="button">Add workload</button><button id="optimise" class="primary" type="button">Optimise queue</button></div></div>
<div id="jobs">__CARDS__</div>__TEMPLATE_CARDS__<p class="status" id="status">Ready. Review the scenario values before optimising.</p></section>
<section class="card"><div class="queue-head"><div><span class="eyebrow">Operational preflight</span><h2>Quality, memory, SLA and capacity readiness</h2>
<p class="note">This gate runs before price or carbon ranking. An execution option that fails quality, memory or deadline cannot become cheaper by moving it to another hour.</p></div></div>
<div class="ops-grid"><div class="ops-stat"><span>Quality eligible</span><b id="rEligible">3 / 3</b></div>
<div class="ops-stat"><span>Workloads at risk</span><b id="rRisk">0</b></div><div class="ops-stat"><span>Candidate facility power</span><b id="rPower">0.075 kW</b></div>
<div class="ops-stat"><span>Capacity headroom</span><b id="rHeadroom">9.925 kW</b></div><div class="ops-stat"><span>Execution variants</span><b id="rVariants">3</b></div></div>
<p class="status" id="readinessIssues">All current scenario variants pass their declared quality, memory and deadline gates.</p></section>
<section class="card evidence-registry" id="evidenceRegistry"><div class="queue-head"><div><span class="eyebrow">Measured workload evidence</span><h2>Governed execution profiles</h2><p class="note">Three or more exact-fingerprint runs create a profile. Selecting one on a queue item replaces editable runtime, power, model and quality values with the server-side measurement.</p></div><div class="actions"><button id="runProbe" type="button">Verify local MLX</button><button id="refreshEvidence" type="button">Refresh evidence</button></div></div>
<div class="ops-grid"><div class="ops-stat"><span>Immutable observations</span><b id="eObservationCount">0</b></div><div class="ops-stat"><span>Ready profiles</span><b id="eProfileCount">0</b></div><div class="ops-stat"><span>Pending fingerprints</span><b id="ePendingCount">0</b></div><div class="ops-stat"><span>Minimum repeats</span><b>3</b></div><div class="ops-stat"><span>Registry state</span><b id="eRegistryState">Loading</b></div></div>
<div class="table-wrap"><table><thead><tr><th>Profile</th><th>Workload</th><th>Model</th><th>Device</th><th>Samples</th><th>Work rate</th><th>Average power</th><th>Energy method</th><th>Variation</th><th>Comparison scope</th></tr></thead><tbody id="evidenceProfiles"></tbody></table><p class="evidence-empty" id="evidenceEmpty">No schedulable measured profile yet. Run the benchmark collector three times with one exact fingerprint and a valid energy measurement.</p></div>
<div class="collector-list" id="evidenceCollectors"><div class="collector-card"><span class="evidence-state">Loading</span><b>Reference workload registry</b><p>Checking locally installed collectors.</p></div></div>
<p class="status" id="eProbeStatus">Performance probe not run in this session. A probe never creates energy evidence.</p>
<p class="scope-note">Apple subsystem estimates may optimise configurations on the same device only. Cross-device energy ranking requires a calibrated external meter. Runtime or Energy Impact alone never becomes watt-hours.</p></section>
<section class="card evidence-registry" id="inventoryCard"><div class="queue-head"><div><span class="eyebrow">Facility discovery</span><h2>Discovered fleet inventory</h2><p class="note">Read-only Redfish walks of operator-declared endpoints. Identity, installed memory and one instantaneous power reading can be MEASURED; throughput never is — calibration remains the only path to measured performance.</p></div><div class="actions"><button id="runScan" class="primary" type="button">⚡ Scan all hardware</button><button id="refreshInventory" type="button">Run discovery</button></div></div>
<div class="scan-panel" id="scanPanel" hidden><div class="scan-head"><b id="scanTitle">Scan complete</b><span id="scanSources"></span></div><div class="scan-grid" id="scanDevices"></div></div>
<div class="live-head"><h3>This host, live</h3><span class="live-dot" id="liveDot" data-state="connecting"></span><span class="live-state" id="liveState">Connecting</span><span class="live-note">Free capacity is the feasibility input: installed memory decides what could fit, available memory decides what fits now.</span></div>
<div class="live-grid" id="liveDevices"><p class="evidence-empty">Waiting for the first reading.</p></div>
<div class="ops-grid"><div class="ops-stat"><span>Snapshots recorded</span><b id="iSnapshotCount">0</b></div><div class="ops-stat"><span>Devices in latest</span><b id="iDeviceCount">0</b></div><div class="ops-stat"><span>Warnings in latest</span><b id="iWarningCount">0</b></div><div class="ops-stat"><span>Access tier</span><b>Read-only GET</b></div><div class="ops-stat"><span>Discovery state</span><b id="iState">Loading</b></div></div>
<div class="table-wrap"><table><thead><tr><th>Device</th><th>Identity</th><th>Memory</th><th>Live power</th><th>Power scope</th><th>Performance</th><th>Source</th><th>Fleet ID</th></tr></thead><tbody id="inventoryRows"></tbody></table><p class="evidence-empty" id="inventoryEmpty">No discovery snapshot yet.</p></div>
<p class="status" id="inventoryStatus">Discovery reads declared endpoints only; it never scans, never writes and never stores a raw serial number.</p></section>
<section class="card" id="energySupply"><div class="queue-head"><div><span class="eyebrow">Physical energy supply</span><h2>Generation, facility demand and storage</h2>
<p class="note">Define the power physically available to this site. The optimiser uses the confidence-adjusted residual after base facility demand, then schedules AI stages against renewable, carbon-free, storage and grid availability.</p></div><span class="badge">Estimated scenario</span></div>
<div class="energy-layout"><div class="table-wrap"><table class="energy-table"><thead><tr><th>Energy source</th><th>Capacity · kW</th><th>Confidence · 0–1</th><th>Cost / MWh</th><th>Carbon · gCO₂/kWh</th><th>Delivery</th><th>Origin latitude</th><th>Origin longitude</th><th>Delivery loss · %</th><th>Connection ID</th></tr></thead><tbody>__SOURCE_ROWS__</tbody></table></div>
<aside class="supply-side"><div class="mini-controls"><label>Base facility demand · kW<input id="baseLoad" type="number" min="0" step="0.1" value="60"></label><label>Solar peak · UTC hour<input id="solarPeak" type="number" min="0" max="23.5" step="0.5" value="18"></label><label>Daytime PUE<input id="dayPue" type="number" min="1" max="5" step="0.01" value="1.12"></label><label>Night PUE<input id="nightPue" type="number" min="1" max="5" step="0.01" value="1.08"></label><label>Battery capacity · kWh<input id="batteryCapacity" type="number" min="0" step="1" value="0"></label><label>Battery charge limit · kW<input id="batteryCharge" type="number" min="0" step="1" value="50"></label><label>Battery discharge limit · kW<input id="batteryDischarge" type="number" min="0" step="1" value="50"></label><label>Initial battery energy · kWh<input id="batteryInitial" type="number" min="0" step="1" value="0"></label><label>Round-trip efficiency<input id="batteryEfficiency" type="number" min="0.01" max="1" step="0.01" value="0.90"></label></div>
<div class="chart-card pnl" id="supplyPreview"><h3>Forecast generation</h3><p class="note">Click to expand, pan, zoom, inspect, hide series or export.</p><svg class="panel" id="supplySvg" viewBox="0 0 720 230" role="img" aria-label="Generation availability and base load forecast" data-inspector='{"kind":"line","series":[]}'></svg></div>
<div class="energy-callout"><b id="supplyHeadline">Calculating usable supply</b><span id="supplySummary">Generation profiles are estimated standard shapes, not plant telemetry.</span></div></aside></div>
<div class="warning">Contractual instruments are recorded but cannot physically serve the facility. Grid electricity remains the residual fallback. Replace these scenario profiles with operator, plant or balancing-authority forecasts before making a real dispatch claim.</div></section>
<section class="card placement-card" id="placementPolicy"><div class="queue-head"><div><span class="eyebrow">Placement constraints</span><h2>Facility, electricity price and carbon policy</h2>
<p class="note">These signals constrain where and when eligible AI work runs. They do not define the workload or its quality requirement.</p></div><a class="section-anchor" href="/grid?__SHARED__">Open full grid terminal</a></div>
<div class="site-policy"><h3>Exact physical facility</h3><p class="note">Enter any WGS84 coordinate pair. This identifies the facility precisely; the selected market feed below keeps its published national, regional, zonal or nodal scope.</p><div class="controls"><label>Site ID<input id="siteId" value="facility-scenario-1"></label><label>Site name<input id="siteName" value="AI facility scenario"></label><label>Latitude · −90 to 90<input id="siteLatitude" inputmode="decimal" placeholder="For example 51.5074"></label><label>Longitude · −180 to 180<input id="siteLongitude" inputmode="decimal" placeholder="For example -0.1278"></label><label>Grid connection / meter ID<input id="siteConnection" placeholder="Operator connection ID"></label><label>IANA time zone<input id="siteTimeZone" value="UTC" placeholder="Europe/London"></label></div><p class="scope-note" id="siteScope"><b>No exact coordinates supplied.</b> Scheduling currently uses the selected market location only.</p></div>
<div class="controls"><label>Power market<select id="market"><option value="GB"__GB__>Great Britain</option><optgroup label="United States"><option value="CAISO"__CAISO__>California ISO</option><option value="NYISO"__NYISO__>New York ISO</option><option value="MISO"__MISO__>Midcontinent ISO</option></optgroup></select></label>
<label>Grid-signal location<select id="location">__LOCATIONS__</select></label>__CUSTOM_NODE__<label>Total facility capacity · kW<input id="maxPower" type="number" min="0.000001" step="0.1" value="200"></label>
<label>Primary energy objective<select id="energyPriority"><option value="renewable">Maximise renewable match</option><option value="carbon_free">Maximise carbon-free match</option><option value="carbon">Minimise operational carbon</option><option value="cost">Minimise electricity cost</option></select></label>
<label>Total electricity cost cap · __SYMBOL__ (0 = off)<input id="maxCost" type="number" min="0" step="0.01" value="0"></label>
<label>Total operational carbon cap · kg (0 = off)<input id="maxCarbon" type="number" min="0" step="0.01" value="0"></label></div>
<div class="signal"><span class="pill">__MARKET__</span><span class="pill">__LOCATION__</span><span class="pill">Price: __PRICE_SCOPE__</span><span class="pill">Carbon: __CARBON_SCOPE__</span><span class="pill">__MODE__</span><span class="pill">__POINTS__ half-hours</span></div>
<div class="ops-grid"><div class="ops-stat"><span>First interval price</span><b>__CURRENT_PRICE__</b></div><div class="ops-stat"><span>First interval carbon</span><b>__CURRENT_CARBON__</b></div><div class="ops-stat"><span>Lowest price in window</span><b>__LOW_PRICE__</b></div><div class="ops-stat"><span>Cleanest interval</span><b>__CLEAN_CARBON__</b></div><div class="ops-stat"><span>Decision / native signal</span><b>30 / __NATIVE_RESOLUTION__ min</b></div></div></section>
<section class="card result" id="result"><div class="queue-head"><div><span class="eyebrow">Exact capacity schedule</span><h2>Recommended run plan</h2><p class="note" id="resultSummary"></p></div>
<div class="actions"><button id="downloadJson" type="button">Plan JSON</button><button id="downloadCsv" type="button">Schedule CSV</button></div></div>
<div class="metrics"><div class="metric"><span>Scheduled jobs</span><b id="mJobs">0</b></div><div class="metric"><span>Utility completed</span><b id="mUtility">0</b></div><div class="metric"><span>AI facility energy</span><b id="mEnergy">0</b></div><div class="metric"><span>Renewable match</span><b id="mRenewable">0%</b></div><div class="metric"><span>Carbon-free match</span><b id="mCarbonFree">0%</b></div><div class="metric"><span>Residual grid</span><b id="mGrid">0 kWh</b></div><div class="metric"><span>Battery delivered</span><b id="mBattery">0 kWh</b></div><div class="metric"><span>Forecast cost</span><b id="mCost">0</b></div><div class="metric"><span>Forecast carbon</span><b id="mCarbon">0</b></div><div class="metric"><span>Curtailed supply</span><b id="mCurtailed">0 kWh</b></div></div>
<div class="comparison"><h3>Why this schedule is better than running immediately</h3><p class="note" id="comparisonSummary">The optimiser will compare this plan with the same workflow started at the earliest capacity-feasible time.</p><div class="comparison-grid"><div class="comparison-stat"><span>Added scheduling delay</span><b id="cDelay">0 h</b></div><div class="comparison-stat"><span>Renewable uplift</span><b id="cRenewable">0 pp</b></div><div class="comparison-stat"><span>Grid avoided</span><b id="cGrid">0 kWh</b></div><div class="comparison-stat"><span>Carbon avoided</span><b id="cCarbon">0 kg</b></div><div class="comparison-stat"><span>Cost saved</span><b id="cCost">0</b></div><div class="comparison-stat"><span>Energy saved by PUE</span><b id="cEnergy">0 kWh</b></div></div></div>
<p class="scope-note" id="resultSite"></p>
<div class="result-charts"><div class="chart-card pnl" id="dispatchChart"><h3>Scheduled demand and source dispatch</h3><p class="note">Every half-hour allocation. Click for the complete interactive workbench.</p><svg class="panel" id="dispatchSvg" viewBox="0 0 720 250" role="img" aria-label="Scheduled demand and energy dispatch" data-inspector='{"kind":"line","series":[]}'></svg></div><div class="chart-card pnl" id="mixChart"><h3>AI energy by source</h3><p class="note">Physical energy attributed to scheduled AI work. Click to inspect or export.</p><svg class="panel" id="mixSvg" viewBox="0 0 720 250" role="img" aria-label="AI energy by source" data-inspector='{"kind":"line","series":[]}'></svg></div></div>
<div class="table-wrap"><table><thead><tr><th>Job</th><th>Stage</th><th>Model</th><th>Execution</th><th>Work</th><th>Start</th><th>Finish</th><th>Energy</th><th>Renewable</th><th>Carbon-free</th><th>Grid</th><th>Battery</th><th>Cost</th><th>Carbon</th><th>Evidence</th></tr></thead><tbody id="schedule"></tbody></table><div class="empty" id="noSchedule" hidden>No jobs were scheduled.</div></div>
<div class="source-results"><h3>Source accounting</h3><div class="table-wrap"><table><thead><tr><th>Source</th><th>Kind</th><th>Delivery</th><th>Origin</th><th>Distance</th><th>Connection</th><th>Declared loss</th><th>Base facility</th><th>AI workloads</th><th>Battery charging</th><th>Curtailed</th><th>Evidence</th></tr></thead><tbody id="sourceSchedule"></tbody></table></div></div>
<p class="provenance" id="provenance"></p></section>
</div><script>
(function(){"use strict";var latest=null,jobCounter=3,EVIDENCE_PROFILES={},GRID_TIMESTAMPS=__GRID_TIMESTAMPS__;
var SOURCE_COLOURS={solar:"#ff9f0a",wind:"#5ac8fa",hydro:"#007aff",nuclear:"#af52de",geothermal:"#ff453a",biomass:"#34c759",gas:"#8e8e93",coal:"#3a3a3c",oil:"#a2845e",other:"#ff2d55",grid:"#ff375f",battery:"#30d158",base:"#6e6e73",ai:"#0a84ff"};
function q(root,name){return root.querySelector('[data-field="'+name+'"]')}
function value(root,name){return q(root,name).value.trim()}
function number(root,name){var n=Number(value(root,name));if(!Number.isFinite(n))throw new Error(name+" must be a number");return n}
function inputNumber(id){var n=Number(document.getElementById(id).value);if(!Number.isFinite(n))throw new Error(id+" must be a number");return n}
function intervalHour(timestamp){var date=new Date(timestamp);return date.getUTCHours()+date.getUTCMinutes()/60}
function cyclicDistance(a,b){var d=Math.abs(a-b)%24;return Math.min(d,24-d)}
function profileFactor(kind,index,timestamp){var hour=intervalHour(timestamp),peak=inputNumber("solarPeak"),distance=cyclicDistance(hour,peak);
  if(kind==="solar")return distance>=6?0:Math.cos(distance*Math.PI/12);
  if(kind==="wind")return Math.max(.12,Math.min(.92,.52+.23*Math.sin(index*.67+1.1)+.10*Math.sin(index*.19)));
  if(kind==="hydro")return Math.max(.55,Math.min(.96,.76+.10*Math.sin(index*.31-1.2)));
  if(kind==="nuclear")return .95;
  if(kind==="geothermal")return Math.max(.78,.91+.025*Math.sin(index*.23));
  if(kind==="biomass")return Math.max(.55,.78+.08*Math.sin(index*.41+.8));
  if(kind==="other")return Math.max(.35,.70+.12*Math.sin(index*.37));
  return 1}
function energySourceRows(){return Array.from(document.querySelectorAll("[data-energy-source]"))}
function sourceSeries(root){var kind=root.dataset.kind,capacity=Number(root.querySelector('[data-energy="capacity"]').value);return GRID_TIMESTAMPS.map(function(stamp,index){return capacity*profileFactor(kind,index,stamp)})}
function energySources(){return energySourceRows().map(function(root){var latitudeText=root.querySelector('[data-energy="latitude"]').value.trim(),longitudeText=root.querySelector('[data-energy="longitude"]').value.trim(),lossPct=Number(root.querySelector('[data-energy="loss"]').value),item={source_id:root.dataset.kind,name:root.dataset.name,kind:root.dataset.kind,availability_kw:sourceSeries(root),cost_per_mwh:Number(root.querySelector('[data-energy="cost"]').value),carbon_g_per_kwh:Number(root.querySelector('[data-energy="carbon"]').value),confidence:Number(root.querySelector('[data-energy="confidence"]').value),renewable:root.dataset.renewable==="true",carbon_free:root.dataset.carbonFree==="true",delivery_type:root.querySelector('[data-energy="delivery"]').value,dispatchable:root.dataset.dispatchable==="true",grid_connection_id:root.querySelector('[data-energy="connection"]').value.trim(),delivery_loss_fraction:lossPct/100,provenance:"ESTIMATED standard profile"};if((latitudeText==="")!==(longitudeText===""))throw new Error(root.dataset.name+" needs both origin latitude and longitude");if(!Number.isFinite(lossPct)||lossPct<0||lossPct>=100)throw new Error(root.dataset.name+" delivery loss must be from 0% to below 100%");if(latitudeText!==""){item.latitude=Number(latitudeText);item.longitude=Number(longitudeText);if(!Number.isFinite(item.latitude)||item.latitude< -90||item.latitude>90)throw new Error(root.dataset.name+" latitude must be from -90 to 90");if(!Number.isFinite(item.longitude)||item.longitude< -180||item.longitude>180)throw new Error(root.dataset.name+" longitude must be from -180 to 180")}return item})}
function exactSite(){var latitudeText=document.getElementById("siteLatitude").value.trim(),longitudeText=document.getElementById("siteLongitude").value.trim();if((latitudeText==="")!==(longitudeText===""))throw new Error("The facility needs both latitude and longitude");if(latitudeText==="")return null;var latitude=Number(latitudeText),longitude=Number(longitudeText),siteId=document.getElementById("siteId").value.trim(),name=document.getElementById("siteName").value.trim(),timeZone=document.getElementById("siteTimeZone").value.trim();if(!Number.isFinite(latitude)||latitude< -90||latitude>90)throw new Error("Facility latitude must be from -90 to 90");if(!Number.isFinite(longitude)||longitude< -180||longitude>180)throw new Error("Facility longitude must be from -180 to 180");if(!siteId||!name||!timeZone)throw new Error("Exact facilities need a site ID, name and IANA time zone");return{site_id:siteId,name:name,latitude:latitude,longitude:longitude,grid_connection_id:document.getElementById("siteConnection").value.trim(),time_zone:timeZone}}
function updateSiteScope(){var node=document.getElementById("siteScope");try{var site=exactSite();if(!site){node.innerHTML="<b>No exact coordinates supplied.</b> Scheduling currently uses the selected market location only.";return}node.textContent="Exact physical site: "+site.latitude.toFixed(6)+", "+site.longitude.toFixed(6)+(site.grid_connection_id?" · connection "+site.grid_connection_id:"")+". Price scope: __PRICE_SCOPE__. Carbon scope: __CARBON_SCOPE__."}catch(error){node.textContent=error.message}}
function pueProfile(){var day=inputNumber("dayPue"),night=inputNumber("nightPue");return GRID_TIMESTAMPS.map(function(stamp){var hour=intervalHour(stamp);return hour>=7&&hour<20?day:night})}
function chartDetails(index){return new Intl.DateTimeFormat("en-GB",{dateStyle:"medium",timeStyle:"short",timeZone:"UTC"}).format(new Date(GRID_TIMESTAMPS[index]))+" UTC"}
function drawNumericChart(svg,series,options){options=options||{};var width=720,height=options.height||230,left=48,right=15,top=12,bottom=32,plotW=width-left-right,plotH=height-top-bottom,all=[];series.forEach(function(item){item.values.forEach(function(value){if(Number.isFinite(value))all.push(value)})});var max=Math.max(1,options.max||0,all.length?Math.max.apply(null,all):1),count=Math.max(2,series.reduce(function(value,item){return Math.max(value,item.values.length)},0)),markup="";for(var tick=0;tick<=4;tick+=1){var y=top+plotH*tick/4,value=max*(1-tick/4);markup+='<line class="p-gl" x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'"></line><text class="p-yt" x="'+(left-7)+'" y="'+(y+3)+'" text-anchor="end">'+value.toFixed(value<10?1:0)+'</text>'}series.forEach(function(item){var points=item.values.map(function(value,index){return(left+plotW*index/(count-1)).toFixed(2)+","+(top+plotH*(1-value/max)).toFixed(2)}).join(" ");markup+='<polyline class="p-line" style="--series:'+item.color+'" points="'+points+'"></polyline>'});markup+='<text class="p-xt" x="'+left+'" y="'+(height-8)+'">0 h</text><text class="p-xt" x="'+(width-right)+'" y="'+(height-8)+'" text-anchor="end">'+((count-1)*.5).toFixed(1)+' h</text><text class="p-key" x="16" y="'+(top+10)+'">'+(options.unit||"kW")+'</text>';svg.innerHTML=markup;var inspector={kind:options.kind||"line",xLabel:"Hours from horizon start",yLabel:options.label||"Power",ySuffix:" "+(options.unit||"kW"),precision:2,series:series.map(function(item){return{name:item.name,color:item.color,pointsOnly:!!item.pointsOnly,points:item.values.map(function(value,index){return[index*.5,value,options.details?options.details(index,item):chartDetails(index)]})}})};svg.dataset.inspector=JSON.stringify(inspector);svg.closest(".pnl").dispatchEvent(new Event("inspector:update"))}
function updateSupplyPreview(){var base=inputNumber("baseLoad"),rows=energySourceRows(),series=[];rows.forEach(function(root){var values=sourceSeries(root),capacity=Number(root.querySelector('[data-energy="capacity"]').value);if(capacity>0)series.push({name:root.dataset.name,color:SOURCE_COLOURS[root.dataset.kind],values:values})});series.push({name:"Base facility demand",color:SOURCE_COLOURS.base,values:GRID_TIMESTAMPS.map(function(){return base})});drawNumericChart(document.getElementById("supplySvg"),series,{label:"Available power",unit:"kW"});var physical=rows.filter(function(root){return root.querySelector('[data-energy="delivery"]').value!=="contractual"}),best=0,bestIndex=0;GRID_TIMESTAMPS.forEach(function(_,index){var firm=physical.reduce(function(total,root){var lossPct=Number(root.querySelector('[data-energy="loss"]').value);return total+sourceSeries(root)[index]*Number(root.querySelector('[data-energy="confidence"]').value)*(1-lossPct/100)},0),surplus=Math.max(0,firm-base);if(surplus>best){best=surplus;bestIndex=index}});document.getElementById("supplyHeadline").textContent="Peak usable delivered surplus: "+best.toFixed(1)+" kW";document.getElementById("supplySummary").textContent=(best?chartDetails(bestIndex)+". ":"")+"Confidence, declared delivery loss and "+base.toFixed(1)+" kW of base demand are applied before AI scheduling. Grid fallback is evaluated separately."}
function compatibleEvidenceProfiles(root){var workload=value(root,"label"),runMode=value(root,"run_mode"),workUnit=value(root,"work_unit");return Object.values(EVIDENCE_PROFILES).filter(function(profile){return profile.workload_class===workload&&profile.run_mode===runMode&&profile.work_unit===workUnit})}
function populateEvidenceSelectors(){document.querySelectorAll('[data-field="evidence_profile_id"]').forEach(function(select){var selected=select.value;select.replaceChildren();var manual=document.createElement("option");manual.value="";manual.textContent="Estimated / manual inputs";select.appendChild(manual);Object.values(EVIDENCE_PROFILES).forEach(function(profile){var option=document.createElement("option");option.value=profile.profile_id;option.textContent=profile.model_id+" · "+profile.device_key+" · "+profile.sample_count+" runs";select.appendChild(option)});if(EVIDENCE_PROFILES[selected])select.value=selected})}
function applyEvidenceProfile(root){var profile=EVIDENCE_PROFILES[value(root,"evidence_profile_id")];if(!profile)return;q(root,"auto_evidence_profiles").checked=false;var workload=value(root,"label"),runMode=value(root,"run_mode"),workUnit=value(root,"work_unit");if(profile.workload_class!==workload)throw new Error("Profile workload class "+profile.workload_class+" does not match "+workload);if(profile.run_mode!==runMode)throw new Error("Profile run mode "+profile.run_mode+" does not match "+runMode);if(profile.work_unit!==workUnit)throw new Error("Profile work unit "+profile.work_unit+" does not match "+workUnit);q(root,"model_id").value=profile.model_id;q(root,"model_version").value=profile.model_version;q(root,"precision").value=profile.precision;q(root,"compute_unit").value=profile.compute_unit;q(root,"hardware").value=profile.device_key+" ("+profile.compute_unit+")";q(root,"runtime_hours").value=number(root,"work_amount")/Number(profile.work_rate_per_second)/3600;q(root,"it_power_kw").value=Number(profile.average_it_power_watts)/1000;q(root,"memory_required_gb").value=Number(profile.peak_memory_mb)/1024;q(root,"quality_score").value=profile.quality_score;q(root,"provenance").value="MEASURED";q(root,"evaluation_suite").value=profile.quality_suite;q(root,"evaluation_version").value=profile.quality_suite_version;setStatus("Applied governed profile "+profile.profile_id+" from "+profile.sample_count+" immutable runs. "+(profile.cross_device_comparable?"External-meter energy permits cross-device comparison.":"Energy is restricted to same-device configuration comparison."),false);updateOverview()}
function renderEvidenceRegistry(payload){var summary=payload.summary||{},profiles=payload.profiles||[],body=document.getElementById("evidenceProfiles"),collectors=document.getElementById("evidenceCollectors");EVIDENCE_PROFILES={};profiles.forEach(function(profile){EVIDENCE_PROFILES[profile.profile_id]=profile});document.getElementById("eObservationCount").textContent=Number(summary.observation_count||0);document.getElementById("eProfileCount").textContent=Number(summary.profile_count||0);document.getElementById("ePendingCount").textContent=Number(summary.pending_fingerprint_count||0);document.getElementById("eRegistryState").textContent=profiles.length?"Ready":"Awaiting runs";body.replaceChildren();profiles.forEach(function(profile){var row=document.createElement("tr"),variation="Throughput ±"+(Number(profile.throughput_relative_mad)*100).toFixed(1)+"% · energy ±"+(Number(profile.energy_relative_mad)*100).toFixed(1)+"%";addCells(row,[profile.profile_id,profile.workload_class+" · "+profile.run_mode,profile.model_id+" · "+profile.model_version+" · "+profile.precision,profile.device_key+" · "+profile.compute_unit,String(profile.sample_count),Number(profile.work_rate_per_second).toFixed(2)+" "+profile.work_unit+"/s",Number(profile.average_it_power_watts).toFixed(2)+" W",profile.energy_method+" · "+profile.energy_scope,variation,profile.cross_device_comparable?"Cross-device":"Same device only"]);body.appendChild(row)});collectors.replaceChildren();(payload.collectors||[]).forEach(function(item){var card=document.createElement("div"),state=document.createElement("span"),name=document.createElement("b"),detail=document.createElement("p"),version=document.createElement("code");card.className="collector-card";state.className="evidence-state";state.textContent=item.status==="runner_ready"?"Runner ready":item.status;name.textContent=item.name;detail.textContent=item.item_count+" public evaluation items · "+item.quality_metric+" · requires pinned model, valid energy and three repeats.";version.textContent=item.evaluation_suite_version;card.append(state,name,detail,version);collectors.appendChild(card)});document.getElementById("evidenceEmpty").hidden=profiles.length>0;populateEvidenceSelectors();updateOverview()}
async function loadEvidenceProfiles(){document.getElementById("eRegistryState").textContent="Loading";try{var response=await fetch("/api/v1/evidence/profiles",{cache:"no-store"}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"Evidence registry unavailable");renderEvidenceRegistry(payload)}catch(error){document.getElementById("eRegistryState").textContent="Unavailable";document.getElementById("evidenceEmpty").hidden=false;document.getElementById("evidenceEmpty").textContent=error.message}}
async function runEvidenceProbe(){var button=document.getElementById("runProbe"),status=document.getElementById("eProbeStatus");button.disabled=true;status.textContent="Running a short local MLX performance probe…";try{var response=await fetch("/api/v1/evidence/probe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({matrix_size:256,iterations:5})}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"MLX probe failed");var probe=payload.probe;status.textContent="MLX execution verified: "+Number(probe.operations_per_second).toLocaleString("en-GB",{maximumFractionDigits:0})+" operations/s. Performance only; no task quality or watt-hours, so no scheduler profile was created."}catch(error){status.textContent=error.message}finally{button.disabled=false}}
function tag(text){var s=document.createElement("span");s.className="tag tag-"+String(text||"unavailable").toLowerCase();s.textContent=text||"UNAVAILABLE";return s}
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
function bindRemove(root){root.querySelector(".remove").addEventListener("click",function(){if(document.querySelectorAll("[data-job]").length===1){setStatus("At least one workload is required.",true);return}root.remove();updateOverview()});q(root,"evidence_profile_id").addEventListener("change",function(){try{applyEvidenceProfile(root)}catch(error){setStatus(error.message,true)}});q(root,"auto_evidence_profiles").addEventListener("change",function(){if(this.checked){q(root,"evidence_profile_id").value="";var count=compatibleEvidenceProfiles(root).length;setStatus(count?"The optimiser will compare "+count+" compatible governed profiles for this workload.":"No compatible governed profile is available for this workload.",count===0)}updateOverview()});root.addEventListener("input",updateOverview);root.addEventListener("change",updateOverview)}
document.querySelectorAll("[data-job]").forEach(bindRemove);
function applyTimeframe(){var hours=document.getElementById("timeframe").value;document.querySelectorAll("[data-job]").forEach(function(root){q(root,"deadline_hours").value=hours});updateOverview()}
document.getElementById("timeframe").addEventListener("change",applyTimeframe);
document.getElementById("loadTemplate").addEventListener("click",function(){var key=document.getElementById("workflowTemplate").value,source=document.getElementById("template-"+key),fragment=source.content.cloneNode(true),jobs=document.getElementById("jobs");jobs.replaceChildren(fragment);document.querySelectorAll("[data-job]").forEach(bindRemove);populateEvidenceSelectors();jobCounter=document.querySelectorAll("[data-job]").length;applyTimeframe();setStatus("Example workflow loaded. Review its estimated execution and quality values before optimising.",false)});
function updateOverview(){
  var roots=Array.from(document.querySelectorAll("[data-job]")),deadlines=roots.map(function(root){return Number(q(root,"deadline_hours").value)}).filter(Number.isFinite),mandatory=roots.filter(function(root){return q(root,"mandatory").checked}).length,measured=0,eligible=0,variantCount=0,risks=[],facilityPower=0;
  roots.forEach(function(root){
    var id=q(root,"job_id").value||"Unnamed workload",minimum=Number(q(root,"minimum_quality").value),deadline=Number(q(root,"deadline_hours").value),pue=Number(q(root,"pue").value),auto=q(root,"auto_evidence_profiles").checked,profiles=auto?compatibleEvidenceProfiles(root):[],issues=[];
    if(auto){
      variantCount+=profiles.length;
      if(profiles.length)measured+=1;else issues.push("no compatible measured profile");
      var workAmount=Number(q(root,"work_amount").value),qualified=profiles.filter(function(profile){var runtime=workAmount/Number(profile.work_rate_per_second)/3600;return Number.isFinite(runtime)&&Number(profile.quality_score)>=minimum&&runtime<=deadline});
      if(profiles.length&&!qualified.length)issues.push("no measured profile meets quality and deadline");
      if(qualified.length){eligible+=1;var leastPower=Math.min.apply(null,qualified.map(function(profile){return Number(profile.average_it_power_watts)/1000}));if(Number.isFinite(leastPower)&&Number.isFinite(pue))facilityPower+=leastPower*pue}
    }else{
      variantCount+=1;if(value(root,"evidence_profile_id")||q(root,"provenance").value==="MEASURED")measured+=1;
      var quality=Number(q(root,"quality_score").value),runtime=Number(q(root,"runtime_hours").value),required=Number(q(root,"memory_required_gb").value),available=Number(q(root,"memory_available_gb").value),power=Number(q(root,"it_power_kw").value);
      if(quality<minimum)issues.push("quality");if(required>available)issues.push("memory");if(runtime>deadline)issues.push("deadline");if(!issues.length)eligible+=1;if(Number.isFinite(power)&&Number.isFinite(pue))facilityPower+=power*pue;
    }
    if(issues.length)risks.push(id+": "+issues.join(", "));
  });
  var capacity=Number(document.getElementById("maxPower").value),base=Number(document.getElementById("baseLoad").value),headroom=capacity-base-facilityPower;
  document.getElementById("oQueued").textContent=roots.length;document.getElementById("oMandatory").textContent=mandatory;document.getElementById("oMeasured").textContent=measured+" / "+roots.length;document.getElementById("oDeadline").textContent=deadlines.length?(Math.min.apply(null,deadlines)+"–"+Math.max.apply(null,deadlines)+" h"):"Unavailable";document.getElementById("rEligible").textContent=eligible+" / "+roots.length;document.getElementById("rRisk").textContent=risks.length;document.getElementById("rPower").textContent=facilityPower.toFixed(3)+" kW AI + "+base.toFixed(1)+" kW base";document.getElementById("rHeadroom").textContent=(Number.isFinite(headroom)?headroom.toFixed(3):"Unavailable")+" kW";document.getElementById("rVariants").textContent=variantCount;var issueNode=document.getElementById("readinessIssues");issueNode.textContent=risks.length?risks.join(" · "):(headroom<0?"Declared base and candidate power exceed facility capacity.":"Every workload has at least one quality, memory, deadline and facility candidate.");issueNode.className="status"+((risks.length||headroom<0)?" error":"")}
document.getElementById("addJob").addEventListener("click",function(){jobCounter+=1;var source=document.querySelector("[data-job]"),copy=source.cloneNode(true);q(copy,"job_id").value="workload-"+jobCounter;q(copy,"label").value="New workload";copy.querySelector(".eyebrow").textContent="Queue item "+jobCounter;bindRemove(copy);document.getElementById("jobs").appendChild(copy);updateOverview()});
function navigate(){var market=document.getElementById("market").value,location=document.getElementById("location").value;if(market!=="__MARKET_KEY__")location=market==="CAISO"?"sp15":market==="NYISO"?"nyc":"national";window.location.href="/?market="+encodeURIComponent(market)+"&location="+encodeURIComponent(location)}
document.getElementById("market").addEventListener("change",navigate);document.getElementById("location").addEventListener("change",navigate);
var loadNode=document.getElementById("loadNode");if(loadNode)loadNode.addEventListener("click",function(){var node=document.getElementById("customNode").value.trim();if(!node){setStatus("Enter an exact CAISO pricing node ID.",true);return}window.location.href="/?market=CAISO&location="+encodeURIComponent(node)});
function collect(){var jobs=[];document.querySelectorAll("[data-job]").forEach(function(root,index){var id=value(root,"job_id"),provenance=value(root,"provenance"),dependencies=value(root,"depends_on").split(",").map(function(item){return item.trim()}).filter(Boolean);if(!id)throw new Error("Every workload needs a job ID");jobs.push({job_id:id,workflow_id:value(root,"workflow_id"),stage_name:value(root,"stage_name"),depends_on:dependencies,workload_class:value(root,"label"),run_mode:value(root,"run_mode"),deadline_hours:number(root,"deadline_hours"),work_amount:number(root,"work_amount"),work_unit:value(root,"work_unit"),utility:number(root,"utility"),minimum_quality:number(root,"minimum_quality"),mandatory:q(root,"mandatory").checked,require_measured_quality:provenance==="MEASURED",variants:[{candidate_key:id+"-variant-"+(index+1),model_id:value(root,"model_id"),model_version:value(root,"model_version"),precision:value(root,"precision"),compute_unit:value(root,"compute_unit"),hardware:value(root,"hardware"),runtime_hours:number(root,"runtime_hours"),it_power_kw:number(root,"it_power_kw"),pue:number(root,"pue"),memory_required_gb:number(root,"memory_required_gb"),memory_available_gb:number(root,"memory_available_gb"),quality_score:number(root,"quality_score"),quality_provenance:provenance,evaluation_suite:value(root,"evaluation_suite"),evaluation_version:value(root,"evaluation_version"),hardware_provenance:provenance}]})});var capacity=inputNumber("maxPower"),base=inputNumber("baseLoad"),facility={max_power_kw:capacity,base_load_kw:base,pue_profile:pueProfile(),energy_sources:energySources(),energy_priority:document.getElementById("energyPriority").value},batteryCapacity=inputNumber("batteryCapacity"),cap=inputNumber("maxCost"),carbon=inputNumber("maxCarbon");if(base>=capacity)throw new Error("Base facility demand must be below total facility capacity");if(batteryCapacity>0){var initial=inputNumber("batteryInitial");if(initial>batteryCapacity)throw new Error("Initial battery energy cannot exceed battery capacity");facility.battery={capacity_kwh:batteryCapacity,max_charge_kw:inputNumber("batteryCharge"),max_discharge_kw:inputNumber("batteryDischarge"),initial_energy_kwh:initial,round_trip_efficiency:inputNumber("batteryEfficiency")}}if(cap>0)facility.max_total_cost=cap;if(carbon>0)facility.max_total_carbon_kg=carbon;return{market:"__MARKET_KEY__",location:"__LOCATION_KEY__",facility:facility,jobs:jobs}}
var collectQueue=collect;collect=function(){var payload=collectQueue(),roots=Array.from(document.querySelectorAll("[data-job]")),site=exactSite();payload.jobs.forEach(function(job,index){var count=Number(q(roots[index],"checkpoint_count").value),evidenceProfileId=value(roots[index],"evidence_profile_id"),autoEvidence=q(roots[index],"auto_evidence_profiles").checked;if(!Number.isInteger(count)||count<1||count>24)throw new Error("Checkpoint chunks must be a whole number from 1 to 24");job.checkpointable=count>1;job.checkpoint_count=count;if(autoEvidence){job.auto_evidence_profiles=true;job.require_measured_quality=true;job.variants=[]}else if(evidenceProfileId)job.variants[0].evidence_profile_id=evidenceProfileId});if(site)payload.facility.site=site;return payload};
function setStatus(message,error){var node=document.getElementById("status");node.textContent=message;node.className="status"+(error?" error":"")}
function money(value,currency){try{return new Intl.NumberFormat("en-GB",{style:"currency",currency:currency,maximumFractionDigits:3}).format(value)}catch(error){return Number(value).toFixed(3)+" "+currency}}
function when(value){return new Intl.DateTimeFormat("en-GB",{dateStyle:"medium",timeStyle:"short",timeZone:"UTC"}).format(new Date(value))+" UTC"}
function evidence(notes){var hit=(notes||[]).find(function(note){return note.indexOf("Quality provenance ")===0});return hit?hit.replace("Quality provenance ",""):"Unavailable"}
function assignmentEvidence(item){return item.evidence_profile_id?"MEASURED · "+item.evidence_sample_count+" runs · "+item.energy_method:item.quality_provenance}
function percent(value){return Number(value||0).toFixed(1)+"%"}
function renderComparison(result){var baseline=result.earliest_run_counterfactual;if(!baseline)return;var delta=baseline.optimisation_delta||{},completion=Number(delta.workflow_completion_delay_hours||0),uplift=Number(delta.renewable_match_uplift_points||0),grid=Number(delta.grid_energy_avoided_kwh||0),carbon=Number(delta.carbon_avoided_kg||0),cost=Number(delta.cost_saved||0),energy=Number(delta.energy_saved_kwh||0);document.getElementById("cDelay").textContent=completion.toFixed(1)+" h";document.getElementById("cRenewable").textContent=uplift.toFixed(1)+" pp";document.getElementById("cGrid").textContent=grid.toFixed(2)+" kWh";document.getElementById("cCarbon").textContent=carbon.toFixed(3)+" kg";document.getElementById("cCost").textContent=money(cost,result.market.currency);document.getElementById("cEnergy").textContent=energy.toFixed(2)+" kWh";var current=result.energy_dispatch,currentMatch=current?Number(current.ai_renewable_match_pct):0,baselineMatch=Number(baseline.renewable_match_pct||0),verb=completion>0?"Moving completion by "+completion.toFixed(1)+" hours":"Resequencing within the same completion time";document.getElementById("comparisonSummary").textContent=verb+" changes renewable matching from "+baselineMatch.toFixed(1)+"% to "+currentMatch.toFixed(1)+"%, avoids "+grid.toFixed(2)+" kWh of residual grid electricity and "+carbon.toFixed(3)+" kg operational carbon under the declared scenario. Negative savings mean the selected primary objective accepted that trade-off."}
function addCells(row,values){values.forEach(function(text){var cell=document.createElement("td");cell.textContent=text;row.appendChild(cell)})}
function drawMixChart(dispatch){var svg=document.getElementById("mixSvg"),sources=(dispatch.sources||[]).filter(function(source){return Number(source.ai_kwh)>0}),width=720,height=250,left=48,right=16,top=12,bottom=48,plotW=width-left-right,plotH=height-top-bottom,max=Math.max(1,sources.reduce(function(value,source){return Math.max(value,Number(source.ai_kwh))},0)),markup="";for(var tick=0;tick<=4;tick+=1){var y=top+plotH*tick/4,value=max*(1-tick/4);markup+='<line class="p-gl" x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'"></line><text class="p-yt" x="'+(left-7)+'" y="'+(y+3)+'" text-anchor="end">'+value.toFixed(value<10?1:0)+'</text>'}sources.forEach(function(source,index){var slot=plotW/Math.max(1,sources.length),barW=Math.min(54,slot*.62),x=left+slot*index+(slot-barW)/2,h=plotH*Number(source.ai_kwh)/max,y=top+plotH-h,color=SOURCE_COLOURS[source.kind]||SOURCE_COLOURS.other;markup+='<rect class="p-fill" style="--series:'+color+';opacity:.72" x="'+x+'" y="'+y+'" width="'+barW+'" height="'+h+'" rx="4"></rect><text class="p-xt" x="'+(x+barW/2)+'" y="'+(height-25)+'" text-anchor="middle">'+source.name.slice(0,10)+'</text>'});markup+='<text class="p-key" x="16" y="22">kWh</text>';svg.innerHTML=markup;svg.dataset.inspector=JSON.stringify({kind:"scatter",xLabel:"Source",yLabel:"AI energy",ySuffix:" kWh",precision:3,pointLabel:"Energy source",series:sources.map(function(source,index){return{name:source.name,color:SOURCE_COLOURS[source.kind]||SOURCE_COLOURS.other,points:[[index,Number(source.ai_kwh),source.name+" · "+source.delivery_type+" · "+source.provenance]]}})});svg.closest(".pnl").dispatchEvent(new Event("inspector:update"))}
function renderEnergy(dispatch){var sourceBody=document.getElementById("sourceSchedule");sourceBody.replaceChildren();if(!dispatch){["mRenewable","mCarbonFree"].forEach(function(id){document.getElementById(id).textContent="Unavailable"});["mGrid","mBattery","mCurtailed"].forEach(function(id){document.getElementById(id).textContent="Unavailable"});return}document.getElementById("mRenewable").textContent=percent(dispatch.ai_renewable_match_pct);document.getElementById("mCarbonFree").textContent=percent(dispatch.ai_carbon_free_match_pct);document.getElementById("mGrid").textContent=Number(dispatch.ai_grid_kwh).toFixed(3)+" kWh";document.getElementById("mBattery").textContent=Number(dispatch.ai_battery_kwh).toFixed(3)+" kWh";document.getElementById("mCurtailed").textContent=Number(dispatch.curtailed_kwh).toFixed(3)+" kWh";(dispatch.sources||[]).forEach(function(source){var row=document.createElement("tr"),origin=source.latitude==null?"Not supplied":Number(source.latitude).toFixed(6)+", "+Number(source.longitude).toFixed(6),distance=source.distance_to_site_km==null?"Not calculated":Number(source.distance_to_site_km).toFixed(2)+" km",loss=(Number(source.delivery_loss_fraction)*100).toFixed(2)+"% · "+Number(source.delivery_loss_kwh).toFixed(3)+" kWh";addCells(row,[source.name,source.kind,source.delivery_type,origin,distance,source.grid_connection_id||"Not supplied",loss,Number(source.base_kwh).toFixed(3)+" kWh",Number(source.ai_kwh).toFixed(3)+" kWh",Number(source.battery_charge_input_kwh).toFixed(3)+" kWh",Number(source.curtailed_kwh).toFixed(3)+" kWh",source.provenance]);sourceBody.appendChild(row)});var intervals=dispatch.intervals||[],series=[{name:"Base facility demand",color:SOURCE_COLOURS.base,values:intervals.map(function(row){return Number(row.base_load_kw)})},{name:"AI facility demand",color:SOURCE_COLOURS.ai,values:intervals.map(function(row){return Number(row.ai_load_kw)})}];(dispatch.sources||[]).forEach(function(source){var values=intervals.map(function(row){return Number((row.source_ai_kwh||{})[source.source_id]||0)*2});if(values.some(function(value){return value>0}))series.push({name:source.name+" to AI",color:SOURCE_COLOURS[source.kind]||SOURCE_COLOURS.other,values:values})});drawNumericChart(document.getElementById("dispatchSvg"),series,{label:"Facility and dispatched power",unit:"kW",height:250,details:function(index){return intervals[index]?when(intervals[index].timestamp):chartDetails(index)}});drawMixChart(dispatch)}
function render(result){latest=result;var box=document.getElementById("result"),body=document.getElementById("schedule");box.classList.add("show");body.replaceChildren();(result.assignments||[]).forEach(function(item){var tr=document.createElement("tr");addCells(tr,[item.job_id,item.stage_name,item.model_id+" · "+item.model_version,item.compute_unit+" · "+item.precision,Number(item.work_amount).toLocaleString("en-GB")+" "+item.work_unit,when(item.start),when(item.finish),Number(item.facility_energy_kwh).toFixed(3)+" kWh",percent(item.renewable_match_pct),percent(item.carbon_free_match_pct),Number(item.grid_kwh||0).toFixed(3)+" kWh",Number(item.battery_kwh||0).toFixed(3)+" kWh",money(item.cost,result.market.currency),Number(item.carbon_kg).toFixed(3)+" kg",assignmentEvidence(item)]);body.appendChild(tr)});document.getElementById("noSchedule").hidden=result.assignments.length>0;document.getElementById("mJobs").textContent=result.assignments.length;document.getElementById("mUtility").textContent=Number(result.completed_utility).toFixed(1);document.getElementById("mEnergy").textContent=Number(result.total_energy_kwh).toFixed(3)+" kWh";document.getElementById("mCost").textContent=money(result.total_cost,result.market.currency);document.getElementById("mCarbon").textContent=Number(result.total_carbon_kg).toFixed(3)+" kg";renderEnergy(result.energy_dispatch);document.getElementById("oDecision").textContent="Scheduled";var work=Object.entries(result.completed_work).map(function(entry){return Number(entry[1]).toLocaleString("en-GB")+" "+entry[0]}).join(", "),dispatch=result.energy_dispatch,match=dispatch?" Renewable match "+percent(dispatch.ai_renewable_match_pct)+", carbon-free match "+percent(dispatch.ai_carbon_free_match_pct)+", residual grid "+Number(dispatch.ai_grid_kwh).toFixed(2)+" kWh.":"",spatial=result.spatial_precision||{},site=spatial.facility_site;document.getElementById("resultSummary").textContent=work+(result.unscheduled_job_ids.length?". Unscheduled: "+result.unscheduled_job_ids.join(", "):". Every requested job was scheduled.")+match;document.getElementById("resultSite").textContent=site?"Exact physical facility "+site.name+" at "+Number(site.latitude).toFixed(6)+", "+Number(site.longitude).toFixed(6)+". Price signal: "+spatial.price_signal_scope+". Carbon signal: "+spatial.carbon_signal_scope+".":"No exact facility coordinates were supplied. The schedule uses the selected market location; price and carbon retain their named provider scope.";document.getElementById("provenance").textContent=result.signal_mode+". "+result.market.provenance+" Algorithm: "+result.algorithm+". Exact search considered "+result.combinations_considered+" complete schedules from an upper bound of "+result.search_space_upper_bound+". Physical supply uses confidence-adjusted scenario profiles, declared delivery losses and base load before flexible AI work. Forecast values require realised outturn scoring before any savings claim.";box.scrollIntoView({behavior:"smooth",block:"start"})}
document.getElementById("optimise").addEventListener("click",async function(){var button=this;try{var payload=collect();button.disabled=true;document.getElementById("oDecision").textContent="Optimising";setStatus("Optimising every legal queue placement…",false);var response=await fetch("/api/v1/portfolio?market="+encodeURIComponent(payload.market)+"&location="+encodeURIComponent(payload.location),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),result=await response.json();if(!response.ok)throw new Error(result.error||"The queue could not be optimised");render(result);setStatus("Exact schedule ready. Inputs and grid values remain labelled by evidence state.",false)}catch(error){document.getElementById("oDecision").textContent="Blocked";setStatus(error.message,true)}finally{button.disabled=false}});
var renderSchedule=render;render=function(result){renderSchedule(result);renderComparison(result)};
function download(name,type,text){var blob=new Blob([text],{type:type}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}
document.getElementById("downloadJson").addEventListener("click",function(){if(latest)download("workload-portfolio.json","application/json",JSON.stringify(latest,null,2))});document.getElementById("downloadCsv").addEventListener("click",function(){if(!latest)return;var head=["job_id","workflow_id","stage_name","hardware","start","finish","facility_energy_kwh","renewable_match_pct","carbon_free_match_pct","grid_kwh","battery_kwh","cost","carbon_kg"],rows=[head.join(",")];latest.assignments.forEach(function(item){rows.push(head.map(function(key){return JSON.stringify(item[key]??"")}).join(","))});download("workload-schedule.csv","text/csv",rows.join("\n"))});
document.querySelectorAll("#energySupply input,#energySupply select").forEach(function(control){control.addEventListener("input",function(){try{updateSupplyPreview();updateOverview()}catch(error){document.getElementById("supplyHeadline").textContent="Complete the energy input";document.getElementById("supplySummary").textContent=error.message}});control.addEventListener("change",function(){try{updateSupplyPreview();updateOverview()}catch(error){document.getElementById("supplyHeadline").textContent="Check the energy input";document.getElementById("supplySummary").textContent=error.message}})});document.querySelectorAll("#siteId,#siteName,#siteLatitude,#siteLongitude,#siteConnection,#siteTimeZone").forEach(function(control){control.addEventListener("input",updateSiteScope);control.addEventListener("change",updateSiteScope)});document.getElementById("refreshEvidence").addEventListener("click",loadEvidenceProfiles);document.getElementById("runProbe").addEventListener("click",runEvidenceProbe);document.getElementById("refreshInventory").addEventListener("click",runDiscovery);document.getElementById("runScan").addEventListener("click",runScan);startTelemetry();document.getElementById("maxPower").addEventListener("input",updateOverview);updateSupplyPreview();updateSiteScope();updateOverview();loadEvidenceProfiles();loadInventory();
})();</script><script>__EXPAND_JS__</script></body></html>"""
    replacements = {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__THEME_CSS__": THEME_CSS,
        "__PANEL_CSS__": PANEL_CSS,
        "__EXPAND_JS__": EXPAND_JS,
        "__SHARED__": html.escape(shared),
        "__GB__": " selected" if context.market_key == "GB" else "",
        "__CAISO__": " selected" if context.market_key == "CAISO" else "",
        "__NYISO__": " selected" if context.market_key == "NYISO" else "",
        "__MISO__": " selected" if context.market_key == "MISO" else "",
        "__LOCATIONS__": location_options,
        "__CUSTOM_NODE__": custom_node,
        "__PRICE_SCOPE__": html.escape(price_scope),
        "__CARBON_SCOPE__": html.escape(carbon_scope),
        "__NATIVE_RESOLUTION__": str(native_resolution),
        "__SYMBOL__": html.escape(context.symbol),
        "__MARKET__": html.escape(context.market_name),
        "__LOCATION__": html.escape(context.location_name),
        "__MODE__": html.escape(context.signal_mode),
        "__POINTS__": str(len(context.series)),
        "__CARDS__": cards,
        "__TEMPLATE_CARDS__": template_cards,
        "__SOURCE_ROWS__": source_rows,
        "__GRID_TIMESTAMPS__": grid_timestamps,
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
