"""Replay a published production job trace against a real electricity market.

Every saving this project has reported so far came from a workload it made up.
That is the honest gap against any funding or procurement conversation: the
scheduler has never been shown work it did not invent. A published trace fixes
half of that. The jobs are real, the arrival times are real, the durations and
GPU counts are real, and they came out of somebody else's production cluster.

The supported trace is Microsoft's Philly DNN training trace (CC BY 4.0), which
records 117,325 jobs submitted to internal GPU clusters between August and
December 2017, each with a submission time, the attempts that ran it, and the
GPUs each attempt held. Cite the ATC'19 paper "Analysis of Large-Scale
Multi-Tenant GPU Clusters for DNN Training Workloads" wherever a result from it
is published.

**The claim this module is built to support, and its limit.**

Every job in the trace waited in a real queue before it ran. That wait is
recorded, so it can be reused rather than assumed: a job that already sat for
90 minutes can be moved anywhere inside those 90 minutes and finish no later
than it actually did. Under the `observed` policy nothing is delayed by a
single second beyond what the production cluster already imposed, which makes
the resulting saving very hard to argue with.

It also makes it small, and the measurement says so: in this trace the median
job waited about two minutes. Most jobs simply have no slack to exploit, and
a replay that reported a large number under this policy would be wrong. The
`declared` policy answers the separate question of what becomes available when
an operator states a deadline, and it is labelled as a counterfactual policy
rather than an observation, because nobody in 2017 declared those deadlines.

**What is replayed and what is not.** The workload is real. The market is
real. Their alignment is not: the trace is from 2017 and the cached market
history is recent, so trace timestamps are mapped onto the market window
preserving weekday and time of day. Diurnal and weekly structure survive; a
claim about 2017 prices does not follow and must not be made.
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from adapters.base_adapter import GridDataPoint
from core.grid import PERIOD_HOURS, Job, cheapest_window, cleanest_window, run_immediately

DEFAULT_TRACE = (Path(__file__).resolve().parents[1]
                 / "data" / "cache" / "traces" / "cluster_job_log")

#: Attribution required by the trace licence, carried with every result.
SOURCE = {
    "name": "Microsoft Philly DNN training trace",
    "licence": "CC BY 4.0",
    "citation": ("Jeon et al., \"Analysis of Large-Scale Multi-Tenant GPU "
                 "Clusters for DNN Training Workloads\", USENIX ATC 2019"),
    "url": "https://github.com/msr-fiddle/philly-traces",
}


@dataclass(frozen=True)
class TraceJob:
    """One real job: when it arrived, how long it ran, how much it held."""

    job_id: str
    submitted: datetime
    started: datetime
    runtime_hours: float
    gpus: int

    @property
    def observed_delay_hours(self) -> float:
        """Slack the production cluster already spent before starting it."""
        return (self.started - self.submitted).total_seconds() / 3600

    @property
    def gpu_hours(self) -> float:
        return self.runtime_hours * self.gpus


def _time(text: Any) -> datetime | None:
    # Missing times are the literal string "None" in this trace, not null.
    if not text or text == "None":
        return None
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def load_jobs(path: Path | None = None, *, limit: int | None = None
              ) -> list[TraceJob]:
    """Read the trace, keeping only jobs that can be replayed honestly.

    Failed and killed jobs are dropped: their runtime measures when something
    broke, not what the work required. Jobs with no usable timestamps or no
    recorded GPUs are dropped rather than defaulted, on the same principle the
    hardware scan follows — an absent figure is reported absent, never filled
    in with a plausible one.
    """
    target = path or DEFAULT_TRACE
    if not target.exists():
        raise FileNotFoundError(
            f"trace not found at {target}. Fetch cluster_job_log from "
            f"{SOURCE['url']} (Git LFS) before replaying.")
    raw = json.loads(target.read_text())

    jobs: list[TraceJob] = []
    for record in raw:
        if record.get("status") != "Pass":
            continue
        attempts = record.get("attempts") or []
        starts = [t for t in (_time(a.get("start_time")) for a in attempts) if t]
        ends = [t for t in (_time(a.get("end_time")) for a in attempts) if t]
        submitted = _time(record.get("submitted_time"))
        if not (submitted and starts and ends):
            continue
        started, finished = min(starts), max(ends)
        if finished <= started or started < submitted:
            continue
        gpus = sum(len(detail.get("gpus") or [])
                   for attempt in attempts
                   for detail in (attempt.get("detail") or []))
        if gpus <= 0:
            continue
        jobs.append(TraceJob(
            job_id=str(record.get("jobid", "")),
            submitted=submitted.replace(tzinfo=timezone.utc),
            started=started.replace(tzinfo=timezone.utc),
            runtime_hours=(finished - started).total_seconds() / 3600,
            gpus=gpus,
        ))
        if limit and len(jobs) >= limit:
            break
    return jobs


def align(jobs: list[TraceJob], series: list[GridDataPoint]) -> datetime:
    """Offset mapping trace time onto market time, preserving the week.

    Aligning on a whole number of weeks keeps weekday and time of day intact,
    which is what matters: both electricity prices and cluster submissions
    have strong daily and weekly structure, and destroying that alignment
    would replay Monday-morning jobs into Saturday-night prices.
    """
    if not jobs or not series:
        raise ValueError("alignment needs both jobs and a market series")
    weeks = round((series[0].timestamp - min(job.submitted for job in jobs))
                  .total_seconds() / (7 * 86400))
    return timedelta(days=7 * weeks)


@dataclass
class ReplayResult:
    policy: str
    objective: str
    jobs_replayed: int
    jobs_shifted: int
    gpu_hours: float
    energy_kwh: float
    cost_immediate: float
    cost_scheduled: float
    carbon_kg_immediate: float
    carbon_kg_scheduled: float
    added_delay_hours: list
    currency: str
    device: str
    device_provenance: str

    def as_dict(self) -> dict[str, Any]:
        delays = sorted(self.added_delay_hours)
        saved = self.cost_immediate - self.cost_scheduled
        carbon_saved = self.carbon_kg_immediate - self.carbon_kg_scheduled
        return {
            "policy": self.policy,
            "objective": self.objective,
            "jobs_replayed": self.jobs_replayed,
            "jobs_moved": self.jobs_shifted,
            "jobs_with_no_slack": self.jobs_replayed - self.jobs_shifted,
            "gpu_hours": round(self.gpu_hours, 1),
            "energy_kwh": round(self.energy_kwh, 1),
            "currency": self.currency,
            "cost_if_run_on_arrival": round(self.cost_immediate, 2),
            "cost_as_scheduled": round(self.cost_scheduled, 2),
            "cost_saved": round(saved, 2),
            "cost_saved_percent": (round(saved / self.cost_immediate * 100, 2)
                                   if self.cost_immediate else None),
            "carbon_kg_if_run_on_arrival": round(self.carbon_kg_immediate, 1),
            "carbon_kg_as_scheduled": round(self.carbon_kg_scheduled, 1),
            "carbon_kg_saved": round(carbon_saved, 1),
            "carbon_saved_percent": (
                round(carbon_saved / self.carbon_kg_immediate * 100, 2)
                if self.carbon_kg_immediate else None),
            "added_delay_hours": {
                "median": round(statistics.median(delays), 3) if delays else 0.0,
                "p90": round(delays[int(len(delays) * 0.9)], 3) if delays else 0.0,
                "max": round(delays[-1], 3) if delays else 0.0,
            },
            "device": self.device,
            "device_provenance": self.device_provenance,
            "source": dict(SOURCE),
        }


def replay(jobs: list[TraceJob], series: list[GridDataPoint], *,
           policy: str = "observed", objective: str = "cost",
           declared_deadline_hours: float = 24.0,
           watts_per_gpu: float = 300.0, pue: float = 1.0,
           device: str = "unspecified accelerator",
           device_provenance: str = "SPEC") -> ReplayResult:
    """Place every trace job on the market timeline and score the result.

    `observed` gives each job exactly the slack its own production queue
    already spent, so no job finishes later than it really did. `declared`
    gives every job the same stated deadline, which is a policy an operator
    would have to choose — it is a counterfactual, not an observation.
    """
    if policy not in {"observed", "declared"}:
        raise ValueError("policy must be 'observed' or 'declared'")
    if objective not in {"cost", "carbon"}:
        raise ValueError("objective must be 'cost' or 'carbon'")
    if not series:
        raise ValueError("replay needs a market series")

    offset = align(jobs, series)
    origin = series[0].timestamp
    horizon = len(series)
    chooser = cheapest_window if objective == "cost" else cleanest_window

    totals = dict(cost_now=0.0, cost_new=0.0, carbon_now=0.0, carbon_new=0.0,
                  energy=0.0, gpu_hours=0.0)
    delays: list[float] = []
    replayed = shifted = 0

    for job in jobs:
        arrival = job.submitted + offset
        index = int((arrival - origin).total_seconds() // (PERIOD_HOURS * 3600))
        if index < 0 or index >= horizon:
            continue
        duration = max(1, round(job.runtime_hours / PERIOD_HOURS))
        slack = (job.observed_delay_hours if policy == "observed"
                 else declared_deadline_hours)
        deadline = duration + max(0, round(slack / PERIOD_HOURS))
        if index + deadline > horizon:
            continue

        power_kw = job.gpus * watts_per_gpu / 1000 * pue
        window = series[index:index + deadline]
        placed = Job(job.job_id, power_kw, duration, deadline)
        now = run_immediately(window, placed)
        best = chooser(window, placed)
        if not (now.complete and best.complete):
            continue

        replayed += 1
        totals["cost_now"] += now.cost
        totals["cost_new"] += best.cost
        totals["carbon_now"] += now.carbon_kg
        totals["carbon_new"] += best.carbon_kg
        totals["energy"] += placed.energy_kwh
        totals["gpu_hours"] += job.gpu_hours
        if best.start_index > 0:
            shifted += 1
            delays.append(best.start_index * PERIOD_HOURS)

    if not replayed:
        raise ValueError(
            "no trace job fitted inside the market window; load more market "
            "history or fewer jobs")

    return ReplayResult(
        policy=policy, objective=objective, jobs_replayed=replayed,
        jobs_shifted=shifted, gpu_hours=totals["gpu_hours"],
        energy_kwh=totals["energy"], cost_immediate=totals["cost_now"],
        cost_scheduled=totals["cost_new"],
        carbon_kg_immediate=totals["carbon_now"],
        carbon_kg_scheduled=totals["carbon_new"], added_delay_hours=delays,
        currency="GBP", device=device, device_provenance=device_provenance,
    )


def main(argv: list[str] | None = None) -> int:
    from core import feed

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--market", default="GB")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--policy", default="observed",
                        choices=["observed", "declared"])
    parser.add_argument("--objective", default="cost",
                        choices=["cost", "carbon"])
    parser.add_argument("--deadline-hours", type=float, default=24.0)
    parser.add_argument("--watts-per-gpu", type=float, default=300.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    jobs = load_jobs(limit=args.limit)
    series = feed.load_days(args.days, market=args.market)
    result = replay(jobs, series, policy=args.policy, objective=args.objective,
                    declared_deadline_hours=args.deadline_hours,
                    watts_per_gpu=args.watts_per_gpu)
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{payload['jobs_replayed']:,} real jobs replayed "
          f"({payload['gpu_hours']:,.0f} GPU-hours, "
          f"{payload['energy_kwh']:,.0f} kWh) against {args.market}")
    print(f"policy: {args.policy}  objective: {args.objective}")
    print(f"cost   {payload['cost_if_run_on_arrival']:,.2f} -> "
          f"{payload['cost_as_scheduled']:,.2f} "
          f"({payload['cost_saved_percent']}% saved)")
    print(f"carbon {payload['carbon_kg_if_run_on_arrival']:,.1f} -> "
          f"{payload['carbon_kg_as_scheduled']:,.1f} kg "
          f"({payload['carbon_saved_percent']}% saved)")
    print(f"moved  {payload['jobs_moved']:,} jobs; "
          f"{payload['jobs_with_no_slack']:,} had no slack to use")
    print(f"delay  median {payload['added_delay_hours']['median']} h, "
          f"max {payload['added_delay_hours']['max']} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
