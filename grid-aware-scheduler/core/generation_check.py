"""Score the local generation model against a reference simulation.

`core/renewables.py` computes a plant's output locally so the scheduler can
recompute in microseconds on an Open-Meteo forecast. It says outright that the
shape should be right and the level optimistic by an unknown margin. This
module measures that margin against Renewables.ninja, which simulates the same
plant from MERRA-2 reanalysis with real turbine curves and bias correction.

**Level error and shape error are different failures and are reported
separately, because they break different things.**

A level error is a capacity error. If the local model says a 10 MW array
delivers 6 MW at noon and it really delivers 4 MW, the site's power envelope is
overstated and the scheduler will admit work the plant cannot carry.

A shape error is a *timing* error, and timing is the whole product. Scheduling
the heaviest job into the window the plant produces most is a claim about
shape. A model can be 30% optimistic at every hour and still place every job
perfectly, because a constant factor cancels out of an argmax. So a large bias
with a faithful shape is a much less serious defect than a small bias with the
peak in the wrong place, and a single blended accuracy score would hide exactly
that distinction.

The metric that decides whether an error mattered is therefore neither of them:
it is **window agreement** — given a job of some duration, does the local model
choose the same window the reference would have, and if not, how much
generation did choosing it give up? That mirrors how `core/backtest.py` scores
a decision rather than a forecast, and for the same reason. A model may be
wrong everywhere and still never change an answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class WindowAgreement:
    """Whether a modelling error would have changed a placement."""

    duration_hours: float
    local_start: datetime | None
    reference_start: datetime | None
    #: Mean reference capacity factor over the window the local model picked.
    delivered: float
    #: Mean reference capacity factor over the window the reference would pick.
    best_available: float

    @property
    def agrees(self) -> bool:
        return (self.local_start is not None
                and self.local_start == self.reference_start)

    @property
    def regret(self) -> float:
        """Generation given up by trusting the local model, 0-1.

        Zero when the two agree, and — the point worth noting — often zero even
        when they do not, because two different windows can carry the same
        generation. A disagreement is only a defect if it cost something.
        """
        return max(0.0, self.best_available - self.delivered)


@dataclass(frozen=True)
class GenerationComparison:
    """Local model scored against a reference, level and shape kept apart."""

    hours_compared: int
    local_mean: float
    reference_mean: float
    #: Mean absolute error in capacity factor, 0-1.
    mean_absolute_error: float
    #: Pearson correlation of the two hourly series. The shape measure.
    correlation: float | None
    #: Hours between the two models' peak output. The blunt shape measure.
    peak_offset_hours: float | None
    windows: list[WindowAgreement] = field(default_factory=list)

    @property
    def bias_ratio(self) -> float | None:
        """Local mean over reference mean. Above 1.0 is optimistic.

        This is the number to multiply the local model by to correct its level.
        It is undefined against a reference that produced nothing, which is a
        real case — a week of winter fog — and returns None rather than a
        division that would present as a correction factor.
        """
        if self.reference_mean <= 0:
            return None
        return self.local_mean / self.reference_mean

    @property
    def window_agreement_rate(self) -> float | None:
        if not self.windows:
            return None
        return sum(1 for w in self.windows if w.agrees) / len(self.windows)

    @property
    def mean_regret(self) -> float | None:
        """Average generation given up across the tested job durations."""
        if not self.windows:
            return None
        return sum(w.regret for w in self.windows) / len(self.windows)

    def summary(self) -> str:
        lines = [f"Compared {self.hours_compared} hours."]
        if self.bias_ratio is None:
            lines.append("Level: the reference produced nothing, so no bias "
                         "ratio is defined.")
        else:
            direction = "optimistic" if self.bias_ratio > 1 else "conservative"
            lines.append(
                f"Level: local mean {self.local_mean:.3f} against reference "
                f"{self.reference_mean:.3f} — {self.bias_ratio:.2f}x, "
                f"{direction}. Multiply local output by "
                f"{1 / self.bias_ratio:.2f} to correct it.")
        if self.correlation is None:
            lines.append("Shape: undefined — one series does not vary.")
        else:
            lines.append(f"Shape: correlation {self.correlation:.3f}, peak "
                         f"offset {self.peak_offset_hours:+.0f} h.")
        rate = self.window_agreement_rate
        if rate is not None:
            lines.append(
                f"Decisions: the local model picked the reference's own window "
                f"in {rate:.0%} of {len(self.windows)} tested durations, giving "
                f"up {self.mean_regret:.3f} capacity factor on average.")
        return "\n".join(lines)


def _align(local: list[tuple[datetime, float]],
           reference: list[tuple[datetime, float]]
           ) -> tuple[list[datetime], list[float], list[float]]:
    """Inner join on timestamp. Only hours both models cover are compared."""
    ref = {stamp: value for stamp, value in reference}
    stamps, a, b = [], [], []
    for stamp, value in sorted(local, key=lambda row: row[0]):
        if stamp in ref:
            stamps.append(stamp)
            a.append(value)
            b.append(ref[stamp])
    return stamps, a, b


def _correlation(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 2:
        return None
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return None  # a flat series has no shape to agree about
    return cov / math.sqrt(var_a * var_b)


def _best_window(values: list[float], width: int) -> tuple[int, float] | None:
    """Index and mean of the highest-output contiguous window."""
    if width <= 0 or width > len(values):
        return None
    total = sum(values[:width])
    best_index, best_total = 0, total
    for i in range(1, len(values) - width + 1):
        total += values[i + width - 1] - values[i - 1]
        if total > best_total:
            best_index, best_total = i, total
    return best_index, best_total / width


def compare(local: list[tuple[datetime, float]],
            reference: list[tuple[datetime, float]],
            *, durations_hours: tuple[float, ...] = (1, 2, 4, 8, 12)
            ) -> GenerationComparison:
    """Score a local capacity-factor series against a reference series.

    Both are hourly ``(timestamp, capacity_factor)``. Only timestamps present
    in both are compared, so a partial reference narrows the comparison rather
    than being padded with zeros — a missing hour is not a dark hour.
    """
    stamps, a, b = _align(local, reference)
    n = len(stamps)
    if n == 0:
        return GenerationComparison(0, 0.0, 0.0, 0.0, None, None, [])

    local_mean = sum(a) / n
    reference_mean = sum(b) / n
    mae = sum(abs(x - y) for x, y in zip(a, b)) / n

    peak_offset = None
    if n >= 2:
        peak_local = max(range(n), key=lambda i: a[i])
        peak_reference = max(range(n), key=lambda i: b[i])
        peak_offset = (stamps[peak_local]
                       - stamps[peak_reference]).total_seconds() / 3600.0

    windows = []
    for duration in durations_hours:
        width = max(1, int(round(duration)))
        if width > n:
            continue
        local_pick = _best_window(a, width)
        reference_pick = _best_window(b, width)
        if local_pick is None or reference_pick is None:
            continue
        local_index, _ = local_pick
        reference_index, best_available = reference_pick
        delivered = sum(b[local_index:local_index + width]) / width
        windows.append(WindowAgreement(
            duration_hours=duration,
            local_start=stamps[local_index],
            reference_start=stamps[reference_index],
            delivered=delivered,
            best_available=best_available,
        ))

    return GenerationComparison(
        hours_compared=n,
        local_mean=local_mean,
        reference_mean=reference_mean,
        mean_absolute_error=mae,
        correlation=_correlation(a, b),
        peak_offset_hours=peak_offset,
        windows=windows,
    )


@dataclass(frozen=True)
class PlantValidation:
    """A GB plant's day, decomposed into model error and curtailment.

    Comparing a weather model straight against a wind farm's meter conflates
    two completely different things, and the conflation is large enough to
    make the model look far worse than it is. Measured at Hornsea 1A on
    2026-08-14, the raw gap was 3.46x — of which 1.51x was the model running
    optimistic and 2.26x was the farm being curtailed. The meter records what
    the grid **allowed**, not what the wind could have driven.

    The decomposition works because a GB unit publishes both halves. Its
    Physical Notification is the operator's own forecast of output, made with
    a professional wind model and submitted before the balancing mechanism
    acts on it. So model against PN measures the model, and PN against the
    meter measures curtailment.
    """

    plant_id: str
    day: date
    #: Our model against the operator's own forecast. The real model score.
    model_vs_forecast: GenerationComparison
    #: The operator's forecast against metered output. Curtailment.
    forecast_vs_metered: GenerationComparison
    planned_mwh: float
    curtailed_mwh: float

    @property
    def curtailed_share(self) -> float | None:
        if self.planned_mwh <= 0:
            return None
        return self.curtailed_mwh / self.planned_mwh

    def summary(self) -> str:
        lines = [f"{self.plant_id} on {self.day}", "",
                 "Our model against the operator's own forecast "
                 "(this is the model score):",
                 self.model_vs_forecast.summary(), "",
                 "The operator's forecast against the meter (this is "
                 "curtailment, not model error):",
                 self.forecast_vs_metered.summary(), ""]
        share = self.curtailed_share
        if share is not None:
            lines.append(
                f"The plant planned {self.planned_mwh:.0f} MWh and was "
                f"curtailed {self.curtailed_mwh:.0f} MWh — {share:.0%} of its "
                f"own plan, in one day.")
        return "\n".join(lines)


def validate_plant(plant_id: str, latitude: float, longitude: float, day: date,
                   *, source: str = "wind") -> PlantValidation:
    """Score the model against a named GB plant, separating out curtailment.

    Intervals where the unit declared itself unavailable are excluded rather
    than counted as zero output: a stopped turbine is not a failed wind
    forecast, and including it would penalise the model for an outage.
    """
    from adapters import gb_plant
    from adapters.open_meteo import fetch_archive
    from core.renewables import solar_capacity_factor, wind_capacity_factor

    rows = gb_plant.fetch_plant(plant_id, day, day)
    metered = gb_plant.fetch_actual_output(plant_id, day)
    frame = fetch_archive(latitude, longitude, day, day)
    weather = {row.start_time.to_pydatetime(): row
               for row in frame.itertuples(index=False)}

    predicted, forecast, measured = [], [], []
    planned_mwh = curtailed_mwh = 0.0
    for row in rows:
        if not row.available_kw or row.intended_kw is None:
            continue  # unit unavailable: nothing for a weather model to answer
        point = weather.get(row.timestamp.replace(minute=0))
        actual = metered.get(row.timestamp)
        if point is None or actual is None:
            continue
        if source == "solar":
            factor = solar_capacity_factor(point.solar_radiation_wm2,
                                           point.temperature_c)
        else:
            factor = wind_capacity_factor(point.wind_speed_100m_ms)
        predicted.append((row.timestamp, factor))
        forecast.append((row.timestamp,
                         _clamp(row.intended_kw / row.available_kw)))
        measured.append((row.timestamp, _clamp(actual / row.available_kw)))
        planned_mwh += row.intended_kw * 0.5 / 1000
        curtailed_mwh += max(0.0, row.intended_kw - actual) * 0.5 / 1000

    return PlantValidation(
        plant_id=plant_id, day=day,
        model_vs_forecast=compare(predicted, forecast),
        forecast_vs_metered=compare(forecast, measured),
        planned_mwh=planned_mwh, curtailed_mwh=curtailed_mwh)


def _clamp(value: float) -> float:
    """Capacity factors live in 0-1. A wind farm drawing station load at night
    meters as slightly negative, which is real but is not negative output."""
    return max(0.0, min(1.0, value))


def validate_site(latitude: float, longitude: float, start, end, *,
                  source: str = "solar", tilt: float = 35,
                  azimuth: float = 180) -> GenerationComparison:
    """Score the local model at a real location over a real past date range.

    Fetches ERA5 weather for the period, runs the local model on it, fetches
    the reference simulation for the same point and period, and compares. Both
    sides are historical on purpose: the local model must be shown to work on
    weather it did not choose before it is trusted on a forecast.
    """
    from adapters import renewables_ninja
    from adapters.open_meteo import fetch_archive
    from core.renewables import solar_capacity_factor, wind_capacity_factor

    frame = fetch_archive(latitude, longitude, start, end)
    local = []
    for row in frame.itertuples(index=False):
        stamp = row.start_time.to_pydatetime()
        if source == "solar":
            factor = solar_capacity_factor(row.solar_radiation_wm2,
                                           row.temperature_c)
        else:
            factor = wind_capacity_factor(row.wind_speed_100m_ms)
        local.append((stamp, factor))

    client = renewables_ninja.RenewablesNinjaClient()
    if source == "solar":
        points = client.solar(latitude, longitude, start, end,
                              tilt=tilt, azimuth=azimuth)
    else:
        points = client.wind(latitude, longitude, start, end)
    return compare(local, [(p.timestamp, p.capacity_factor) for p in points])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Score the local generation model against Renewables.ninja.")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--source", choices=("solar", "wind"), default="solar")
    parser.add_argument(
        "--plant", metavar="BM_UNIT",
        help="Validate against a named GB unit's own forecast and meter "
             "instead of a reference simulation, separating model error from "
             "curtailment. Needs no token. Uses --start as the day.")
    parser.add_argument("--tilt", type=float, default=35)
    parser.add_argument("--azimuth", type=float, default=180)
    args = parser.parse_args()

    if args.plant:
        print(validate_plant(args.plant, args.lat, args.lon, args.start,
                             source=args.source).summary())
        return

    result = validate_site(args.lat, args.lon, args.start, args.end,
                           source=args.source, tilt=args.tilt,
                           azimuth=args.azimuth)
    print(f"{args.source} at {args.lat}, {args.lon} — "
          f"{args.start} to {args.end}\n")
    print(result.summary())


if __name__ == "__main__":
    main()
