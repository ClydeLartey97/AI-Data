"""Append-only history of measured device capability.

A single microbenchmark run is a reading, not a baseline. A baseline is a
reading that passed its preflight gate and has been reproduced across
separate runs, which is why this store keeps every run rather than
overwriting a current value: reproducibility can only be judged from the
history, and a device that slows down is only visible against its own past.

The stored quantity is a *ceiling* — achievable arithmetic throughput and
streaming bandwidth on ideal dense work. It is never a workload rate, and
`baseline()` refuses to promote a run that was not validated or that has not
been repeated.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "baselines.sqlite"

#: Separate invocations required before a set of readings becomes a baseline.
#: Matches the evidence threshold used for workload calibration.
MIN_RUNS = 3

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS baseline_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    device       TEXT NOT NULL,
    stack        TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    validated    INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS baseline_device
ON baseline_runs(device, stack, observed_at);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def _validated(report: dict) -> bool:
    """A run counts only if a preflight gate actually passed for it.

    `run(skip_preflight=True)` leaves the context empty, so an unvalidated
    diagnostic can never be mistaken for evidence later.
    """
    context = report.get("context") or {}
    return bool(context.get("captured_at"))


def record_run(report: dict, path: Path | None = None) -> int:
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    device = str(report.get("device") or "").strip()
    stack = str(report.get("stack") or "").strip()
    if not device or not stack:
        raise ValueError("report needs a device and a software stack fingerprint")
    measurements = report.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise ValueError("report needs at least one measurement")
    try:
        datetime.fromisoformat(str(report.get("observed_at")))
    except (TypeError, ValueError) as exc:
        raise ValueError("report observed_at is not a timestamp") from exc

    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO baseline_runs"
            " (device, stack, observed_at, recorded_at, validated, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (device, stack, str(report["observed_at"]),
             datetime.now(timezone.utc).isoformat(),
             int(_validated(report)), json.dumps(report, sort_keys=True)),
        )
        return int(cursor.lastrowid)


def history(device: str | None = None, *, validated_only: bool = True,
            limit: int = 100, path: Path | None = None) -> list[dict]:
    query = "SELECT * FROM baseline_runs"
    clauses, params = [], []
    if device:
        clauses.append("device = ?")
        params.append(device)
    if validated_only:
        clauses.append("validated = 1")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY run_id DESC LIMIT ?"
    params.append(int(limit))
    with closing(connect(path)) as connection:
        rows = connection.execute(query, params).fetchall()
    runs = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload["run_id"] = row["run_id"]
        payload["validated"] = bool(row["validated"])
        payload["recorded_at"] = row["recorded_at"]
        runs.append(payload)
    return runs


def _rate(report: dict, name: str, dtype: str | None = None) -> float | None:
    rates = [m["rate"] for m in report.get("measurements", [])
             if m.get("name") == name and (dtype is None or m.get("dtype") == dtype)]
    return max(rates) if rates else None


def baseline(device: str, *, path: Path | None = None,
             min_runs: int = MIN_RUNS) -> dict:
    """Promote repeated validated runs into one device ceiling, or explain why not."""
    runs = history(device, validated_only=True, path=path)
    if len(runs) < min_runs:
        return {
            "device": device,
            "established": False,
            "run_count": len(runs),
            "reason": (f"{len(runs)} validated run(s); {min_runs} separate runs "
                       "are required before a ceiling is treated as reproduced"),
        }
    stacks = {run.get("stack") for run in runs[:min_runs]}
    if len(stacks) != 1:
        return {
            "device": device,
            "established": False,
            "run_count": len(runs),
            "reason": "runs span different software stacks and are not comparable",
        }

    metrics: dict[str, Any] = {}
    for label, name, dtype in (
        ("gemm_fp32_gflops", "gemm", "float32"),
        ("gemm_fp16_gflops", "gemm", "float16"),
        ("memory_bandwidth_gbs", "memory_bandwidth", None),
        # Model-level rates, which are workload throughput rather than a
        # ceiling; they are reported separately and never averaged together.
        ("prefill_tokens_per_second", "prefill", None),
        ("decode_tokens_per_second", "decode", None),
    ):
        values = [value for value in
                  (_rate(run, name, dtype) for run in runs) if value is not None]
        if len(values) < min_runs:
            continue
        median = statistics.median(values)
        metrics[label] = {
            "median": round(median, 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "spread_percent": round((max(values) - min(values)) / median * 100, 1)
            if median else 0.0,
            "samples": len(values),
        }
    return {
        "device": device,
        "established": bool(metrics),
        "run_count": len(runs),
        "stack": stacks.pop(),
        "metrics": metrics,
        "scope": "ceiling",
        "note": ("Achievable rate on ideal dense work. Not a workload "
                 "throughput and not a substitute for model-level measurement."),
    }


def summary(path: Path | None = None) -> dict:
    with closing(connect(path)) as connection:
        total = connection.execute("SELECT COUNT(*) FROM baseline_runs").fetchone()[0]
        validated = connection.execute(
            "SELECT COUNT(*) FROM baseline_runs WHERE validated = 1").fetchone()[0]
        devices = [row[0] for row in connection.execute(
            "SELECT DISTINCT device FROM baseline_runs").fetchall()]
    return {
        "run_count": int(total),
        "validated_run_count": int(validated),
        "devices": devices,
        "runs_required_for_baseline": MIN_RUNS,
    }
