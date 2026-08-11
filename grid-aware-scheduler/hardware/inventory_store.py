"""Append-only local store for facility discovery snapshots.

A snapshot is one provider's `DetectionResult.public_dict()` — already free
of raw serial numbers, UUIDs and credentials by the provider's own contract
(docs/discovery.md). The store never edits or deletes a snapshot; the newest
one is what the product surfaces. History stays intact so a device that
disappears from a later poll remains a matter of record.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "inventory.sqlite"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def _validate(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if not str(snapshot.get("provider", "")).strip():
        raise ValueError("snapshot needs a provider")
    observed = snapshot.get("observed_at")
    try:
        datetime.fromisoformat(str(observed))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"snapshot observed_at is not a timestamp: {observed!r}") from exc
    devices = snapshot.get("devices")
    if not isinstance(devices, list):
        raise ValueError("snapshot needs a devices list")
    if not all(isinstance(device, dict) for device in devices):
        raise ValueError("each snapshot device must be an object")
    warnings = snapshot.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        raise ValueError("snapshot warnings must be a list of strings")


def record_snapshot(snapshot: dict[str, Any], path: Path | None = None) -> int:
    _validate(snapshot)
    recorded_at = datetime.now().astimezone().isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO inventory_snapshots"
            " (provider, observed_at, recorded_at, payload_json)"
            " VALUES (?, ?, ?, ?)",
            (str(snapshot["provider"]), str(snapshot["observed_at"]),
             recorded_at, json.dumps(snapshot, sort_keys=True)),
        )
        return int(cursor.lastrowid)


def latest(path: Path | None = None) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT snapshot_id, recorded_at, payload_json"
            " FROM inventory_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    payload["snapshot_id"] = int(row["snapshot_id"])
    payload["recorded_at"] = row["recorded_at"]
    return payload


def summary(path: Path | None = None) -> dict[str, Any]:
    with closing(connect(path)) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM inventory_snapshots"
        ).fetchone()[0]
    newest = latest(path)
    return {
        "snapshot_count": int(count),
        "latest_recorded_at": newest["recorded_at"] if newest else None,
        "latest_device_count": len(newest["devices"]) if newest else 0,
        "latest_warning_count": len(newest.get("warnings", [])) if newest else 0,
    }
