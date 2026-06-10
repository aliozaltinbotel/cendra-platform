"""Invariants of Property Twin value objects."""

from __future__ import annotations

from datetime import date

import pytest

from brain_engine.property_twin.models import (
    RolloutTrace,
    TwinAction,
    TwinObservation,
    TwinState,
)


def _state(**overrides: object) -> TwinState:
    base: dict[str, object] = {
        "property_id": "p1",
        "as_of": date(2026, 5, 10),
        "occupancy_30d": 0.5,
        "adr": 100.0,
        "review_score": 4.5,
    }
    base.update(overrides)
    return TwinState(**base)  # type: ignore[arg-type]


def test_state_validation_property_id() -> None:
    with pytest.raises(ValueError, match="property_id"):
        _state(property_id="")


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"occupancy_30d": -0.1}, "occupancy_30d"),
        ({"occupancy_30d": 1.5}, "occupancy_30d"),
        ({"adr": -1.0}, "adr"),
        ({"review_score": -0.1}, "review_score"),
        ({"review_score": 5.5}, "review_score"),
        ({"maintenance_debt": -0.1}, "maintenance_debt"),
    ],
    ids=[
        "neg_occ",
        "high_occ",
        "neg_adr",
        "neg_review",
        "high_review",
        "neg_maintenance",
    ],
)
def test_state_numeric_bounds(
    override: dict[str, float],
    match: str,
) -> None:
    """Numeric bounds are enforced fail-fast."""
    with pytest.raises(ValueError, match=match):
        _state(**override)


def test_action_requires_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        TwinAction(
            kind="",
            magnitude=1.0,
            effective_on=date(2026, 5, 10),
        )


def test_observation_review_added_validation() -> None:
    """``review_added`` outside ``[0, 5]`` raises."""
    with pytest.raises(ValueError, match="review_added"):
        TwinObservation(
            property_id="p",
            observed_on=date(2026, 5, 10),
            occupancy_delta=0.0,
            revenue=10.0,
            review_added=6.0,
        )


def test_rollout_trace_states_match_actions_count() -> None:
    """Trace requires actions length = states length - 1."""
    s = _state()
    with pytest.raises(ValueError, match="actions length"):
        RolloutTrace(states=(s, s), actions=())


def test_rollout_trace_must_have_at_least_one_state() -> None:
    with pytest.raises(ValueError, match="states must"):
        RolloutTrace(states=(), actions=())
