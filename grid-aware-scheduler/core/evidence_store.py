"""Append-only local store for metadata-only workload evidence.

Observations are immutable by run ID. Repeated exact-fingerprint runs are
aggregated into stable measured profiles only after the evidence contract has
validated energy scope, quality provenance and thermal state.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from core.evidence import (MIN_PROFILE_SAMPLES, EvidenceProfile,
                           WorkloadObservation, build_evidence_profile,
                           observation_from_dict, observation_to_dict,
                           profile_from_dict, profile_to_dict)

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "evidence.sqlite"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS evidence_observations (
    run_id          TEXT PRIMARY KEY,
    fingerprint_key TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_fingerprint
ON evidence_observations(fingerprint_key, observed_at);

CREATE TABLE IF NOT EXISTS evidence_profiles (
    profile_id      TEXT PRIMARY KEY,
    fingerprint_key TEXT NOT NULL UNIQUE,
    profiled_at     TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _fingerprint_key(observation: WorkloadObservation) -> str:
    encoded = json.dumps(
        observation.fingerprint, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ingest_observation(observation: WorkloadObservation,
                       path: Path | None = None) -> dict[str, Any]:
    """Store one immutable observation and rebuild its profile if eligible."""
    if {observation.thermal_start, observation.thermal_end}.intersection(
        {"serious", "critical"}
    ):
        raise ValueError("serious or critical thermal runs cannot enter evidence")
    payload = observation_to_dict(observation)
    serialised = _canonical(payload)
    fingerprint_key = _fingerprint_key(observation)
    duplicate = False
    profile: EvidenceProfile | None = None

    with closing(connect(path)) as connection:
        existing = connection.execute(
            "SELECT payload_json FROM evidence_observations WHERE run_id = ?",
            (observation.run_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != serialised:
                raise ValueError(
                    f"run_id {observation.run_id!r} already exists with different evidence"
                )
            duplicate = True
        else:
            with connection:
                connection.execute(
                    "INSERT INTO evidence_observations VALUES (?, ?, ?, ?)",
                    (
                        observation.run_id,
                        fingerprint_key,
                        observation.observed_at.isoformat(),
                        serialised,
                    ),
                )

        rows = connection.execute(
            "SELECT payload_json FROM evidence_observations "
            "WHERE fingerprint_key = ? ORDER BY observed_at, run_id",
            (fingerprint_key,),
        ).fetchall()
        observations = [
            observation_from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]
        if len(observations) >= MIN_PROFILE_SAMPLES:
            profile = build_evidence_profile(observations)
            profile_payload = profile_to_dict(profile)
            with connection:
                connection.execute(
                    "INSERT INTO evidence_profiles VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(profile_id) DO UPDATE SET "
                    "profiled_at=excluded.profiled_at, "
                    "payload_json=excluded.payload_json",
                    (
                        profile.profile_id,
                        fingerprint_key,
                        profile.profiled_at.isoformat(),
                        _canonical(profile_payload),
                    ),
                )

    return {
        "run_id": observation.run_id,
        "duplicate": duplicate,
        "fingerprint_sample_count": len(observations),
        "profile": profile_to_dict(profile) if profile else None,
        "profile_ready": profile is not None,
        "samples_required": MIN_PROFILE_SAMPLES,
    }


def ingest_payload(payload: dict[str, Any],
                   path: Path | None = None) -> dict[str, Any]:
    raw = payload.get("observation") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("request needs an observation object")
    return ingest_observation(observation_from_dict(raw), path)


def list_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM evidence_profiles "
            "ORDER BY profiled_at DESC, profile_id"
        ).fetchall()
    return [profile_to_dict(profile_from_dict(json.loads(row["payload_json"])))
            for row in rows]


def get_profile(profile_id: str,
                path: Path | None = None) -> EvidenceProfile | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT payload_json FROM evidence_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
    return None if row is None else profile_from_dict(json.loads(row["payload_json"]))


def profile_map(path: Path | None = None) -> dict[str, EvidenceProfile]:
    profiles = [profile_from_dict(row) for row in list_profiles(path)]
    return {profile.profile_id: profile for profile in profiles}


def summary(path: Path | None = None) -> dict[str, int]:
    with closing(connect(path)) as connection:
        observations = connection.execute(
            "SELECT COUNT(*) FROM evidence_observations"
        ).fetchone()[0]
        profiles = connection.execute(
            "SELECT COUNT(*) FROM evidence_profiles"
        ).fetchone()[0]
        groups = connection.execute(
            "SELECT COUNT(DISTINCT fingerprint_key) FROM evidence_observations"
        ).fetchone()[0]
    return {
        "observation_count": int(observations),
        "profile_count": int(profiles),
        "pending_fingerprint_count": max(0, int(groups) - int(profiles)),
    }
