"""Interactive capacity-aware AI workload queue."""
from __future__ import annotations

import html
import json
from urllib.parse import urlencode

from app import template
from app.markets import MarketContext
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav


_JOB_CARD = template.load("job_card.html")
_PAGE = template.load("workloads.html")


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
    return template.fill(_JOB_CARD, {
        "__JOB_ID__": html.escape(job['id']),
        "__STAGE_NAME__": html.escape(job['stage_name']),
        "__RUNTIME__": job['runtime'],
        "__POWER__": job['power'],
        "__DEADLINE__": job['deadline'],
        "__WORKFLOW_ID__": html.escape(job['workflow_id']),
        "__DEPENDS_ON__": html.escape(', '.join(job['depends_on'])),
        "__LABEL__": html.escape(job['label']),
        "__RUN_MODES__": run_modes,
        "__MODEL_ID__": html.escape(job['model_id']),
        "__MODEL_VERSION__": html.escape(job['model_version']),
        "__PRECISIONS__": precisions,
        "__COMPUTE_UNITS__": compute_units,
        "__HARDWARE__": html.escape(job['hardware']),
        "__AMOUNT__": job['amount'],
        "__UNITS__": units,
        "__CHECKPOINT_COUNT__": job.get('checkpoint_count', 1),
        "__PUE__": job['pue'],
        "__MEMORY_REQUIRED__": job['memory_required'],
        "__MEMORY_AVAILABLE__": job['memory_available'],
        "__QUALITY__": job['quality'],
        "__MINIMUM__": job['minimum'],
        "__UTILITY__": job['utility'],
    })


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
    shared = urlencode({"market": context.market_key, "location": context.location_key})
    navigation = product_nav("overview", {
        "overview": f"/?{shared}",
        "plan": f"/planner?{shared}",
        "hardware": f"/simulator?{shared}",
        "energy": f"/grid?{shared}",
    })
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
    replacements = {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__THEME_CSS__": THEME_CSS,
        "__PANEL_CSS__": PANEL_CSS,
        "__EXPAND_JS__": EXPAND_JS,
        "__PRODUCT_NAV__": navigation,
        "__PRICE_SCOPE__": html.escape(price_scope),
        "__CARBON_SCOPE__": html.escape(carbon_scope),
        "__SYMBOL__": html.escape(context.symbol),
        "__MARKET__": html.escape(context.market_name),
        "__LOCATION__": html.escape(context.location_name),
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
    return template.fill(_PAGE, replacements)
