"""
Local cache for market data.

Without this, every page load re-fetches from the APIs, which costs about 0.3
seconds per day of history. That made a year impractical, so the app fetched
three weeks — and then offered 1M, 3M, 1Y and Max range buttons that all
rendered the *same three weeks*. Four controls, one view, no indication
anything was wrong. A range selector that silently lies is worse than not
having one.

So: fetch once, keep it, serve from disk. SQLite because the data is
settlement-keyed and small (a year of half-hours is ~17,500 rows per market —
about 1 MB), and because it is already how the sibling grid tool stores the
same shape of data.

Writes are idempotent per (market, settlement_date): re-fetching a day replaces
it rather than duplicating, so a backfill can be re-run or interrupted safely.

**Only settled days are cached.** Today and tomorrow are still forecasts and
will change, so caching them would pin a stale prediction. The cutoff is
yesterday; anything more recent goes to the live API every time.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "grid.sqlite"

#: Days more recent than this are forecasts and are never cached.
SETTLED_LAG_DAYS = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grid (
    market           TEXT NOT NULL,
    settlement_date  TEXT NOT NULL,
    ts               TEXT NOT NULL,
    carbon           REAL,
    price            REAL,
    PRIMARY KEY (market, ts)
);
CREATE INDEX IF NOT EXISTS grid_by_day ON grid (market, settlement_date);
CREATE INDEX IF NOT EXISTS grid_by_ts  ON grid (market, ts);

CREATE TABLE IF NOT EXISTS coverage (
    market          TEXT NOT NULL,
    settlement_date TEXT NOT NULL,
    rows            INTEGER NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (market, settlement_date)
);
"""


@dataclass(frozen=True)
class Row:
    timestamp: datetime
    carbon: float | None
    price: float | None


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.executescript(_SCHEMA)
    return conn


def settled_cutoff(today: date | None = None) -> date:
    return (today or datetime.now(timezone.utc).date()) - timedelta(days=SETTLED_LAG_DAYS)


def cached_days(market: str, conn: sqlite3.Connection | None = None) -> set[date]:
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            "SELECT settlement_date FROM coverage WHERE market = ? AND rows > 0",
            (market,)).fetchall()
        return {date.fromisoformat(r[0]) for r in rows}
    finally:
        if own:
            conn.close()


def missing_days(market: str, start: date, end: date,
                 conn: sqlite3.Connection | None = None) -> list[date]:
    """Settled days in the range that are not cached yet."""
    have = cached_days(market, conn)
    cutoff = settled_cutoff()
    out, day = [], start
    while day <= end:
        if day <= cutoff and day not in have:
            out.append(day)
        day += timedelta(days=1)
    return out


def write_day(market: str, day: date, rows: list[Row],
              conn: sqlite3.Connection | None = None) -> int:
    """Replace a day's rows. Idempotent, so a backfill can be re-run."""
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            conn.execute("DELETE FROM grid WHERE market = ? AND settlement_date = ?",
                         (market, day.isoformat()))
            conn.executemany(
                "INSERT OR REPLACE INTO grid (market, settlement_date, ts, carbon, price)"
                " VALUES (?, ?, ?, ?, ?)",
                [(market, day.isoformat(), r.timestamp.astimezone(timezone.utc).isoformat(),
                  r.carbon, r.price) for r in rows])
            conn.execute(
                "INSERT OR REPLACE INTO coverage (market, settlement_date, rows, fetched_at)"
                " VALUES (?, ?, ?, ?)",
                (market, day.isoformat(), len(rows),
                 datetime.now(timezone.utc).isoformat()))
        return len(rows)
    finally:
        if own:
            conn.close()


def read(market: str, start: datetime, end: datetime,
         conn: sqlite3.Connection | None = None) -> list[Row]:
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            "SELECT ts, carbon, price FROM grid"
            " WHERE market = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (market, _iso(start), _iso(end))).fetchall()
        return [Row(datetime.fromisoformat(t), c, p) for t, c, p in rows]
    finally:
        if own:
            conn.close()


def extent(market: str, conn: sqlite3.Connection | None = None
           ) -> tuple[datetime, datetime] | None:
    """Oldest and newest cached timestamp — what the range selector may offer."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM grid WHERE market = ?", (market,)).fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1])
    finally:
        if own:
            conn.close()


def stats(market: str) -> dict:
    with closing(connect()) as conn:
        n = conn.execute("SELECT COUNT(*) FROM grid WHERE market = ?", (market,)).fetchone()[0]
        d = conn.execute("SELECT COUNT(*) FROM coverage WHERE market = ? AND rows > 0",
                         (market,)).fetchone()[0]
        ext = extent(market, conn)
    return {"rows": n, "days": d,
            "from": ext[0].date().isoformat() if ext else None,
            "to": ext[1].date().isoformat() if ext else None,
            "size_mb": round(DB_PATH.stat().st_size / 1e6, 2) if DB_PATH.exists() else 0.0}


def _iso(value: datetime) -> str:
    v = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat()
