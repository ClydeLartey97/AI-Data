"""Workload types, and the schema that lets the scheduler stay ignorant of them.

**The finding this module is built on.** Before writing it, the existing
scheduler was read to see how much of it was actually AI-shaped. Very little
is. `core/portfolio.py` schedules a `PortfolioJob`, whose fields are
`work_amount`, `work_unit`, `utility`, `earliest_start`, `deadline` and
`depends_on` — nothing about models or tokens. `core/planner.py` places a
`PlanningCandidate` described by `runtime_hours`, `it_power_kw` and `pue`.
Neither knows what the work *is*. The AI assumptions live above them, in
`core/workload.py`'s two-value `Task` enum, in the estimator, and in the user
interface.

So this module does **not** touch the scheduler. It adds a declarative layer
that compiles any workload type down to the contracts the scheduler already
consumes. Adding a ninth workload type later means adding one `WorkloadType`
definition here — a name, its extra fields, and how those fields become power
and duration — and changing nothing else. That is the whole design, and it is
why the scheduling engine is not re-litigated for every new kind of work.

**What is deliberately NOT claimed.** Compiling a workload produces
`runtime_hours` and `it_power_kw`. Where those numbers come from is the
caller's problem and their provenance travels with them. A user-entered
estimate is `ESTIMATED` and stays that way. Nothing here measures anything,
and nothing here promotes a typed-in figure to a measurement.

**And the physics claim this module refuses to make.** Cheap or abundant
electricity does not make hardware run faster. A workload placed in a cheap
window takes exactly as long as it would have taken in an expensive one. Work
finishes sooner only if the platform allocates it *more hardware* or *more
power headroom* — which is a capacity decision, modelled by
`core/portfolio.py`'s per-interval power ceiling, not a consequence of price.
`compile_candidates` therefore never varies duration with price, and a test
asserts it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

#: Work units the scheduler can carry. The first six already existed in
#: `core/evidence.py` for AI modalities; the rest are what the new workload
#: types actually produce. A unit is never converted into another unit —
#: `core/portfolio.py` already refuses to add unlike work, and that refusal is
#: what keeps "300 frames plus 40 terahashes" from becoming a meaningless sum.
WORK_UNITS = frozenset({
    # existing AI modalities
    "tokens", "images", "audio_seconds", "samples",
    "training_examples", "optimizer_steps",
    # added for general compute
    "frames", "terahashes", "simulation_steps", "records", "gigabytes",
    "core_hours", "tasks",
})

#: How interruptible a workload is. These are separate because they are
#: genuinely separate capabilities and conflating them causes real errors: a
#: job can often be paused in place (suspend to RAM) without being
#: checkpointable (restartable on another machine after a crash), and a job
#: can be movable between sites without being pausable at all.
@dataclass(frozen=True)
class Flexibility:
    pausable: bool = False
    checkpointable: bool = False
    movable: bool = False
    interruptible: bool = False
    parallelisable: bool = False
    #: Largest number of independent pieces the work splits into. 1 means it
    #: must run as one block. Only meaningful when `parallelisable`.
    max_parallel_units: int = 1

    def __post_init__(self) -> None:
        if self.max_parallel_units < 1:
            raise ValueError("max_parallel_units must be at least 1")
        if self.max_parallel_units > 1 and not self.parallelisable:
            raise ValueError(
                "max_parallel_units above 1 requires parallelisable=True")

    @property
    def shiftable(self) -> bool:
        """Can this work be moved in time at all?

        A job that cannot be paused, checkpointed or interrupted must run as
        one uninterrupted block, which it still can — starting later is not
        the same as being interrupted. So shiftability is about whether the
        *whole block* can move, which every workload here supports; what these
        flags gate is whether it can be **split**.
        """
        return True

    @property
    def splittable(self) -> bool:
        return self.checkpointable or self.interruptible or self.parallelisable


@dataclass(frozen=True)
class ResourceRequest:
    """Hardware the work needs, as a range rather than a single number.

    The minimum and maximum are what make "give it more hardware in a cheap
    window" expressible at all. Without a maximum there is no headroom to
    allocate; without a minimum there is no floor to protect.
    """

    cpu_cores: int = 0
    gpu_count: int = 0
    accelerator_count: int = 0
    memory_gb: float = 0.0
    gpu_memory_gb_each: float = 0.0
    min_gpu_count: int | None = None
    max_gpu_count: int | None = None
    min_cpu_cores: int | None = None
    max_cpu_cores: int | None = None

    def __post_init__(self) -> None:
        for name in ("cpu_cores", "gpu_count", "accelerator_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("memory_gb", "gpu_memory_gb_each"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative number")
        for low, high, base in (("min_gpu_count", "max_gpu_count", "gpu_count"),
                                ("min_cpu_cores", "max_cpu_cores", "cpu_cores")):
            low_value, high_value = getattr(self, low), getattr(self, high)
            if (low_value is not None and high_value is not None
                    and low_value > high_value):
                raise ValueError(f"{low} cannot exceed {high}")
            nominal = getattr(self, base)
            if low_value is not None and nominal and nominal < low_value:
                raise ValueError(f"{base} is below {low}")
            if high_value is not None and nominal and nominal > high_value:
                raise ValueError(f"{base} is above {high}")

    @property
    def scalable(self) -> bool:
        """Whether the platform has any headroom to speed this work up.

        This is the only honest route to "finishes sooner in a good window":
        more hardware, not cheaper electricity.
        """
        return bool((self.max_gpu_count or 0) > (self.min_gpu_count or self.gpu_count)
                    or (self.max_cpu_cores or 0) > (self.min_cpu_cores or self.cpu_cores))


class WorkloadType(str, Enum):
    """What kind of computation this is.

    A string enum so it serialises into the existing JSON API without a
    converter, matching how `core/workload.py`'s `Task` is already handled.
    """

    AI_TRAINING = "ai_training"
    AI_INFERENCE = "ai_inference"
    MINING = "mining"
    RENDERING = "rendering"
    HPC = "hpc"
    DATA_PROCESSING = "data_processing"
    BATCH = "batch"
    CUSTOM = "custom"


@dataclass(frozen=True)
class FieldSpec:
    """One type-specific field, described well enough to build a form from.

    The user interface is generated from these rather than hand-written per
    type, which is what stops a ninth workload type from needing its own page.
    """

    key: str
    label: str
    unit: str = ""
    kind: str = "number"            # number | integer | text | boolean
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    default: object = None
    help: str = ""

    def validate(self, value: object) -> object:
        if value is None:
            if self.required:
                raise ValueError(f"{self.key} is required")
            return self.default
        if self.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{self.key} must be true or false")
            return value
        if self.kind == "text":
            if not isinstance(value, str):
                raise ValueError(f"{self.key} must be text")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.key} must be a number")
        if not math.isfinite(value):
            raise ValueError(f"{self.key} must be finite")
        if self.kind == "integer" and int(value) != value:
            raise ValueError(f"{self.key} must be a whole number")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.key} must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.key} must be at most {self.maximum}")
        return int(value) if self.kind == "integer" else float(value)


@dataclass(frozen=True)
class WorkloadDefinition:
    """Everything that distinguishes one workload type from another.

    Deliberately data plus one function. If a new type needs more than this,
    that is a signal the abstraction is wrong rather than a reason to special-
    case it in the scheduler.
    """

    type: WorkloadType
    label: str
    description: str
    default_work_unit: str
    fields: tuple[FieldSpec, ...] = ()
    #: Continuous revenue-earning work with no completion deadline. Mining is
    #: the only one today. It needs different optimisation logic entirely —
    #: see `core/mining.py` — because there is no "finish by" to schedule
    #: against, only an hour-by-hour decision about whether running pays.
    continuous: bool = False
    default_flexibility: Flexibility = field(default_factory=Flexibility)

    def validate_fields(self, values: dict | None) -> dict:
        values = dict(values or {})
        known = {spec.key for spec in self.fields}
        unknown = set(values) - known
        if unknown:
            raise ValueError(
                f"{self.label} does not accept {sorted(unknown)}; "
                f"known fields are {sorted(known)}")
        return {spec.key: spec.validate(values.get(spec.key))
                for spec in self.fields}


def _f(key, label, unit="", kind="number", required=False,
       minimum=None, maximum=None, default=None, help="") -> FieldSpec:
    return FieldSpec(key=key, label=label, unit=unit, kind=kind,
                     required=required, minimum=minimum, maximum=maximum,
                     default=default, help=help)


DEFINITIONS: dict[WorkloadType, WorkloadDefinition] = {
    WorkloadType.AI_TRAINING: WorkloadDefinition(
        type=WorkloadType.AI_TRAINING,
        label="AI model training",
        description="Training or fine-tuning a model. Usually the longest and "
                    "most power-dense work on a site, and the hardest to shift "
                    "in time because a run can outlast any deadline.",
        default_work_unit="optimizer_steps",
        default_flexibility=Flexibility(
            checkpointable=True, movable=True, pausable=True),
        fields=(
            _f("model_size_b", "Model size", "billion parameters",
               minimum=0.001, help="Total parameters, which drives memory."),
            _f("gpu_count", "GPUs", "count", kind="integer", minimum=1,
               default=1),
            _f("gpu_memory_gb", "GPU memory each", "GB", minimum=0),
            _f("checkpoint_interval_hours", "Checkpoint interval", "hours",
               minimum=0, default=1.0,
               help="How much work is lost if the run is interrupted. Sets "
                    "the smallest chunk the scheduler can move."),
            _f("distributed", "Distributed across nodes", kind="boolean",
               default=False,
               help="Multi-node scaling is modelled optimistically at ~99% "
                    "where real distributed training reaches 70-85%."),
        ),
    ),
    WorkloadType.AI_INFERENCE: WorkloadDefinition(
        type=WorkloadType.AI_INFERENCE,
        label="Batch AI inference",
        description="Serving a fixed volume of requests. Batch inference is "
                    "shiftable; latency-bound online serving is not, and "
                    "should be modelled as base load instead.",
        default_work_unit="tokens",
        default_flexibility=Flexibility(
            interruptible=True, movable=True, parallelisable=True,
            max_parallel_units=64),
        fields=(
            _f("request_count", "Requests", "count", kind="integer",
               minimum=1, required=True),
            _f("tokens_per_request", "Tokens per request", "tokens",
               minimum=1, default=512),
            _f("latency_target_ms", "Latency target", "ms", minimum=0,
               help="Leave empty for offline batch. A tight target means the "
                    "work is arrival-driven and is not shiftable."),
        ),
    ),
    WorkloadType.MINING: WorkloadDefinition(
        type=WorkloadType.MINING,
        label="Bitcoin / proof-of-work mining",
        description="Continuous, interruptible, revenue-earning. Has no "
                    "completion deadline, so it is not scheduled — it is "
                    "dispatched hour by hour on whether revenue beats cost.",
        default_work_unit="terahashes",
        continuous=True,
        default_flexibility=Flexibility(
            pausable=True, interruptible=True, parallelisable=True,
            max_parallel_units=10000),
        fields=(
            _f("miner_model", "Miner model", kind="text",
               help="Recorded for the audit trail; not used in the maths."),
            _f("hash_rate_th_s", "Available hash rate", "TH/s", minimum=0.001,
               required=True),
            _f("efficiency_j_per_th", "Efficiency", "J/TH", minimum=0.001,
               required=True,
               help="Joules per terahash. With hash rate this fixes power "
                    "draw exactly, so no separate power figure is needed."),
            _f("revenue_per_th_day", "Revenue", "currency per TH/s per day",
               minimum=0, required=True,
               help="Operator-supplied. Depends on network difficulty and "
                    "coin price, which this project does not fetch."),
            _f("curtailable", "Can curtail on demand", kind="boolean",
               default=True),
            _f("opex_per_hour", "Non-energy operating cost", "currency/hour",
               minimum=0, default=0.0),
        ),
    ),
    WorkloadType.RENDERING: WorkloadDefinition(
        type=WorkloadType.RENDERING,
        label="3D rendering / video processing",
        description="Frame-parallel work. Close to ideal for grid-aware "
                    "scheduling: it splits cleanly, each piece is short, and "
                    "the deadline is usually real but generous.",
        default_work_unit="frames",
        default_flexibility=Flexibility(
            checkpointable=True, interruptible=True, movable=True,
            parallelisable=True, max_parallel_units=100000),
        fields=(
            _f("frame_count", "Frames", "count", kind="integer", minimum=1,
               required=True),
            _f("seconds_per_frame", "Render time per frame", "seconds",
               minimum=0.001, required=True),
            _f("max_parallel_frames", "Maximum frames in parallel", "count",
               kind="integer", minimum=1, default=1,
               help="The real lever. More workers finish sooner; cheap "
                    "electricity on its own does not."),
        ),
    ),
    WorkloadType.HPC: WorkloadDefinition(
        type=WorkloadType.HPC,
        label="Scientific computing / HPC simulation",
        description="Tightly coupled simulation. Often checkpointable but "
                    "rarely splittable mid-run, and frequently CPU-bound "
                    "rather than GPU-bound.",
        default_work_unit="core_hours",
        default_flexibility=Flexibility(checkpointable=True, movable=True),
        fields=(
            _f("core_hours", "Total core-hours", "core-hours", minimum=0.001,
               required=True),
            _f("nodes", "Nodes", "count", kind="integer", minimum=1, default=1),
            _f("checkpoint_interval_hours", "Checkpoint interval", "hours",
               minimum=0, default=0.0,
               help="Zero means the run cannot be interrupted at all."),
            _f("mpi_coupled", "Tightly coupled (MPI)", kind="boolean",
               default=True,
               help="Coupled runs cannot be split across time windows."),
        ),
    ),
    WorkloadType.DATA_PROCESSING: WorkloadDefinition(
        type=WorkloadType.DATA_PROCESSING,
        label="Data processing / ETL",
        description="Dataset-driven batch work with real dependencies. "
                    "Usually the most deadline-flexible work on a site.",
        default_work_unit="gigabytes",
        default_flexibility=Flexibility(
            checkpointable=True, interruptible=True, movable=True,
            parallelisable=True, max_parallel_units=1000),
        fields=(
            _f("dataset_gb", "Dataset size", "GB", minimum=0.001,
               required=True),
            _f("throughput_gb_per_hour", "Throughput", "GB/hour",
               minimum=0.001, required=True),
            _f("stage_count", "Pipeline stages", "count", kind="integer",
               minimum=1, default=1),
        ),
    ),
    WorkloadType.BATCH: WorkloadDefinition(
        type=WorkloadType.BATCH,
        label="General batch computing",
        description="Anything that runs to completion on a deadline and does "
                    "not need a specialised model.",
        default_work_unit="tasks",
        default_flexibility=Flexibility(movable=True),
        fields=(
            _f("task_count", "Tasks", "count", kind="integer", minimum=1,
               default=1),
        ),
    ),
    WorkloadType.CUSTOM: WorkloadDefinition(
        type=WorkloadType.CUSTOM,
        label="Custom workload",
        description="Declare power and duration directly. The escape hatch, "
                    "and deliberately the least opinionated type — everything "
                    "the scheduler needs is stated rather than derived.",
        default_work_unit="tasks",
        default_flexibility=Flexibility(movable=True),
        fields=(
            _f("work_unit_label", "What one unit of work is", kind="text",
               default="tasks"),
            _f("work_amount", "Amount of work", "units", minimum=0.000001,
               default=1.0),
        ),
    ),
}


def definition(workload_type: WorkloadType | str) -> WorkloadDefinition:
    """Look up a type, accepting the enum or its string value."""
    if isinstance(workload_type, str):
        try:
            workload_type = WorkloadType(workload_type)
        except ValueError as error:
            raise ValueError(
                f"unknown workload type {workload_type!r}; known types are "
                f"{[t.value for t in WorkloadType]}") from error
    return DEFINITIONS[workload_type]


def catalogue() -> list[dict]:
    """Every type, shaped for a selector in the interface or the API."""
    return [
        {
            "type": entry.type.value,
            "label": entry.label,
            "description": entry.description,
            "work_unit": entry.default_work_unit,
            "continuous": entry.continuous,
            "fields": [
                {"key": spec.key, "label": spec.label, "unit": spec.unit,
                 "kind": spec.kind, "required": spec.required,
                 "default": spec.default, "help": spec.help}
                for spec in entry.fields
            ],
        }
        for entry in DEFINITIONS.values()
    ]


@dataclass(frozen=True)
class WorkloadSpec:
    """One piece of work, whatever kind it is.

    The common fields are everything the scheduler needs. The type-specific
    fields live in `attributes`, validated against the type's own schema, and
    the scheduler never reads them — they exist to *derive* the common fields
    and to be shown back to the operator in the explanation.
    """

    workload_id: str
    name: str
    type: WorkloadType
    earliest_start: datetime
    deadline: datetime | None
    duration_hours: float
    power_kw: float
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    flexibility: Flexibility | None = None
    work_amount: float = 1.0
    work_unit: str = "tasks"
    priority: float = 1.0
    facilities: tuple[str, ...] = ()
    attributes: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    max_cost: float | None = None
    max_carbon_kg: float | None = None
    #: Where duration and power came from. Defaults to ESTIMATED because a
    #: figure typed into a form is an estimate, and this project does not
    #: promote typed input to a measurement.
    provenance: str = "ESTIMATED"

    def __post_init__(self) -> None:
        if not self.workload_id.strip():
            raise ValueError("workload_id is required")
        if not self.name.strip():
            raise ValueError("name is required")
        if self.earliest_start.tzinfo is None:
            raise ValueError("earliest_start must be timezone-aware")
        if self.deadline is not None:
            if self.deadline.tzinfo is None:
                raise ValueError("deadline must be timezone-aware")
            if self.deadline <= self.earliest_start:
                raise ValueError("deadline must be after earliest_start")
        for name in ("duration_hours", "power_kw", "work_amount", "priority"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
        if self.work_unit not in WORK_UNITS:
            raise ValueError(
                f"work_unit must be one of {sorted(WORK_UNITS)}")
        for name in ("max_cost", "max_carbon_kg"):
            value = getattr(self, name)
            if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.flexibility is None:
            object.__setattr__(self, "flexibility",
                               definition(self.type).default_flexibility)

    @property
    def definition(self) -> WorkloadDefinition:
        return definition(self.type)

    @property
    def continuous(self) -> bool:
        """Revenue-earning work with no completion deadline.

        Kept as a property rather than a field so it cannot drift from the
        type's own definition — mining is continuous because mining is
        continuous, not because someone ticked a box.
        """
        return self.definition.continuous

    @property
    def energy_kwh(self) -> float:
        return self.power_kw * self.duration_hours

    def fits_window(self) -> bool:
        """Is there room to finish before the deadline at all?"""
        if self.deadline is None:
            return True
        available = (self.deadline - self.earliest_start).total_seconds() / 3600
        return available >= self.duration_hours

    def slack_hours(self) -> float:
        """Time available beyond the run itself. This is the whole lever.

        A workload with zero slack cannot be moved, however cheap another hour
        is. The trace replay in this project measured why that matters: jobs
        longer than their window consume most of the energy, so the saving
        from timing is bounded by the ratio of slack to duration.
        """
        if self.deadline is None:
            return math.inf
        available = (self.deadline - self.earliest_start).total_seconds() / 3600
        return max(0.0, available - self.duration_hours)

    def public_dict(self) -> dict:
        return {
            "workload_id": self.workload_id,
            "name": self.name,
            "type": self.type.value,
            "type_label": self.definition.label,
            "earliest_start": self.earliest_start.isoformat(),
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "duration_hours": round(self.duration_hours, 4),
            "power_kw": round(self.power_kw, 4),
            "energy_kwh": round(self.energy_kwh, 4),
            "work_amount": self.work_amount,
            "work_unit": self.work_unit,
            "priority": self.priority,
            "continuous": self.continuous,
            "slack_hours": (None if math.isinf(self.slack_hours())
                            else round(self.slack_hours(), 3)),
            "splittable": self.flexibility.splittable,
            "attributes": dict(self.attributes),
            "provenance": self.provenance,
        }


#: Derives (duration_hours, power_kw, work_amount, work_unit) from a type's
#: own fields. Returning None for duration or power means "the caller must
#: supply it" — the deriver refuses rather than inventing a number, which is
#: the same rule the rest of this project follows.
Deriver = Callable[[dict, "ResourceRequest"], dict]


def _derive_mining(attributes: dict, resources: ResourceRequest) -> dict:
    """Hash rate and J/TH fix power exactly, so nothing is estimated.

    This is the one type where power is *known* rather than guessed: a miner's
    efficiency in joules per terahash multiplied by its hash rate in terahashes
    per second is watts, by definition. No form field can improve on that, so
    a user-supplied power figure is ignored in favour of the physics.
    """
    hash_rate = attributes.get("hash_rate_th_s") or 0.0
    efficiency = attributes.get("efficiency_j_per_th") or 0.0
    watts = hash_rate * efficiency          # TH/s * J/TH = J/s = W
    return {
        "power_kw": watts / 1000 if watts > 0 else None,
        "duration_hours": None,             # continuous: caller sets the window
        "work_amount": hash_rate * 3600 if hash_rate > 0 else None,
        "work_unit": "terahashes",
        "provenance": "SPEC",               # from the miner's datasheet
    }


def _derive_rendering(attributes: dict, resources: ResourceRequest) -> dict:
    """Frames divided by however many render at once.

    The parallel divisor is the honest version of "finishes sooner": more
    workers genuinely shorten the wall clock. Price does not appear here.
    """
    frames = attributes.get("frame_count") or 0
    seconds = attributes.get("seconds_per_frame") or 0.0
    parallel = max(1, int(attributes.get("max_parallel_frames") or 1))
    total_seconds = frames * seconds / parallel
    return {
        "duration_hours": total_seconds / 3600 if total_seconds > 0 else None,
        "work_amount": float(frames) if frames else None,
        "work_unit": "frames",
    }


def _derive_hpc(attributes: dict, resources: ResourceRequest) -> dict:
    core_hours = attributes.get("core_hours") or 0.0
    cores = resources.cpu_cores or (attributes.get("nodes") or 1)
    return {
        "duration_hours": core_hours / cores if core_hours and cores else None,
        "work_amount": core_hours or None,
        "work_unit": "core_hours",
    }


def _derive_data_processing(attributes: dict, resources: ResourceRequest) -> dict:
    dataset = attributes.get("dataset_gb") or 0.0
    throughput = attributes.get("throughput_gb_per_hour") or 0.0
    return {
        "duration_hours": dataset / throughput if dataset and throughput else None,
        "work_amount": dataset or None,
        "work_unit": "gigabytes",
    }


def _derive_inference(attributes: dict, resources: ResourceRequest) -> dict:
    requests = attributes.get("request_count") or 0
    per_request = attributes.get("tokens_per_request") or 0
    total = requests * per_request
    return {
        "work_amount": float(total) if total else None,
        "work_unit": "tokens",
    }


def _derive_custom(attributes: dict, resources: ResourceRequest) -> dict:
    return {"work_amount": attributes.get("work_amount") or None,
            "work_unit": "tasks"}


DERIVERS: dict[WorkloadType, Deriver] = {
    WorkloadType.MINING: _derive_mining,
    WorkloadType.RENDERING: _derive_rendering,
    WorkloadType.HPC: _derive_hpc,
    WorkloadType.DATA_PROCESSING: _derive_data_processing,
    WorkloadType.AI_INFERENCE: _derive_inference,
    WorkloadType.CUSTOM: _derive_custom,
}


class WorkloadRefused(ValueError):
    """The workload as described cannot be turned into a schedulable job."""


def build(workload_id: str, name: str, workload_type: WorkloadType | str,
          earliest_start: datetime, *,
          deadline: datetime | None = None,
          duration_hours: float | None = None,
          power_kw: float | None = None,
          resources: ResourceRequest | None = None,
          flexibility: Flexibility | None = None,
          attributes: dict | None = None,
          priority: float = 1.0,
          facilities: tuple[str, ...] = (),
          depends_on: tuple[str, ...] = (),
          max_cost: float | None = None,
          max_carbon_kg: float | None = None,
          work_amount: float | None = None,
          work_unit: str | None = None,
          provenance: str = "ESTIMATED") -> WorkloadSpec:
    """Validate a workload of any type and fill in what its type can derive.

    Explicit arguments always win over derived ones — except mining power,
    which is fixed by physics and where a typed-in figure would be strictly
    worse than the datasheet calculation.

    Refuses rather than defaults. A workload with no duration and no way to
    derive one is not given "1 hour"; it is rejected with the reason, because
    a scheduler fed an invented duration produces a plausible schedule for
    work that does not exist.
    """
    spec_definition = definition(workload_type)
    resolved_type = spec_definition.type
    resources = resources or ResourceRequest()
    # A missing or malformed type-specific field and an underivable duration
    # are the same thing to a caller: this workload cannot be scheduled. One
    # exception type means callers do not have to catch two.
    try:
        validated = spec_definition.validate_fields(attributes)
    except ValueError as error:
        raise WorkloadRefused(
            f"{spec_definition.label}: {error}") from error

    derived: dict = {}
    deriver = DERIVERS.get(resolved_type)
    if deriver is not None:
        derived = {k: v for k, v in deriver(validated, resources).items()
                   if v is not None}

    # Mining power is physics, not preference.
    if resolved_type is WorkloadType.MINING and "power_kw" in derived:
        power_kw = derived["power_kw"]
        provenance = derived.get("provenance", provenance)
    else:
        power_kw = power_kw if power_kw is not None else derived.get("power_kw")

    duration_hours = (duration_hours if duration_hours is not None
                      else derived.get("duration_hours"))
    work_amount = (work_amount if work_amount is not None
                   else derived.get("work_amount", 1.0))
    work_unit = (work_unit or derived.get("work_unit")
                 or spec_definition.default_work_unit)

    if duration_hours is None:
        raise WorkloadRefused(
            f"{spec_definition.label} needs a duration. Either supply "
            f"duration_hours, or fill the fields it derives from "
            f"({', '.join(s.key for s in spec_definition.fields) or 'none'}). "
            f"A default would produce a confident schedule for imaginary work.")
    if power_kw is None:
        raise WorkloadRefused(
            f"{spec_definition.label} needs an estimated power draw in kW. "
            f"Nothing here can infer it, and guessing it would make every "
            f"cost and carbon figure downstream meaningless.")

    if spec_definition.continuous and deadline is None:
        # A continuous workload has no completion deadline by nature. It still
        # needs a window to be evaluated over, which the caller supplies.
        deadline = earliest_start + timedelta(hours=duration_hours)

    return WorkloadSpec(
        workload_id=workload_id, name=name, type=resolved_type,
        earliest_start=earliest_start, deadline=deadline,
        duration_hours=float(duration_hours), power_kw=float(power_kw),
        resources=resources, flexibility=flexibility,
        work_amount=float(work_amount), work_unit=work_unit,
        priority=priority, facilities=tuple(facilities),
        attributes=validated, depends_on=tuple(depends_on),
        max_cost=max_cost, max_carbon_kg=max_carbon_kg, provenance=provenance,
    )


def to_planning_candidates(spec: WorkloadSpec, placements) -> tuple:
    """Turn one workload into the `PlanningCandidate`s the planner consumes.

    `placements` is an iterable of (key, hardware, market, location, series,
    currency, pue) describing where this work *could* run. The workload
    contributes duration and power; the placement contributes the grid data.
    Nothing about the workload's type reaches the planner, which is the point.

    **Duration does not vary with price.** Every candidate carries the same
    `runtime_hours`. A cheap window does not make hardware faster, and the
    only reason two candidates here differ in duration is different hardware.
    """
    from core.planner import PlanningCandidate

    candidates = []
    for placement in placements:
        candidates.append(PlanningCandidate(
            key=placement["key"],
            hardware=placement.get("hardware", "unspecified"),
            market=placement.get("market", "GB"),
            location=placement.get("location", "national"),
            series=placement["series"],
            runtime_hours=placement.get("runtime_hours", spec.duration_hours),
            it_power_kw=placement.get("it_power_kw", spec.power_kw),
            pue=placement.get("pue", 1.2),
            memory_ok=placement.get("memory_ok", True),
            currency=placement.get("currency", "GBP"),
            hardware_provenance=placement.get("hardware_provenance",
                                              spec.provenance),
            grid_provenance=placement.get("grid_provenance", "MEASURED"),
            notes=tuple(placement.get("notes", ())),
        ))
    return tuple(candidates)


def to_portfolio_job(spec: WorkloadSpec, candidates):
    """Turn one workload into the `PortfolioJob` the multi-job scheduler takes.

    Continuous workloads are refused here on purpose. `core/portfolio.py`
    schedules work that finishes by a deadline and maximises operator utility;
    mining has neither property. Forcing it through this path would produce a
    schedule that looks valid and answers the wrong question. Use
    `core/mining.py` instead.
    """
    from core.portfolio import PortfolioJob

    if spec.continuous:
        raise WorkloadRefused(
            f"{spec.definition.label} is continuous revenue-earning work with "
            f"no completion deadline, so the deadline-based portfolio "
            f"scheduler is the wrong tool. Use core.mining.dispatch, which "
            f"compares revenue against cost per interval instead.")
    if spec.deadline is None:
        raise WorkloadRefused(
            f"{spec.name} has no deadline, so there is no window to schedule "
            f"it in. Supply one, or model it as base load.")
    if not spec.fits_window():
        raise WorkloadRefused(
            f"{spec.name} runs for {spec.duration_hours:.2f} h but only "
            f"{(spec.deadline - spec.earliest_start).total_seconds()/3600:.2f} h "
            f"is available before its deadline. It cannot finish in time on "
            f"any schedule, so no placement exists.")
    return PortfolioJob(
        job_id=spec.workload_id,
        candidates=tuple(candidates),
        earliest_start=spec.earliest_start,
        deadline=spec.deadline,
        work_amount=spec.work_amount,
        work_unit=spec.work_unit,
        utility=spec.priority,
        mandatory=spec.priority >= 1.0,
        depends_on=spec.depends_on,
    )
