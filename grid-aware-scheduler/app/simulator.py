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
Apple, where no vendor figures for GPU throughput or package power exist —
except where `hardware.resolve` has better: the M2 is MEASURED and its larger
siblings are DERIVED from that measurement.
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
from hardware import catalogue
from hardware.derive import MEASURED_M2
from hardware.resolve import resolve
from app import template
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav

OUT = Path(__file__).resolve().parent / "build" / "simulator.html"

COUNTS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


#: The committed M2 measurement, so every clone shows the same evidence.
MEASURED = {MEASURED_M2.chip_key: {
    "achieved_gflops": MEASURED_M2.gemm_fp16_gflops,
    "bandwidth_gbs": MEASURED_M2.memory_bandwidth_gbs,
}}


def device_specs() -> dict:
    specs = {}
    for k, d in catalogue.CATALOGUE.items():
        spec = {
            "name": d.name, "vendor": d.vendor, "kind": d.kind,
            "tflops": d.peak_tflops_bf16, "mfu": d.mfu,
            "mem": d.memory_gb, "bw": d.memory_bandwidth_gbs,
            "tdp": d.tdp_watts, "idle": d.idle_watts,
            "link": d.interconnect.value, "prov": d.provenance.value,
            "catprov": d.provenance.value, "source": d.source,
        }
        # A measured or derived figure outranks the catalogue's. Achieved
        # bandwidth replaces the spec bus for decode; peak and utilisation
        # stay as the catalogue has them, because dense GEMM is a ceiling,
        # not what a transformer reaches.
        resolved = resolve(k, measured=MEASURED, catalogue_device=d)
        if resolved.achieved_gflops.known:
            spec["prov"] = resolved.achieved_gflops.provenance
            spec["evidence"] = resolved.summary()
        if resolved.bandwidth_gbs.known:
            spec["bw"] = resolved.bandwidth_gbs.value
        specs[k] = spec
    return specs


def model_specs() -> dict:
    out = {}
    for k, m in model_catalog.CATALOGUE.items():
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


_PAGE = template.load("simulator.html")


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
    navigation = product_nav("hardware", {
        "overview": operations_href,
        "plan": planner_href,
        "hardware": f"/simulator?{query}",
        "energy": grid_href,
    })
    grid_controls = "" if not grid_locations else f"""
    <div class="ctl"><label for="marketSelect">Power market</label><select id="marketSelect">
      <option value="GB"{" selected" if market_key == "GB" else ""}>Great Britain</option>
      <optgroup label="United States">
        <option value="CAISO"{" selected" if market_key == "CAISO" else ""}>California ISO</option>
        <option value="NYISO"{" selected" if market_key == "NYISO" else ""}>New York ISO</option><option value="MISO"{" selected" if market_key == "MISO" else ""}>Midcontinent ISO</option><option value="ERCOT"{" selected" if market_key == "ERCOT" else ""}>ERCOT (Texas)</option>
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

    return template.fill(_PAGE, {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__PANEL_CSS__": PANEL_CSS,
        "__THEME_CSS__": THEME_CSS,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__NAVIGATION__": navigation,
        "__NOTE__": note,
        "__DETECTED_BANNER__": detected_banner,
        "__GRID_CONTROLS__": grid_controls,
        "__CUSTOM_NODE__": custom_node,
        "__MODEL_OPTIONS__": model_opts,
        "__PRECISION_OPTIONS__": prec_opts,
        "__DEVICE_OPTIONS__": device_opts,
        "__COUNT_OPTIONS__": count_opts,
        "__SITE_OPTIONS__": site_opts,
        "__CAPACITY_OPTIONS__": cap_opts,
        "__DEVICES_JSON__": json.dumps(devices),
        "__MODELS_JSON__": json.dumps(models),
        "__GRID_JSON__": json.dumps(grid),
        "__SITES_JSON__": json.dumps(sites),
        "__COUNTS_JSON__": json.dumps(COUNTS),
        "__BYTES_PER_PARAM_JSON__": json.dumps(model_catalog.BYTES_PER_PARAM),
        "__LOCAL_DEFAULT_JSON__": json.dumps(local_default),
        "__LOCAL_DEFAULT_COUNT__": local_default_count,
        "__MARKET_KEY_JSON__": json.dumps(market_key),
        "__EXPAND_JS__": EXPAND_JS,
    })


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
    print(f"{len(model_catalog.CATALOGUE)} models × {len(catalogue.CATALOGUE)} devices "
          f"× {len(COUNTS)} fleet sizes × 6 precisions, computed live → {OUT}")
    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
