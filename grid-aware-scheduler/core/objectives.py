"""Named optimisation objectives, mapped onto the weights the planner already has.

`core/planner.py` takes `cost_weight`, `carbon_weight` and `delay_weight` and
optimises exactly against them. That is the right primitive, but it is not
what an operator thinks in: nobody wants to reason about whether 0.7/0.3/0.0
expresses their policy. This module names the handful of policies people
actually hold and translates each into those weights.

**Why this is a translation and not a new engine.** The planner is exhaustive
and exact over hardware, location and start time. Adding objectives there
would mean touching the search. Adding them here means the search is unchanged
and only its scoring inputs differ, so an objective can never introduce a bug
into placement itself.

**Two objectives cannot be expressed as weights, and they say so.**
`MAX_RENEWABLE` needs on-site generation data, which lives in
`core/energy.py`'s dispatch rather than in the price/carbon series the planner
scores. `MAX_PROFIT` only means anything for revenue-earning work, which today
is mining alone and is handled by `core/mining.py`. Both are declared here so
the interface can offer them, and both carry an explicit route to the module
that actually implements them rather than being silently approximated by a
weight that would answer a different question.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Objective(str, Enum):
    MIN_COST = "min_cost"
    MIN_CARBON = "min_carbon"
    MAX_RENEWABLE = "max_renewable"
    BALANCED = "balanced"
    MAX_PROFIT = "max_profit"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ObjectiveWeights:
    """What the planner is actually asked to minimise."""

    cost_weight: float
    carbon_weight: float
    delay_weight: float

    def __post_init__(self) -> None:
        for name in ("cost_weight", "carbon_weight", "delay_weight"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.cost_weight + self.carbon_weight + self.delay_weight <= 0:
            raise ValueError("at least one weight must be positive")


@dataclass(frozen=True)
class ObjectiveSpec:
    objective: Objective
    label: str
    description: str
    weights: ObjectiveWeights | None
    #: Set when the objective needs machinery outside the planner's weights.
    handled_by: str = ""

    @property
    def expressible_as_weights(self) -> bool:
        return self.weights is not None


CATALOGUE: dict[Objective, ObjectiveSpec] = {
    Objective.MIN_COST: ObjectiveSpec(
        objective=Objective.MIN_COST,
        label="Lowest electricity cost",
        description="Place work in the cheapest windows the deadline allows. "
                    "Measured on 392 days of GB data, this misses the carbon "
                    "target 41% of the time — the cheapest decile is also the "
                    "cleanest only 59% of the time.",
        weights=ObjectiveWeights(1.0, 0.0, 0.0),
    ),
    Objective.MIN_CARBON: ObjectiveSpec(
        objective=Objective.MIN_CARBON,
        label="Lowest carbon emissions",
        description="Place work in the cleanest windows. Can cost more than "
                    "running immediately, which the planner surfaces rather "
                    "than hiding.",
        weights=ObjectiveWeights(0.0, 1.0, 0.0),
    ),
    Objective.BALANCED: ObjectiveSpec(
        objective=Objective.BALANCED,
        label="Balanced cost and carbon",
        description="Equal weight. Sensible default when no policy has been "
                    "stated, and honest about being a compromise rather than "
                    "an optimum for either.",
        weights=ObjectiveWeights(0.5, 0.5, 0.0),
    ),
    Objective.MAX_RENEWABLE: ObjectiveSpec(
        objective=Objective.MAX_RENEWABLE,
        label="Maximum use of on-site renewable energy",
        description="Match load to the site's own generation rather than to "
                    "the grid signal. Needs declared generation: with no "
                    "on-site sources this objective has nothing to maximise.",
        weights=None,
        handled_by="core.energy.dispatch_energy with dispatch priority "
                   "'renewable', over a site declared in core.site_profile",
    ),
    Objective.MAX_PROFIT: ObjectiveSpec(
        objective=Objective.MAX_PROFIT,
        label="Maximum operating profit",
        description="Only meaningful for work that earns revenue while it "
                    "runs. Today that is mining alone; a deadline-bound job "
                    "has a cost but no revenue, so 'profit' would just be "
                    "cost with a sign flipped.",
        weights=None,
        handled_by="core.mining.dispatch, which compares revenue against "
                   "energy cost, operating cost and export opportunity",
    ),
    Objective.CUSTOM: ObjectiveSpec(
        objective=Objective.CUSTOM,
        label="Custom weighted objective",
        description="Supply cost, carbon and delay weights directly.",
        weights=None,
        handled_by="caller-supplied weights via resolve(..., custom=...)",
    ),
}


class ObjectiveUnavailable(ValueError):
    """This objective cannot be served with what has been provided."""


def resolve(objective: Objective | str,
            custom: ObjectiveWeights | None = None) -> ObjectiveWeights:
    """The weights to hand the planner, or a refusal explaining why not.

    Refusing is the point. Silently approximating "maximum renewable" as
    "minimum carbon" would return a schedule that looks like an answer to the
    question asked and is not one — grid carbon and on-site generation are
    different signals, and a site can be at its cleanest grid hour while its
    own array produces nothing.
    """
    if isinstance(objective, str):
        try:
            objective = Objective(objective)
        except ValueError as error:
            raise ObjectiveUnavailable(
                f"unknown objective {objective!r}; known objectives are "
                f"{[o.value for o in Objective]}") from error
    spec = CATALOGUE[objective]
    if objective is Objective.CUSTOM:
        if custom is None:
            raise ObjectiveUnavailable(
                "the custom objective needs explicit cost, carbon and delay "
                "weights")
        return custom
    if spec.weights is None:
        raise ObjectiveUnavailable(
            f"{spec.label} cannot be expressed as planner weights. "
            f"It is served by {spec.handled_by}. Approximating it with "
            f"weights would answer a different question.")
    return spec.weights


def catalogue() -> list[dict]:
    """Every objective, shaped for a selector."""
    return [
        {
            "objective": spec.objective.value,
            "label": spec.label,
            "description": spec.description,
            "weights": (None if spec.weights is None else {
                "cost": spec.weights.cost_weight,
                "carbon": spec.weights.carbon_weight,
                "delay": spec.weights.delay_weight,
            }),
            "expressible_as_weights": spec.expressible_as_weights,
            "handled_by": spec.handled_by,
        }
        for spec in CATALOGUE.values()
    ]
