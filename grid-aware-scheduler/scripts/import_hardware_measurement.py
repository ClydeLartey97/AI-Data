#!/usr/bin/env python3
"""Import accepted standalone measurement JSON into the baseline history."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware import baseline_store

SCHEMA = "ai-energy-hardware-measurement-v1"


def import_one(source: Path, database: Path | None = None) -> tuple[int, dict]:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"{source.name}: not an AI Energy hardware measurement")
    validation = payload.get("validation") or {}
    eligible = validation.get("eligible_metrics") or {}
    if (payload.get("status") != "accepted"
            or validation.get("accepted") is not True
            or eligible.get("throughput") is not True):
        reasons = validation.get("blockers") or ["measurement was not accepted"]
        raise ValueError(f"{source.name}: rejected measurement: {'; '.join(reasons)}")
    run_id = baseline_store.record_run(payload, database)
    state = baseline_store.baseline(payload["device"], path=database)
    return run_id, state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import accepted Mac or NVIDIA measurement files.")
    parser.add_argument("measurements", nargs="+", type=Path)
    parser.add_argument("--database", type=Path,
                        help="alternate baseline database (normally omitted)")
    args = parser.parse_args(argv)

    failed = False
    for source in args.measurements:
        try:
            run_id, state = import_one(source, args.database)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            failed = True
            print(f"REFUSED: {exc}", file=sys.stderr)
            continue
        print(f"Imported accepted run {run_id} for {state['device']}.")
        if state["established"]:
            print(f"Reproduced baseline established from {state['run_count']} runs.")
        else:
            print(state["reason"] + ".")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
