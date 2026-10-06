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

from app import template
from app.markets import MarketContext
from app.panels import EXPAND_JS, PANEL_CSS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav
from app.simulator import COUNTS, device_specs, model_specs
from core import models as model_catalog


def _options(context: MarketContext) -> str:
    return "".join(
        f'<option value="{html.escape(x.key)}"'
        f'{" selected" if x.key == context.location_key else ""}>'
        f'{html.escape(x.name)} · {html.escape(x.detail)}</option>'
        for x in context.locations
    )


_PAGE = template.load("planner.html")


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
    navigation = product_nav("plan", {
        "overview": operations_href,
        "plan": "/planner?" + urlencode({
            "market": context.market_key,
            "location": context.location_key,
        }),
        "hardware": simulator_href,
        "energy": grid_href,
    })
    custom_node = "" if not context.allows_custom_node else """
      <div class="ctl"><label for="customNode">Custom CAISO PNode</label>
        <div class="joined"><input id="customNode" placeholder="Enter exact node ID">
        <button type="button" id="loadNode">Load</button></div></div>"""

    return template.fill(_PAGE, {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__PANEL_CSS__": PANEL_CSS,
        "__THEME_CSS__": THEME_CSS,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__NAVIGATION__": navigation,
        "__SIGNAL_MODE__": html.escape(context.signal_mode),
        "__MARKET_NAME__": html.escape(context.market_name),
        "__LOCATION_NAME__": html.escape(context.location_name),
        "__POINTS_COUNT__": len(points),
        "__GB_SELECTED__": " selected" if context.market_key == "GB" else "",
        "__CAISO_SELECTED__": " selected" if context.market_key == "CAISO" else "",
        "__NYISO_SELECTED__": " selected" if context.market_key == "NYISO" else "",
        "__MISO_SELECTED__": " selected" if context.market_key == "MISO" else "",
        "__ERCOT_SELECTED__": " selected" if context.market_key == "ERCOT" else "",
        "__LOCATION_OPTIONS__": _options(context),
        "__CUSTOM_NODE__": custom_node,
        "__MODEL_OPTIONS__": model_options,
        "__PRECISION_OPTIONS__": precision_options,
        "__COUNT_OPTIONS__": count_options,
        "__SYMBOL__": html.escape(context.symbol),
        "__PROVENANCE__": html.escape(context.provenance),
        "__CARBON_LABEL__": html.escape(context.carbon_label),
        "__DEVICES_JSON__": json.dumps(devices),
        "__MODELS_JSON__": json.dumps(models),
        "__POINTS_JSON__": json.dumps(points),
        "__BYTES_PER_PARAM_JSON__": json.dumps(model_catalog.BYTES_PER_PARAM),
        "__SYMBOL_JSON__": json.dumps(context.symbol),
        "__PRICE_LABEL__": html.escape(context.price_label),
        "__CURRENCY_JSON__": json.dumps(context.currency),
        "__MARKET_KEY_JSON__": json.dumps(context.market_key),
        "__LOCATION_KEY_JSON__": json.dumps(context.location_key),
        "__SIGNAL_MODE_JSON__": json.dumps(context.signal_mode),
        "__PRICE_LABEL_JSON__": json.dumps(context.price_label),
        "__CARBON_LABEL_JSON__": json.dumps(context.carbon_label),
        "__PROVENANCE_JSON__": json.dumps(context.provenance),
        "__CURRENT_MARKET__": current_market,
        "__EXPAND_JS__": EXPAND_JS,
    })
