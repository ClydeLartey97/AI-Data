"""Named objectives, and the two that refuse to be faked as weights."""
from __future__ import annotations

import pytest

from core.objectives import (CATALOGUE, Objective, ObjectiveUnavailable,
                             ObjectiveWeights, catalogue, resolve)


def test_every_requested_objective_exists():
    assert {o.value for o in Objective} == {
        "min_cost", "min_carbon", "max_renewable", "balanced",
        "max_profit", "custom"}


def test_cost_and_carbon_objectives_are_opposites():
    cost, carbon = resolve("min_cost"), resolve("min_carbon")
    assert (cost.cost_weight, cost.carbon_weight) == (1.0, 0.0)
    assert (carbon.cost_weight, carbon.carbon_weight) == (0.0, 1.0)


def test_balanced_weights_both_equally():
    weights = resolve("balanced")
    assert weights.cost_weight == weights.carbon_weight


def test_max_renewable_refuses_rather_than_pretending_to_be_min_carbon():
    """Grid carbon and on-site generation are different signals. A site can
    be at its cleanest grid hour while its own array produces nothing, so
    approximating one with the other answers a different question."""
    with pytest.raises(ObjectiveUnavailable, match="core.energy"):
        resolve("max_renewable")


def test_max_profit_refuses_and_points_at_the_mining_dispatcher():
    with pytest.raises(ObjectiveUnavailable, match="core.mining"):
        resolve("max_profit")


def test_custom_needs_explicit_weights():
    with pytest.raises(ObjectiveUnavailable, match="explicit"):
        resolve("custom")


def test_custom_weights_pass_straight_through():
    supplied = ObjectiveWeights(0.7, 0.2, 0.1)
    assert resolve("custom", supplied) is supplied


def test_an_unknown_objective_names_the_known_ones():
    with pytest.raises(ObjectiveUnavailable, match="unknown objective"):
        resolve("cheapest_possible")


def test_weights_cannot_all_be_zero():
    with pytest.raises(ValueError, match="at least one weight"):
        ObjectiveWeights(0.0, 0.0, 0.0)


def test_negative_weights_are_refused():
    with pytest.raises(ValueError, match="non-negative"):
        ObjectiveWeights(-1.0, 1.0, 0.0)


def test_the_catalogue_marks_which_objectives_need_other_machinery():
    entries = {e["objective"]: e for e in catalogue()}
    assert entries["balanced"]["expressible_as_weights"] is True
    for key in ("max_renewable", "max_profit"):
        assert entries[key]["expressible_as_weights"] is False
        assert entries[key]["handled_by"]
