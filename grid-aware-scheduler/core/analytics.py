"""
Derived analytics — the questions a scheduler actually asks of a year of data.

A time series answers "what happened". These answer "what should I do", and
each is a standard piece of energy analysis rather than something invented
here:

- **Duration curve.** Every half-hour sorted worst-to-best. The classic tool
  of power-system analysis, and it reads directly as opportunity: the shape of
  the left-hand tail is how much of the year is expensive, and the flatness of
  the middle is how much shifting is worth.
- **Time-of-day profile.** Mean and spread by hour, across the whole history.
  A scheduler with a deadline longer than a day cares far more about this than
  about any individual day.
- **Savings against deadline.** The product's central question: given a
  deadline of N hours, how much does the best window beat starting now? Run
  over every start in the history, so the answer is a distribution rather than
  one anecdote.
- **Price-carbon relationship.** Cheap and clean are not the same thing. The
  correlation says how often optimising one gets you the other for free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from adapters.base_adapter import GridDataPoint

PERIODS_PER_HOUR = 2


@dataclass
class Profile:
    """Statistics by hour of day."""

    hours: list[int] = field(default_factory=list)
    mean: list[float] = field(default_factory=list)
    p10: list[float] = field(default_factory=list)
    p90: list[float] = field(default_factory=list)
    lo: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)


def _q(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = (len(sorted_vals) - 1) * p
    a, b = math.floor(i), math.ceil(i)
    return sorted_vals[a] + (sorted_vals[b] - sorted_vals[a]) * (i - a)


def hour_profile(series: list[GridDataPoint], field_name: str) -> Profile:
    buckets: dict[int, list[float]] = {h: [] for h in range(24)}
    for p in series:
        v = getattr(p, field_name)
        if v is not None:
            buckets[p.timestamp.hour].append(float(v))
    out = Profile()
    for h in range(24):
        vals = sorted(buckets[h])
        if not vals:
            continue
        out.hours.append(h)
        out.mean.append(sum(vals) / len(vals))
        out.p10.append(_q(vals, 0.10))
        out.p90.append(_q(vals, 0.90))
        out.lo.append(vals[0])
        out.hi.append(vals[-1])
    return out


def duration_curve(series: list[GridDataPoint], field_name: str,
                   points: int = 200) -> list[tuple[float, float]]:
    """(percent of time, value) sorted high to low, downsampled for drawing."""
    vals = sorted((float(getattr(p, field_name)) for p in series
                   if getattr(p, field_name) is not None), reverse=True)
    if not vals:
        return []
    n = len(vals)
    step = max(1, n // points)
    return [(i / n * 100.0, vals[i]) for i in range(0, n, step)]


@dataclass
class SavingsCurve:
    deadlines: list[float] = field(default_factory=list)   # hours
    median: list[float] = field(default_factory=list)      # % saved
    p90: list[float] = field(default_factory=list)
    best: list[float] = field(default_factory=list)


def savings_vs_deadline(series: list[GridDataPoint], field_name: str,
                        run_hours: float = 4.0,
                        deadlines: tuple[float, ...] = (2, 4, 6, 12, 24, 48, 72, 168),
                        samples: int = 400) -> SavingsCurve:
    """How much a longer deadline is worth, measured over the whole history.

    For each deadline, sample starts across the series, compare running
    immediately against the cheapest window that still meets the deadline, and
    report the distribution of savings. One example proves nothing; this is
    every start in a year.
    """
    vals = [float(getattr(p, field_name)) for p in series
            if getattr(p, field_name) is not None]
    run = max(1, int(run_hours * PERIODS_PER_HOUR))
    out = SavingsCurve()
    if len(vals) < run * 2:
        return out

    # Rolling mean over the run length — the cost of starting at each index.
    window: list[float] = []
    total = 0.0
    for i, v in enumerate(vals):
        total += v
        window.append(total)
    def run_cost(i: int) -> float:
        return window[i + run - 1] - (window[i - 1] if i else 0.0)

    last_start = len(vals) - run
    for dl in deadlines:
        horizon = max(1, int(dl * PERIODS_PER_HOUR))
        if horizon <= run:
            continue
        starts = range(0, max(1, last_start - horizon), max(1, (last_start - horizon) // samples or 1))
        pct = []
        for s in starts:
            base = run_cost(s)
            if base <= 0:
                continue
            best = min(run_cost(k) for k in range(s, min(s + horizon - run + 1, last_start + 1)))
            pct.append((base - best) / base * 100.0)
        if not pct:
            continue
        pct.sort()
        out.deadlines.append(dl)
        out.median.append(_q(pct, 0.5))
        out.p90.append(_q(pct, 0.9))
        out.best.append(pct[-1])
    return out


def correlation(series: list[GridDataPoint]) -> dict:
    """How closely cheap tracks clean — and how often they disagree."""
    pairs = [(float(p.price), float(p.carbon_intensity)) for p in series
             if p.price is not None and p.carbon_intensity is not None]
    if len(pairs) < 10:
        return {"n": 0}
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0

    # How often is the cheapest decile also the cleanest decile?
    px = sorted(xs)[max(0, len(xs) // 10)]
    py = sorted(ys)[max(0, len(ys) // 10)]
    cheap = [(a, b) for a, b in pairs if a <= px]
    both = sum(1 for _, b in cheap if b <= py)
    return {
        "n": len(pairs), "r": r,
        "cheap_and_clean_pct": (both / len(cheap) * 100.0) if cheap else 0.0,
        "price_p10": px, "carbon_p10": py,
        "scatter": pairs[:: max(1, len(pairs) // 900)],
    }
