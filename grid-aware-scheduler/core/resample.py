"""
Time-range bucketing — how a year of half-hours becomes a readable chart.

No chart should ever receive a year of 30-minute bars. A year is 17,520
points against maybe 1,000 pixels: seventeen points per pixel, most of them
invisible, all of them shipped. Every good financial chart solves this by
choosing a resolution per range, and so does this.

**The rule that matters here, and it is domain-specific: never average away
the extremes.** Naive downsampling takes the mean of each bucket. For a
scheduler that is actively harmful — the entire job is finding the cheapest
half-hour, and a mean erases exactly that. A day whose 30-minute prices ran
from GBP 2.84 to GBP 158.94 has a daily mean around GBP 80, and both numbers
that matter are gone.

So each bucket keeps mean, min and max. The chart draws the mean as a line
and the min-max range as a band behind it, which means zooming out loses
resolution but never loses the extremes. The reader can still see that a
cheap half-hour existed, then zoom in to find it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Target points on screen. Roughly one per 2 CSS pixels on a wide chart —
#: dense enough to show structure, sparse enough to stay responsive.
TARGET_POINTS = 420

PERIOD = timedelta(minutes=30)


@dataclass(frozen=True)
class Range:
    """A selectable time range, and the bucket it implies."""

    key: str
    label: str
    span: timedelta | None          # None = everything available
    bucket: timedelta
    tick_format: str

    @property
    def is_intraday(self) -> bool:
        """Whether a BUCKET is sub-daily. Drives the hover readout only."""
        return self.bucket < timedelta(days=1)

    @property
    def axis_scale(self) -> str:
        """How to label the TIME AXIS — derived from the span, not the bucket.

        These are different questions and conflating them is a real bug: a
        1-month range buckets into 2-hour bars, so the bucket is "intraday",
        but labelling a month-wide axis with clock times ("22:00", "20:00")
        tells the reader nothing about where they are. Span decides the axis;
        bucket decides the readout.
        """
        span = self.span or timedelta(days=3650)
        if span <= timedelta(days=2):
            return "time"      # 14:30
        if span <= timedelta(days=120):
            return "day"       # 14 Aug
        return "month"         # Aug 2026


#: Ranges in the order a range selector shows them. Buckets chosen so each
#: range lands near TARGET_POINTS rather than by round numbers.
RANGES: list[Range] = [
    Range("1D", "1D", timedelta(days=1), PERIOD, "%H:%M"),
    Range("1W", "1W", timedelta(days=7), PERIOD, "%a %d"),
    Range("1M", "1M", timedelta(days=30), timedelta(hours=2), "%d %b"),
    Range("3M", "3M", timedelta(days=90), timedelta(hours=6), "%d %b"),
    Range("1Y", "1Y", timedelta(days=365), timedelta(days=1), "%b %Y"),
    Range("MAX", "Max", None, timedelta(days=7), "%b %Y"),
]

RANGES_BY_KEY = {r.key: r for r in RANGES}


@dataclass
class Bucket:
    """One aggregated interval. Keeps the extremes, not just the middle."""

    start: datetime
    mean: float
    low: float
    high: float
    count: int

    @property
    def spread(self) -> float:
        return self.high - self.low


def pick_range(key: str) -> Range:
    return RANGES_BY_KEY.get(key, RANGES_BY_KEY["1W"])


def auto_bucket(span: timedelta, target: int = TARGET_POINTS) -> timedelta:
    """Bucket width that lands a span near ``target`` points.

    Snapped to intervals a human reads naturally — nobody wants a 37-minute
    bucket. Used when a range is derived from data rather than chosen.
    """
    ideal = span / max(target, 1)
    for candidate in (PERIOD, timedelta(hours=1), timedelta(hours=2),
                      timedelta(hours=3), timedelta(hours=6), timedelta(hours=12),
                      timedelta(days=1), timedelta(days=2), timedelta(days=7),
                      timedelta(days=14), timedelta(days=30)):
        if candidate >= ideal:
            return candidate
    return timedelta(days=90)


def _floor(moment: datetime, bucket: timedelta) -> datetime:
    """Snap a timestamp down to its bucket boundary, anchored on the epoch."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    at = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    elapsed = (at - epoch) // bucket
    return epoch + elapsed * bucket


def bucket_series(
    points: list[tuple[datetime, float | None]],
    bucket: timedelta,
) -> list[Bucket]:
    """Aggregate ``(timestamp, value)`` pairs into buckets.

    Missing values are dropped rather than zero-filled: a half-hour with no
    price is not a half-hour that was free, and the two must never be
    confused (the same rule the scheduler follows when picking a window).
    """
    groups: dict[datetime, list[float]] = {}
    for stamp, value in points:
        if value is None:
            continue
        groups.setdefault(_floor(stamp, bucket), []).append(value)

    return [
        Bucket(start=start, mean=sum(vals) / len(vals),
               low=min(vals), high=max(vals), count=len(vals))
        for start, vals in sorted(groups.items())
    ]


def prepare(
    points: list[tuple[datetime, float | None]],
    range_key: str = "1W",
    *,
    now: datetime | None = None,
) -> tuple[list[Bucket], Range]:
    """Clip to a range and bucket it — the one call a chart needs."""
    rng = pick_range(range_key)
    if not points:
        return [], rng

    latest = now or max(ts for ts, _ in points)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)

    if rng.span is not None:
        cutoff = latest - rng.span
        points = [(ts, v) for ts, v in points
                  if (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)) >= cutoff]

    return bucket_series(points, rng.bucket), rng
