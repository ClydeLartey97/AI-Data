"""Quality-constrained model, hardware, location and time routing.

Task difficulty does not directly determine which accelerator should run a
fixed model. For a fixed model and token shape, semantic difficulty does not
change the FLOPs or memory traffic in a defensible way. Difficulty matters
first as a quality requirement: harder or higher-risk work may require a model
deployment that achieves a higher measured evaluation score. Only after that
quality floor is enforced should the planner minimise cost, carbon and delay.

This module encodes that boundary. It consumes evaluation results supplied by
an external, versioned evaluation process. It never invents a quality score or
classifies user content itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.planner import PlanResult, PlanningCandidate, PlanningRequest, optimise

MEASURED = "MEASURED"
ESTIMATED = "ESTIMATED"


@dataclass(frozen=True)
class QualityCandidate:
    """One evaluated model deployment and its placement estimate."""

    planning: PlanningCandidate
    model_key: str
    workload_class: str
    quality_score: float
    evaluation_suite: str
    evaluation_version: str
    quality_provenance: str = MEASURED
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.quality_score <= 1:
            raise ValueError("quality_score must be between 0 and 1")
        if not self.model_key.strip():
            raise ValueError("model_key is required")
        if not self.workload_class.strip():
            raise ValueError("workload_class is required")
        if not self.evaluation_suite.strip() or not self.evaluation_version.strip():
            raise ValueError("a versioned evaluation suite is required")
        if self.quality_provenance not in {MEASURED, ESTIMATED}:
            raise ValueError("quality_provenance must be MEASURED or ESTIMATED")


@dataclass(frozen=True)
class RoutingRequest:
    workload_class: str
    minimum_quality: float
    planning: PlanningRequest
    require_measured_quality: bool = True

    def __post_init__(self) -> None:
        if not self.workload_class.strip():
            raise ValueError("workload_class is required")
        if not 0 <= self.minimum_quality <= 1:
            raise ValueError("minimum_quality must be between 0 and 1")


@dataclass
class RouteResult:
    selected: QualityCandidate
    plan: PlanResult
    eligible: list[QualityCandidate]
    rejected: dict[str, str] = field(default_factory=dict)


def optimise_route(candidates: list[QualityCandidate],
                   request: RoutingRequest) -> RouteResult:
    """Enforce measured quality first, then run the exact placement planner."""
    keys = [candidate.planning.key for candidate in candidates]
    if len(keys) != len(set(keys)):
        raise ValueError("quality routing requires unique planning candidate keys")

    matching = [
        candidate for candidate in candidates
        if candidate.workload_class == request.workload_class
    ]
    suites = {
        (candidate.evaluation_suite, candidate.evaluation_version)
        for candidate in matching
    }
    if len(suites) > 1:
        raise ValueError(
            "quality scores from different evaluation suites or versions cannot be ranked"
        )

    eligible: list[QualityCandidate] = []
    rejected: dict[str, str] = {}
    for candidate in candidates:
        key = candidate.planning.key
        if candidate.workload_class != request.workload_class:
            rejected[key] = "no evaluation for requested workload class"
        elif request.require_measured_quality and candidate.quality_provenance != MEASURED:
            rejected[key] = "quality evidence is not measured"
        elif candidate.quality_score < request.minimum_quality:
            rejected[key] = (
                f"quality {candidate.quality_score:.3f} is below required "
                f"{request.minimum_quality:.3f}"
            )
        else:
            eligible.append(candidate)

    if not eligible:
        detail = "; ".join(f"{key}: {reason}" for key, reason in rejected.items())
        raise ValueError(f"no quality-eligible route{': ' + detail if detail else ''}")

    plan = optimise([candidate.planning for candidate in eligible], request.planning)
    rejected.update(plan.rejected)
    by_key = {candidate.planning.key: candidate for candidate in eligible}
    selected = by_key[plan.selected.candidate.key]
    plan.rejected = rejected.copy()
    return RouteResult(selected=selected, plan=plan, eligible=eligible,
                       rejected=rejected)
