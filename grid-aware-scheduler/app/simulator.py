"""
Page 2 — the model-on-hardware simulator.

Pick a model, pick hardware, pick how many, and see how it would run: time,
power, energy, and whether it even fits. Then that energy meets the grid
signal from page 1 and becomes cost and carbon.

Everything is precomputed server-side and embedded, so every control is
instant and nothing round-trips. The combination space is small enough that
this is cheaper than an API: a dozen devices by a handful of counts by two
task types.

**Every number is labelled with where it came from.** A simulator showing an
H100 nobody owns, running a model nobody ran, at a throughput nobody measured,
is only honest if it says so — so SPEC, ESTIMATED and SIMULATED are on screen,
not buried in a docstring.
"""
from __future__ import annotations

import argparse
import html
import json
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.gb import GBAdapter
from core.grid import PERIOD_HOURS
from core.workload import MODELS, Job, Task, estimate, memory_required_gb
from hardware import catalog
from hardware.base import Fleet, Group, Provenance

OUT = Path(__file__).resolve().parent / "build" / "simulator.html"

COUNTS = [1, 2, 4, 8, 16, 32, 64]

#: Representative sizes. Training on a token budget roughly matching a
#: fine-tune rather than a from-scratch run; inference on a batch of work.
TOKENS = {Task.TRAINING: 1e9, Task.INFERENCE: 5e7}
PROMPT_TOKENS = 2e6


def build_matrix() -> dict:
    """Every (model, task, device, count) combination, precomputed."""
    out: dict = {}
    for mkey, model in MODELS.items():
        out[mkey] = {}
        for task in (Task.TRAINING, Task.INFERENCE):
            job = Job(name=f"{model.name} {task.value}", model=model, task=task,
                      tokens=TOKENS[task],
                      prompt_tokens=PROMPT_TOKENS if task is Task.INFERENCE else 0.0)
            need = memory_required_gb(job)
            rows = {}
            for dkey, device in catalog.CATALOG.items():
                per_count = {}
                for n in COUNTS:
                    fleet = Fleet.of(device, n)
                    est = estimate(job, fleet)
                    per_count[n] = {
                        "hours": round(est.runtime_hours, 4),
                        "kwh": round(est.energy_kwh, 3),
                        "kw": round(est.average_power_kw, 3),
                        "fits": est.fits,
                        "mem": round(fleet.total_memory_gb, 1),
                        "scaling": round(est.scaling_efficiency, 4),
                    }
                per_count["meta"] = {
                    "name": device.name, "vendor": device.vendor,
                    "prov": device.provenance.value, "tdp": device.tdp_watts,
                    "tflops": device.peak_tflops_bf16, "mfu": device.mfu,
                    "bw": device.memory_bandwidth_gbs, "mem1": device.memory_gb,
                    "link": device.interconnect.value, "source": device.source,
                }
                rows[dkey] = per_count
            out[mkey][task.value] = {"need": round(need, 1), "devices": rows,
                                     "tokens": TOKENS[task]}
    return out


def _grid_context(days: int = 2) -> dict:
    """Current grid prices, so energy can be priced honestly at both ends."""
    try:
        end = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        series = GBAdapter().get_data(end - timedelta(days=days), end)
        prices = [p.price for p in series if p.price is not None]
        carbon = [p.carbon_intensity for p in series if p.carbon_intensity is not None]
        if not prices or not carbon:
            raise ValueError("no grid data")
        window = max(1, int(4 / PERIOD_HOURS))
        cheap = min(sum(prices[i:i + window]) / window
                    for i in range(max(len(prices) - window + 1, 1)))
        clean = min(sum(carbon[i:i + window]) / window
                    for i in range(max(len(carbon) - window + 1, 1)))
        return {"ok": True,
                "price_now": prices[-1], "price_cheap": cheap,
                "price_mean": sum(prices) / len(prices),
                "carbon_now": carbon[-1], "carbon_clean": clean,
                "carbon_mean": sum(carbon) / len(carbon),
                "from": series[0].timestamp.strftime("%d %b"),
                "to": series[-1].timestamp.strftime("%d %b %Y")}
    except Exception as exc:  # offline, API down — the page still works
        return {"ok": False, "error": str(exc)}


def render(matrix: dict, grid: dict) -> str:
    models = "".join(
        f'<option value="{k}">{html.escape(m.name)} · {m.params_b:g}B</option>'
        for k, m in MODELS.items())
    devices = "".join(
        f'<option value="{k}">{html.escape(d.vendor)} {html.escape(d.name)}</option>'
        for k, d in catalog.CATALOG.items())
    counts = "".join(f'<option value="{n}">{n}</option>' for n in COUNTS)

    grid_note = (
        f"Priced against live GB data, {html.escape(grid['from'])}–{html.escape(grid['to'])}."
        if grid.get("ok") else
        "Grid data unavailable — energy is shown without cost or carbon.")

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
    --text-2:rgba(235,235,245,.60); --text-3:rgba(235,235,245,.30);
    --sep:rgba(84,84,88,.65);
    --blue:#0A84FF; --green:#2A9D48; --orange:#E08A2E; --red:#E2554A;
    --shadow:none;
  }}
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:0 24px 72px;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto}}
header{{padding:56px 0 28px}}
h1{{margin:0 0 6px;font-size:40px;line-height:1.08;font-weight:700;letter-spacing:-.022em}}
.sub{{color:var(--text-2);font-size:17px;margin:0}}
nav{{margin-top:16px;display:flex;gap:8px}}
nav a{{font-size:13px;font-weight:550;text-decoration:none;padding:6px 14px;
border-radius:980px;color:var(--text-2);background:color-mix(in srgb,var(--text) 5%,transparent)}}
nav a.on{{background:var(--blue);color:#fff}}
.card{{background:var(--card);border-radius:18px;padding:22px 24px;box-shadow:var(--shadow);
margin-bottom:18px}}
.card>h2{{margin:0 0 4px;font-size:20px;font-weight:640;letter-spacing:-.015em}}
.card>.note{{margin:0 0 18px;color:var(--text-2);font-size:14px}}
.controls{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}}
.ctl{{display:flex;flex-direction:column;gap:6px}}
.ctl label{{font-size:12px;color:var(--text-2);font-weight:510}}
.ctl select{{font:inherit;font-size:14px;padding:9px 11px;border-radius:10px;
border:1px solid var(--sep);background:var(--card);color:var(--text);cursor:pointer}}
.seg{{display:inline-flex;gap:3px;background:color-mix(in srgb,var(--text) 5%,transparent);
padding:3px;border-radius:980px}}
.seg button{{font:inherit;font-size:13px;font-weight:550;padding:7px 16px;border:0;
border-radius:980px;background:transparent;color:var(--text-2);cursor:pointer}}
.seg button.on{{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.10)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
background:var(--sep);border-radius:14px;overflow:hidden;margin-top:18px}}
.tile{{background:var(--card);padding:16px 18px}}
.tile-label{{font-size:12px;color:var(--text-2)}}
.tile-value{{font-size:28px;font-weight:630;letter-spacing:-.02em;margin:4px 0 2px;
font-variant-numeric:tabular-nums}}
.tile-sub{{font-size:12px;color:var(--text-2)}}
.prov{{display:inline-block;font-size:10px;font-weight:640;letter-spacing:.05em;
padding:2px 7px;border-radius:5px;vertical-align:2px;margin-left:6px}}
.prov.SPEC{{background:color-mix(in srgb,var(--blue) 14%,transparent);color:var(--blue)}}
.prov.ESTIMATED{{background:color-mix(in srgb,var(--orange) 16%,transparent);color:var(--orange)}}
.prov.SIMULATED{{background:color-mix(in srgb,var(--text) 8%,transparent);color:var(--text-2)}}
.warn{{margin-top:16px;padding:13px 16px;border-radius:12px;font-size:14px;
background:color-mix(in srgb,var(--red) 10%,transparent);color:var(--red)}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px;
font-variant-numeric:tabular-nums}}
th,td{{text-align:right;padding:9px 10px;border-bottom:1px solid var(--sep)}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--text-2);font-weight:510;font-size:12px}}
tr.best td{{background:color-mix(in srgb,var(--green) 8%,transparent)}}
tr.nofit td{{opacity:.45}}
.bar{{height:6px;border-radius:3px;background:var(--blue);display:block}}
.foot{{color:var(--text-2);font-size:13px;margin-top:26px}}
.assum{{margin:14px 0 0;padding-left:18px;color:var(--text-2);font-size:13px}}
.assum li{{margin:3px 0}}
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
  <p class="note">{grid_note}</p>
  <div class="controls">
    <div class="ctl"><label for="model">Model</label><select id="model">{models}</select></div>
    <div class="ctl"><label for="device">Hardware</label><select id="device">{devices}</select></div>
    <div class="ctl"><label for="count">How many</label><select id="count">{counts}</select></div>
    <div class="ctl"><label>Task</label>
      <span class="seg" id="task">
        <button type="button" data-t="training" class="on">Training</button>
        <button type="button" data-t="inference">Inference</button>
      </span>
    </div>
  </div>
  <div class="tiles" id="tiles"></div>
  <div id="warn"></div>
  <ul class="assum" id="assum"></ul>
</section>

<section class="card">
  <h2>Every option, ranked by energy</h2>
  <p class="note">Same job, same count, every device in the catalogue. Greyed rows don't fit in memory.</p>
  <div style="overflow-x:auto"><table id="cmp">
    <thead><tr><th>Device</th><th>Runtime</th><th>Power</th><th>Energy</th>
    <th>Memory</th><th>Cost</th><th>CO₂</th></tr></thead>
    <tbody></tbody>
  </table></div>
</section>

<section class="card">
  <h2>Does adding hardware help?</h2>
  <p class="note">The same job on the selected device at every fleet size.</p>
  <div style="overflow-x:auto"><table id="scale">
    <thead><tr><th>Fleet</th><th>Runtime</th><th>Energy</th><th>Scaling</th><th>Memory</th></tr></thead>
    <tbody></tbody>
  </table></div>
</section>

<p class="foot" id="foot"></p>

</div>
<script>
var M = {json.dumps(matrix)};
var GRID = {json.dumps(grid)};
var COUNTS = {json.dumps(COUNTS)};
var state = {{ model: Object.keys(M)[0], task: "training",
               device: Object.keys(M[Object.keys(M)[0]]["training"].devices)[0], count: 8 }};

function n(v, d) {{ return v.toLocaleString("en-GB", {{minimumFractionDigits:d, maximumFractionDigits:d}}); }}
function dur(h) {{
  if (!isFinite(h)) return "—";
  if (h < 1) return n(h * 60, 0) + " min";
  if (h < 48) return n(h, 1) + " h";
  return n(h / 24, 1) + " days";
}}
function money(kwh, pMWh) {{ return kwh * pMWh / 1000; }}
function co2kg(kwh, g) {{ return kwh * g / 1000; }}

function current() {{
  var t = M[state.model][state.task];
  return {{ t: t, d: t.devices[state.device], r: t.devices[state.device][state.count] }};
}}

function render() {{
  var c = current(), meta = c.d.meta, r = c.r;

  var cost = GRID.ok ? money(r.kwh, GRID.price_cheap) : null;
  var costNow = GRID.ok ? money(r.kwh, GRID.price_now) : null;
  var carb = GRID.ok ? co2kg(r.kwh, GRID.carbon_clean) : null;

  document.getElementById("tiles").innerHTML =
    tile("Runtime", dur(r.hours), n(r.kw,1) + " kW draw") +
    tile("Energy", n(r.kwh,0) + " kWh", "over the whole run") +
    tile("Cost", cost === null ? "—" : "£" + n(cost,2),
         costNow === null ? "" : "vs £" + n(costNow,2) + " at current price") +
    tile("Carbon", carb === null ? "—" : n(carb,1) + " kg",
         "cleanest window") +
    tile("Provenance", meta.prov, meta.vendor + " " + meta.name, meta.prov);

  var fits = r.fits;
  document.getElementById("warn").innerHTML = fits ? "" :
    '<div class="warn"><b>Does not fit.</b> This needs about ' + n(c.t.need,0) +
    ' GB and ' + state.count + '\\u00d7 ' + meta.name + ' provides ' + n(r.mem,0) +
    ' GB. Runtime below assumes it did fit, so treat it as the compute cost only.</div>';

  document.getElementById("assum").innerHTML = [
    state.task === "training"
      ? "Training FLOPs \\u2248 6 \\u00d7 parameters \\u00d7 tokens."
      : "Decode is memory-bandwidth-bound: tokens/sec \\u2248 bandwidth \\u00f7 model bytes.",
    meta.name + " assumed to reach " + n(meta.mfu*100,0) + "% of its " +
      n(meta.tflops,0) + " TFLOPS peak, not peak itself.",
    state.count > 1
      ? "Scaling " + n(r.scaling*100,0) + "% of linear over " + meta.link +
        " \\u2014 communication cost only; pipeline bubbles and stragglers are not modelled, so this is optimistic."
      : "Single device \\u2014 no scaling loss.",
    "Source: " + meta.source
  ].map(function (s) {{ return "<li>" + s + "</li>"; }}).join("");

  // --- comparison across devices
  var rows = Object.keys(c.t.devices).map(function (k) {{
    var d = c.t.devices[k], rr = d[state.count];
    return {{ key: k, meta: d.meta, r: rr }};
  }}).sort(function (a, b) {{
    if (a.r.fits !== b.r.fits) return a.r.fits ? -1 : 1;
    return a.r.kwh - b.r.kwh;
  }});
  var best = rows.find(function (x) {{ return x.r.fits; }});
  document.querySelector("#cmp tbody").innerHTML = rows.map(function (x) {{
    var cls = (!x.r.fits ? "nofit" : (best && x.key === best.key ? "best" : ""));
    return '<tr class="' + cls + '"><td>' + x.meta.vendor + " " + x.meta.name +
      '<span class="prov ' + x.meta.prov + '">' + x.meta.prov + "</span></td><td>" +
      dur(x.r.hours) + "</td><td>" + n(x.r.kw,1) + " kW</td><td><b>" + n(x.r.kwh,0) +
      " kWh</b></td><td>" + n(x.r.mem,0) + " GB</td><td>" +
      (GRID.ok ? "£" + n(money(x.r.kwh, GRID.price_cheap),2) : "—") + "</td><td>" +
      (GRID.ok ? n(co2kg(x.r.kwh, GRID.carbon_clean),1) + " kg" : "—") + "</td></tr>";
  }}).join("");

  // --- scaling table
  document.querySelector("#scale tbody").innerHTML = COUNTS.map(function (k) {{
    var rr = c.d[k];
    return "<tr" + (k === state.count ? ' class="best"' : "") + "><td>" + k +
      "\\u00d7 " + meta.name + "</td><td>" + dur(rr.hours) + "</td><td>" +
      n(rr.kwh,0) + " kWh</td><td>" + n(rr.scaling*100,0) + "%</td><td>" +
      n(rr.mem,0) + " GB</td></tr>";
  }}).join("");

  document.getElementById("foot").textContent =
    "Job: " + (c.t.tokens/1e9 >= 1 ? n(c.t.tokens/1e9,1) + "B" : n(c.t.tokens/1e6,0) + "M") +
    " tokens. Needs ~" + n(c.t.need,0) + " GB. " +
    (GRID.ok ? "Cost and carbon use the cheapest and cleanest 4-hour windows in the live GB data."
             : "Grid data unavailable.");
}}

function tile(label, value, sub, prov) {{
  return '<div class="tile"><div class="tile-label">' + label + '</div><div class="tile-value">' +
    value + (prov ? '' : '') + '</div><div class="tile-sub">' + (sub || "") + "</div></div>";
}}

["model","device","count"].forEach(function (id) {{
  document.getElementById(id).addEventListener("change", function (e) {{
    state[id] = id === "count" ? +e.target.value : e.target.value;
    render();
  }});
}});
document.querySelectorAll("#task button").forEach(function (b) {{
  b.addEventListener("click", function () {{
    document.querySelectorAll("#task button").forEach(function (o) {{ o.classList.remove("on"); }});
    b.classList.add("on"); state.task = b.dataset.t; render();
  }});
}});
document.getElementById("count").value = String(state.count);
render();
</script>
</body>
</html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the model simulator.")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    print("Building simulation matrix …")
    matrix = build_matrix()
    print("Fetching grid context …")
    grid = _grid_context()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(matrix, grid), encoding="utf-8")
    combos = sum(len(t["devices"]) * len(COUNTS) for m in matrix.values() for t in m.values())
    print(f"{combos:,} configurations → {OUT}")
    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
