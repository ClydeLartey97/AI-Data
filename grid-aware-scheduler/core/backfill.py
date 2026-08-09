"""
Backfill the local cache from the market APIs.

    python -m core.backfill --days 400
    python -m core.backfill --stats

Fetches only the settled days that are missing, so it is safe to re-run and
safe to interrupt — each day is committed as it lands. A year takes roughly
two minutes, once, and then every range in the UI is real instead of four
buttons rendering the same three weeks.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone

from adapters.gb import GBAdapter
from core import store


def backfill(days: int = 400, market: str = "GB", chunk: int = 5) -> dict:
    adapter = GBAdapter()
    end = store.settled_cutoff()
    start = end - timedelta(days=days - 1)

    conn = store.connect()
    try:
        todo = store.missing_days(market, start, end, conn)
        if not todo:
            print("Nothing missing — cache already covers that range.")
            return store.stats(market)

        print(f"{len(todo)} days missing of {days} requested "
              f"({start} → {end}). Fetching …")
        t0, written, failed = time.perf_counter(), 0, 0

        # Group consecutive days so the price endpoint's range call is used
        # rather than one request per day.
        i = 0
        while i < len(todo):
            block = [todo[i]]
            while (len(block) < chunk and i + len(block) < len(todo)
                   and todo[i + len(block)] == block[-1] + timedelta(days=1)):
                block.append(todo[i + len(block)])
            i += len(block)

            a, b = block[0], block[-1]
            try:
                points = adapter.get_data(
                    datetime.combine(a, datetime.min.time()),
                    datetime.combine(b, datetime.max.time()))
            except Exception as exc:
                failed += len(block)
                print(f"  {a} → {b}  FAILED: {str(exc)[:70]}", file=sys.stderr)
                continue

            by_day: dict[date, list[store.Row]] = {d: [] for d in block}
            for p in points:
                d = p.timestamp.astimezone(timezone.utc).date()
                if d in by_day:
                    by_day[d].append(store.Row(p.timestamp, p.carbon_intensity, p.price))
            for d, rows in by_day.items():
                if rows:
                    written += store.write_day(market, d, rows, conn)

            done = min(i, len(todo))
            rate = done / max(time.perf_counter() - t0, 0.01)
            eta = (len(todo) - done) / max(rate, 0.01)
            print(f"  {a} → {b}  {done}/{len(todo)} days  "
                  f"{written:,} rows  eta {eta:,.0f}s", flush=True)
    finally:
        conn.close()

    s = store.stats(market)
    print(f"\nCache now: {s['days']} days, {s['rows']:,} rows, "
          f"{s['from']} → {s['to']}, {s['size_mb']} MB"
          + (f" ({failed} days failed)" if failed else ""))
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the local grid cache.")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--market", default="GB")
    ap.add_argument("--stats", action="store_true", help="show cache state and exit")
    args = ap.parse_args()

    if args.stats:
        s = store.stats(args.market)
        print(f"{args.market}: {s['days']} days, {s['rows']:,} rows, "
              f"{s['from']} → {s['to']}, {s['size_mb']} MB")
        return
    backfill(args.days, args.market)


if __name__ == "__main__":
    main()
