"""What a shadow-mode pilot produced, aggregated from the decision journal.

A pilot runs beside an operator's existing scheduler. It sees the queue, it
recommends, and it executes nothing — so no real job is ever deferred by an
unproven system. Each recommendation is persisted by `core/audit_store.py`
with the exact price and carbon signal visible at the moment it was made, and
later graded against what the grid actually did via the scoring endpoint.

This module turns that journal into the one artefact the project could not
otherwise produce: a measured saving on hardware we do not own, with the
sample it rests on stated beside it.

Three rules shape everything here, and each exists because the obvious
implementation would mislead:

1. **Money is never summed across currencies.** A pilot spanning GB and MISO
   holds pounds and dollars; adding them produces a number with no meaning.
   Totals are grouped by currency and reported separately.
2. **The denominator is the scored subset, never the whole journal.** An
   unscored recommendation has no realised outcome, so including it would
   silently average a measurement against an assumption.
3. **A recommendation that lost money is counted.** Reporting only the wins
   is how a backtest flatters itself; losses appear in the totals and are
   also counted separately, because "saved on average" and "never worse" are
   different claims and an operator is buying the second one.
"""
from __future__ import annotations

import argparse
import json
import statistics
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import audit_store

#: Travels with every report. These are the limits a technical reader would
#: otherwise find themselves, and finding them unaided costs more credibility
#: than stating them costs.
DISCLOSURES = [
    "Advisory only. The pilot recommends and records; it never launches, "
    "defers or cancels a workload, so no result here was obtained by taking "
    "control of a production queue.",
    "Device throughput is ESTIMATED or PUBLISHED for every accelerator except "
    "the Apple M2 baseline this project measured itself. A PUBLISHED figure "
    "comes from an audited third-party submission and reflects a tuned "
    "ceiling, not typical deployment throughput.",
    "Multi-accelerator scaling is modelled at approximately 99% efficiency. "
    "Real distributed training achieves roughly 70-85%, so any multi-device "
    "runtime is optimistic and the energy attributed to it is understated.",
    "Carbon intensity is balancing-area scoped in US markets and national or "
    "regional in GB. It is never facility-level measurement and must not be "
    "reported as the emissions of a specific meter.",
    "Realised price and carbon series are supplied to the scoring endpoint by "
    "the operator. The report inherits the provenance of whatever was "
    "submitted; it does not independently verify outturn.",
]


@dataclass
class CurrencyTotals:
    """Cost aggregates for one currency. Never merged with another."""

    currency: str
    decisions: int = 0
    immediate: float = 0.0
    scheduled: float = 0.0
    oracle: float = 0.0
    saved: float = 0.0
    regret: float = 0.0
    worse_than_immediate: int = 0
    worst_loss: float = 0.0
    absolute_forecast_errors: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        saving_percent = (
            round(self.saved / self.immediate * 100, 2)
            if self.immediate else None)
        # How much of the prize a perfect-hindsight scheduler would have taken
        # was actually captured. Regret is what was left on the table.
        available = self.saved + self.regret
        capture_percent = (
            round(self.saved / available * 100, 2) if available > 0 else None)
        return {
            "currency": self.currency,
            "scored_decisions": self.decisions,
            "cost_if_run_immediately": round(self.immediate, 4),
            "cost_as_scheduled": round(self.scheduled, 4),
            "cost_saved": round(self.saved, 4),
            "saving_percent": saving_percent,
            "cost_with_perfect_hindsight": round(self.oracle, 4),
            "regret_against_perfect_hindsight": round(self.regret, 4),
            "capture_percent": capture_percent,
            "decisions_worse_than_immediate": self.worse_than_immediate,
            "worst_single_loss": round(self.worst_loss, 4),
            "median_absolute_forecast_error": (
                round(statistics.median(self.absolute_forecast_errors), 4)
                if self.absolute_forecast_errors else None),
        }


def _number(value: Any) -> float | None:
    """A finite float, or nothing. A NaN in a total is worse than a gap."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def build(*, path: Path | None = None, limit: int = 200,
          since: str | None = None) -> dict[str, Any]:
    """Aggregate the journal into one report.

    `since` filters on decision creation time (ISO-8601). `limit` bounds the
    journal read, mirroring the store's own cap rather than inventing a
    second one.
    """
    summaries = audit_store.list_decisions(limit=limit, path=path)
    if since:
        cutoff = datetime.fromisoformat(since)
        summaries = [row for row in summaries
                     if datetime.fromisoformat(row["created_at"]) >= cutoff]

    scored_ids = [row["id"] for row in summaries if row["status"] == "scored"]
    totals: dict[str, CurrencyTotals] = {}
    carbon_saved = carbon_regret = 0.0
    carbon_errors: list[float] = []
    carbon_worse = 0
    delays: list[float] = []
    markets: dict[str, int] = defaultdict(int)
    hardware: dict[str, int] = defaultdict(int)
    scored_records = 0

    for decision_id in scored_ids:
        decision = audit_store.get_decision(decision_id, path=path)
        if decision is None or not decision.get("score"):
            continue
        result = decision["score"]["result"]
        selected = result.get("realised_selected") or {}
        immediate = result.get("realised_immediate") or {}
        oracle = result.get("realised_oracle") or {}
        scored_records += 1
        markets[decision["market"]] += 1
        if selected.get("hardware"):
            hardware[selected["hardware"]] += 1

        delay = _number(selected.get("delay_hours"))
        if delay is not None:
            delays.append(delay)

        saved = _number(result.get("cost_saved"))
        currency = selected.get("currency") or immediate.get("currency")
        if saved is not None and currency:
            bucket = totals.setdefault(currency, CurrencyTotals(currency))
            bucket.decisions += 1
            bucket.saved += saved
            for field_name, source in (("immediate", immediate),
                                       ("scheduled", selected),
                                       ("oracle", oracle)):
                value = _number(source.get("cost"))
                if value is not None:
                    setattr(bucket, field_name,
                            getattr(bucket, field_name) + value)
            regret = _number(result.get("cost_regret"))
            if regret is not None:
                bucket.regret += regret
            error = _number(result.get("cost_forecast_error"))
            if error is not None:
                bucket.absolute_forecast_errors.append(abs(error))
            if saved < 0:
                bucket.worse_than_immediate += 1
                bucket.worst_loss = min(bucket.worst_loss, saved)

        carbon = _number(result.get("carbon_saved_kg"))
        if carbon is not None:
            carbon_saved += carbon
            if carbon < 0:
                carbon_worse += 1
        carbon_regret_value = _number(result.get("carbon_regret_kg"))
        if carbon_regret_value is not None:
            carbon_regret += carbon_regret_value
        carbon_error = _number(result.get("carbon_forecast_error_kg"))
        if carbon_error is not None:
            carbon_errors.append(abs(carbon_error))

    created = [row["created_at"] for row in summaries]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow",
        "period": {
            "first_decision": min(created) if created else None,
            "last_decision": max(created) if created else None,
        },
        "coverage": {
            "decisions_recorded": len(summaries),
            "decisions_scored": scored_records,
            "awaiting_outturn": len(summaries) - scored_records,
            # The whole report rests on this fraction. An operator reading a
            # saving without it cannot tell a campaign from an anecdote.
            "scored_percent": (round(scored_records / len(summaries) * 100, 1)
                               if summaries else None),
        },
        "cost_by_currency": [totals[key].as_dict() for key in sorted(totals)],
        "carbon": {
            "scored_decisions": scored_records,
            "carbon_saved_kg": round(carbon_saved, 4),
            "regret_against_perfect_hindsight_kg": round(carbon_regret, 4),
            "decisions_worse_than_immediate": carbon_worse,
            "median_absolute_forecast_error_kg": (
                round(statistics.median(carbon_errors), 4)
                if carbon_errors else None),
        },
        "delay": {
            "median_hours": (round(statistics.median(delays), 2)
                             if delays else None),
            "max_hours": round(max(delays), 2) if delays else None,
        },
        "markets": dict(sorted(markets.items())),
        "hardware_selected": dict(sorted(hardware.items())),
        "claimable": _claimable(scored_records, totals),
        "disclosures": list(DISCLOSURES),
    }


def _claimable(scored: int, totals: dict[str, CurrencyTotals]) -> dict[str, Any]:
    """What this report does and does not entitle anyone to say.

    Written as data rather than left to the reader, because the failure mode
    is a headline number quoted without its sample size. Zero scored
    decisions produces "nothing", not a saving of 0.00.
    """
    if scored == 0:
        return {
            "state": "no_measured_result",
            "statement": "No decision has been scored against realised "
                         "outturn, so no saving can be claimed.",
        }
    sample = f"{scored} scored decision" + ("" if scored == 1 else "s")
    if scored < 30:
        state = "indicative"
        caveat = (" This is a demonstration, not a statistically settled "
                  "result.")
    else:
        state, caveat = "measured", ""
    # Same currency order as the detailed sections, so the two never disagree.
    parts = [f"{totals[key].saved:+,.2f} {key}" for key in sorted(totals)]
    if parts:
        headline = ("Recommendations saved " + ", ".join(parts)
                    + " against running each job immediately, measured across "
                    + sample + ".")
    else:
        headline = f"No costed outcome was scored across {sample}."
    return {"state": state, "statement": headline + caveat}


#: Prose is hard-wrapped at this width. The numeric blocks are column-aligned,
#: so the text must never rely on the reader's viewport to wrap it — a soft
#: wrap in a narrow pane destroys the alignment that makes the figures legible.
WIDTH = 74


def _wrap(text: str, indent: str = "  ") -> list[str]:
    return textwrap.wrap(text, width=WIDTH, initial_indent=indent,
                         subsequent_indent=indent + "  ") or [indent + text]


def render(report: dict[str, Any]) -> str:
    """Plain text, deliberately unstyled — the numbers carry it.

    Kept free of layout so the same content can be dropped into a document,
    an email or a page without a redesign travelling with it.
    """
    lines = ["Shadow-mode pilot report",
             f"Generated {report['generated_at']}"]
    period = report["period"]
    if period["first_decision"]:
        lines.append(f"Decisions from {period['first_decision'][:19]}")
        lines.append(f"          to {period['last_decision'][:19]} (UTC)")

    coverage = report["coverage"]
    lines += ["", "Coverage",
              f"  Recorded          {coverage['decisions_recorded']}",
              f"  Scored            {coverage['decisions_scored']}"
              f" ({coverage['scored_percent']}%)"
              if coverage["scored_percent"] is not None else
              f"  Scored            {coverage['decisions_scored']}",
              f"  Awaiting outturn  {coverage['awaiting_outturn']}"]

    for item in report["cost_by_currency"]:
        lines += ["", f"Cost ({item['currency']})",
                  f"  Run immediately   {item['cost_if_run_immediately']:,.2f}",
                  f"  As scheduled      {item['cost_as_scheduled']:,.2f}",
                  f"  Saved             {item['cost_saved']:,.2f}"
                  + (f"  ({item['saving_percent']}%)"
                     if item["saving_percent"] is not None else ""),
                  f"  Perfect hindsight {item['cost_with_perfect_hindsight']:,.2f}"
                  + (f"  (captured {item['capture_percent']}% of it)"
                     if item["capture_percent"] is not None else ""),
                  f"  Worse than immediate on {item['decisions_worse_than_immediate']}"
                  f" of {item['scored_decisions']}"
                  + (f", worst {item['worst_single_loss']:,.2f}"
                     if item["worst_single_loss"] else "")]
        if item["median_absolute_forecast_error"] is not None:
            lines.append("  Typical forecast miss "
                         f"{item['median_absolute_forecast_error']:,.2f}")

    carbon = report["carbon"]
    lines += ["", "Carbon",
              f"  Saved             {carbon['carbon_saved_kg']:,.3f} kg",
              f"  Left on the table {carbon['regret_against_perfect_hindsight_kg']:,.3f} kg",
              f"  Worse than immediate on {carbon['decisions_worse_than_immediate']}"
              f" of {carbon['scored_decisions']}"]

    delay = report["delay"]
    if delay["median_hours"] is not None:
        lines += ["", "Delay accepted",
                  f"  Median {delay['median_hours']} h, longest {delay['max_hours']} h"]

    lines += ["", "What can be claimed"]
    lines += _wrap(report["claimable"]["statement"])
    lines += ["", "Disclosures"]
    for text in report["disclosures"]:
        lines += textwrap.wrap(text, width=WIDTH, initial_indent="  - ",
                               subsequent_indent="    ")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="emit the structured report instead of text")
    parser.add_argument("--since", help="ISO-8601 decision cutoff")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)
    report = build(limit=args.limit, since=args.since)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
