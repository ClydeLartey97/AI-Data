"""Durable local audit log for planning decisions and signal snapshots."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.base_adapter import GridDataPoint

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "audit.sqlite"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    market          TEXT NOT NULL,
    location        TEXT NOT NULL,
    signal_mode     TEXT NOT NULL,
    request_json    TEXT NOT NULL,
    response_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_created ON decisions(created_at DESC);

CREATE TABLE IF NOT EXISTS decision_signals (
    decision_id     TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    ts              TEXT NOT NULL,
    price           REAL,
    carbon          REAL,
    carbon_method   TEXT,
    PRIMARY KEY (decision_id, ts)
);

CREATE TABLE IF NOT EXISTS decision_scores (
    decision_id     TEXT PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
    scored_at       TEXT NOT NULL,
    score_json      TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    return connection


def save_decision(*, market: str, location: str, signal_mode: str,
                  request: dict[str, Any], response: dict[str, Any],
                  signals: list[GridDataPoint], path: Path | None = None) -> str:
    decision_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id, created_at, market, location, signal_mode,
                    json.dumps(request, sort_keys=True, separators=(",", ":")),
                    json.dumps(response, sort_keys=True, separators=(",", ":")),
                ),
            )
            connection.executemany(
                "INSERT INTO decision_signals VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        decision_id,
                        point.timestamp.astimezone(timezone.utc).isoformat(),
                        point.price,
                        point.carbon_intensity,
                        getattr(point, "carbon_method", None),
                    )
                    for point in signals
                ],
            )
    return decision_id


def save_score(decision_id: str, score: dict[str, Any],
               path: Path | None = None) -> None:
    with closing(connect(path)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if not exists:
            raise KeyError(f"unknown decision {decision_id}")
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO decision_scores VALUES (?, ?, ?)",
                (
                    decision_id, datetime.now(timezone.utc).isoformat(),
                    json.dumps(score, sort_keys=True, separators=(",", ":")),
                ),
            )


def get_decision(decision_id: str, path: Path | None = None) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            return None
        points = connection.execute(
            "SELECT ts, price, carbon, carbon_method FROM decision_signals "
            "WHERE decision_id = ? ORDER BY ts", (decision_id,),
        ).fetchall()
        score = connection.execute(
            "SELECT scored_at, score_json FROM decision_scores WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "market": row["market"],
        "location": row["location"],
        "signal_mode": row["signal_mode"],
        "request": json.loads(row["request_json"]),
        "response": json.loads(row["response_json"]),
        "signals": [dict(point) for point in points],
        "score": None if score is None else {
            "scored_at": score["scored_at"],
            "result": json.loads(score["score_json"]),
        },
    }


def list_decisions(limit: int = 50, path: Path | None = None) -> list[dict[str, Any]]:
    limit = min(200, max(1, int(limit)))
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT d.id, d.created_at, d.market, d.location, d.signal_mode, "
            "d.request_json, d.response_json, s.scored_at "
            "FROM decisions AS d LEFT JOIN decision_scores AS s "
            "ON s.decision_id = d.id "
            "ORDER BY d.created_at DESC LIMIT ?", (limit,),
        ).fetchall()

    summaries = []
    for row in rows:
        request = json.loads(row["request_json"])
        response = json.loads(row["response_json"])
        workload = request.get("workload", {})
        selected = response.get("selected", {})
        summaries.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "market": row["market"],
            "location": row["location"],
            "signal_mode": row["signal_mode"],
            "status": "scored" if row["scored_at"] else "awaiting_outturn",
            "scored_at": row["scored_at"],
            "model_key": workload.get("model_key"),
            "task": workload.get("task"),
            "hardware": selected.get("hardware"),
            "start": selected.get("start"),
            "finish": selected.get("finish"),
            "cost": selected.get("cost"),
            "currency": selected.get("currency"),
            "carbon_kg": selected.get("carbon_kg"),
            "pareto": selected.get("pareto"),
        })
    return summaries
