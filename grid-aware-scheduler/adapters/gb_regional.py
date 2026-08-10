"""
GB regional grid signal — location-aware carbon intensity.

Location matters far more than it first looks. GB is split into 18 grid
regions, and their carbon intensity diverges enormously in real time: a
measurement taken while writing this had North Scotland at 0 gCO2/kWh (96%
wind) against East Midlands at 131. Same country, same instant, same market.

One honest limit, and it shapes what the UI may claim:

    In GB, location changes CARBON but not PRICE.

GB runs a single national wholesale price — there is one Market Index Price
for the whole country, so moving a job from London to Scotland changes its
emissions and nothing about its bill. Locational price variation is real, but
it lives in nodal markets like CAISO and ERCOT, where thousands of pricing
nodes settle separately. So a location control in GB drives carbon and
weather; when the CAISO/ERCOT adapters land it will drive price too. Do not
present a GB map as though it varied price.

Endpoints used (all public, keyless, api.carbonintensity.org.uk):
    regional                                  all 18 regions, current
    regional/postcode/{outcode}               region for a postcode
    regional/intensity/{from}/fw48h/regionid/{id}   48h forward forecast
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_NGT_PATH = Path(__file__).resolve().parents[2] / "National-Grid-Tool"
_NGT_PATH = Path(os.environ.get("NATIONAL_GRID_TOOL_PATH", _DEFAULT_NGT_PATH))
if str(_NGT_PATH) not in sys.path:
    sys.path.insert(0, str(_NGT_PATH))

from sources.carbon_intensity.client import CarbonIntensityClient  # noqa: E402

#: The Carbon Intensity API's own banding. Kept as published rather than
#: rebucketed, so the UI reports the operator's judgement, not ours.
INDEX_ORDER = ["very low", "low", "moderate", "high", "very high"]

# Carbon Intensity API region identifiers. The first fourteen are the
# distribution regions returned by the live regional endpoint. National
# aggregates are intentionally excluded from a location selector.
REGIONS: dict[str, tuple[int, str]] = {
    "north-scotland": (1, "North Scotland"),
    "south-scotland": (2, "South Scotland"),
    "north-west-england": (3, "North West England"),
    "north-east-england": (4, "North East England"),
    "yorkshire": (5, "Yorkshire"),
    "north-wales-merseyside": (6, "North Wales and Merseyside"),
    "south-wales": (7, "South Wales"),
    "west-midlands": (8, "West Midlands"),
    "east-midlands": (9, "East Midlands"),
    "east-england": (10, "East England"),
    "south-west-england": (11, "South West England"),
    "south-england": (12, "South England"),
    "london": (13, "London"),
    "south-east-england": (14, "South East England"),
}


@dataclass
class RegionSignal:
    """One GB grid region at one moment."""

    region_id: int
    name: str
    carbon_forecast: float | None
    index: str | None
    generation_mix: dict[str, float] = field(default_factory=dict)

    @property
    def renewable_pct(self) -> float:
        """Wind + solar + hydro. Biomass and nuclear are excluded on purpose:
        both are low-carbon but neither is weather-driven, and this figure
        exists to be read against the weather panel."""
        return sum(self.generation_mix.get(f, 0.0) for f in ("wind", "solar", "hydro"))

    @property
    def top_sources(self) -> list[tuple[str, float]]:
        return sorted(self.generation_mix.items(), key=lambda kv: -kv[1])[:3]


@dataclass
class RegionForecastPoint:
    timestamp: datetime
    carbon_forecast: float | None
    index: str | None


def _mix(entry: dict) -> dict[str, float]:
    return {g["fuel"]: float(g["perc"]) for g in entry.get("generationmix", [])}


def _region_from(entry: dict, inner: dict | None = None) -> RegionSignal:
    src = inner if inner is not None else entry
    intensity = src.get("intensity", {}) or {}
    return RegionSignal(
        region_id=int(entry.get("regionid", 0)),
        name=entry.get("shortname") or entry.get("dnoregion") or "Unknown",
        carbon_forecast=intensity.get("forecast"),
        index=intensity.get("index"),
        generation_mix=_mix(src),
    )


class GBRegionalAdapter:
    """Location-aware GB carbon intensity. Carbon only — see module docstring."""

    def __init__(self, client: CarbonIntensityClient | None = None, *,
                 timeout_seconds: float = 30.0,
                 max_attempts: int = 3) -> None:
        self._client = client or CarbonIntensityClient(
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )

    @property
    def market_name(self) -> str:
        return "GB"

    def regions(self) -> list[RegionSignal]:
        """All 18 GB grid regions, right now."""
        data = self._client.get("regional").get("data", [])
        entries = data[0].get("regions", []) if data else []
        return [_region_from(e) for e in entries]

    def for_postcode(self, outcode: str) -> RegionSignal:
        """Region covering a postcode. Takes the OUTWARD code only.

        The API rejects a full postcode, and quietly rejects some short forms
        too, so this normalises rather than passing user input straight
        through: "sw1a 1aa" -> "SW1A".
        """
        code = _outward(outcode)
        payload = self._client.get(f"regional/postcode/{code}")
        entry = payload.get("data", [{}])[0]
        inner = (entry.get("data") or [{}])[0]
        region = _region_from(entry, inner)
        if not region.name or region.name == "Unknown":
            region.name = entry.get("shortname", code)
        return region

    def forecast(self, region_id: int, start: datetime | None = None
                 ) -> list[RegionForecastPoint]:
        """48 hours of half-hourly forward carbon forecast for one region.

        Forward-looking on purpose, same rule as the national adapter: a
        scheduler deciding now only ever has the forecast.
        """
        moment = (start or datetime.now(timezone.utc)).astimezone(timezone.utc)
        stamp = moment.strftime("%Y-%m-%dT%H:%MZ")
        payload = self._client.get(f"regional/intensity/{stamp}/fw48h/regionid/{region_id}")
        entry = payload.get("data", {})
        rows = entry.get("data", []) if isinstance(entry, dict) else entry
        out = []
        for row in rows:
            intensity = row.get("intensity", {}) or {}
            out.append(RegionForecastPoint(
                timestamp=datetime.strptime(row["from"], "%Y-%m-%dT%H:%MZ").replace(
                    tzinfo=timezone.utc),
                carbon_forecast=intensity.get("forecast"),
                index=intensity.get("index"),
            ))
        return out


def _outward(postcode: str) -> str:
    """Outward code from anything the user might type."""
    cleaned = "".join(ch for ch in postcode.upper() if ch.isalnum() or ch == " ").strip()
    return cleaned.split(" ")[0] if " " in cleaned else cleaned[:4]
