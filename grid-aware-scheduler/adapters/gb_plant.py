"""What a specific GB power station is actually able to generate.

Every other availability path in this project *models* a plant: solar and wind
from weather, a turbine from ambient temperature, everything else from a
declared availability factor. A model is a guess with physics behind it, and
for the plant a data centre is actually plugged into, a guess is unnecessary —
because in GB the plant already tells the system operator the answer, twice
over, every half hour.

**MELS — Maximum Export Limit.** The most the unit is able to export in a given
half hour. It is the availability signal directly: outages, derates and
maintenance all show up in it, because a unit that cannot run declares that it
cannot run.

**PN — Physical Notification.** What the unit intends to generate. Forward
looking, submitted ahead of the settlement period, and therefore available at
the moment a scheduler has to decide.

**Why this matters more than it sounds.** Checked live on 2026-08-20, Sizewell B
unit 1 declared 588 MW available and Torness unit 1 declared 637 MW — while
Hartlepool unit 1 and Drax unit 1 both declared **zero**. Those units were
offline. A modelled availability factor of 0.9 would have reported them at
ninety per cent of nameplate and scheduled a data centre's heaviest work
against a nuclear unit that was not running. Nothing in a weather forecast
would have caught it.

This is GB-only, and deliberately so. It rests on Elexon's per-BM-unit balancing
data, which exists because GB runs a central balancing mechanism; the US ISOs
publish plant-level output far more coarsely and mostly after the fact. The
import is deferred through `adapters.national_grid_tool`, so a US-market install
never touches it.

**Identity is the BM Unit id, not the coordinates.** A latitude and longitude
locate a plant well enough to fetch its weather, but two units at one station
have the same coordinates and different availability — exactly the case
Hartlepool demonstrates. `T_HRTL-1` and `T_HRTL-2` are different machines.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from adapters import national_grid_tool

#: Elexon's per-unit physical datasets this module reads.
AVAILABILITY_DATASET = "MELS"   # what the unit *can* export
INTENDED_DATASET = "PN"         # what the unit *plans* to export

#: Half-hour settlement periods per day. A clock-change day has 46 or 50, which
#: is why periods are read from the data rather than assumed.
_MW_TO_KW = 1000.0


@dataclass(frozen=True)
class PlantInterval:
    """One half hour of a named unit's declared position."""

    timestamp: datetime
    available_kw: float
    intended_kw: float | None = None

    @property
    def offline(self) -> bool:
        return self.available_kw <= 0


def _to_intervals(frame, column: str) -> dict[datetime, float]:
    """Map an Elexon physical frame onto UTC half-hour starts."""
    out: dict[datetime, float] = {}
    if frame is None or len(frame) == 0:
        return out
    for row in frame.itertuples(index=False):
        stamp = getattr(row, "time_from", None)
        level = getattr(row, column, None)
        if stamp is None or level is None or level != level:
            continue
        moment = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
        # A unit may submit several ramp segments inside one half hour. The
        # binding availability is the lowest of them: a limit that applies for
        # part of the period constrains the period.
        value = float(level) * _MW_TO_KW
        key = moment.replace(minute=0 if moment.minute < 30 else 30,
                             second=0, microsecond=0)
        out[key] = value if key not in out else min(out[key], value)
    return out


def fetch_plant(bm_unit: str, start: date, end: date, *,
                include_intended: bool = True) -> list[PlantInterval]:
    """Declared availability, and optionally intended output, for one unit.

    Returns one entry per half hour the unit reported. A day with no rows is
    returned as no rows rather than as zeros — "the unit filed nothing" and
    "the unit declared itself unavailable" are different statements, and only
    the second is an outage.
    """
    fetch_physical, = national_grid_tool.load(
        "sources.elexon.physical", "fetch_physical")

    availability: dict[datetime, float] = {}
    intended: dict[datetime, float] = {}
    day = start
    while day <= end:
        availability.update(
            _to_intervals(fetch_physical(bm_unit, AVAILABILITY_DATASET, day),
                          "level_from"))
        if include_intended:
            intended.update(
                _to_intervals(fetch_physical(bm_unit, INTENDED_DATASET, day),
                              "level_from"))
        day += timedelta(days=1)

    return [
        PlantInterval(timestamp=stamp, available_kw=available,
                      intended_kw=intended.get(stamp))
        for stamp, available in sorted(availability.items())
    ]


def availability_by_timestamp(bm_unit: str, timestamps: list[datetime]
                              ) -> dict[datetime, float]:
    """Declared available power in kW, keyed to the requested half hours.

    Timestamps the unit did not report are absent from the result rather than
    zero, so a caller can tell a silent unit from an unavailable one and
    decide for itself. `core/site_profile.py` treats a silent unit as
    unavailable, which is the conservative reading, but that is its decision to
    make and not one this module should bake in.
    """
    if not timestamps:
        return {}
    # A GB settlement day is not a UTC day. Under BST it runs 23:00 UTC to
    # 23:00 UTC, so the last two half-hours of a UTC day are filed under the
    # *next* settlement date. Fetching only the UTC dates left those two
    # intervals absent, and "absent" is read downstream as unavailable — which
    # reported a running reactor as offline for an hour. Widen by a day on each
    # side and let the timestamp filter below discard the excess.
    start = min(timestamps).astimezone(timezone.utc).date() - timedelta(days=1)
    end = max(timestamps).astimezone(timezone.utc).date() + timedelta(days=1)
    wanted = {stamp.astimezone(timezone.utc) for stamp in timestamps}
    return {
        interval.timestamp: interval.available_kw
        for interval in fetch_plant(bm_unit, start, end, include_intended=False)
        if interval.timestamp in wanted
    }
