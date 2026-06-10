"""End-to-end behaviour of :class:`PropertyTwin`."""

from __future__ import annotations

from datetime import date

import pytest

from brain_engine.property_twin.models import (
    TwinAction,
    TwinState,
)
from brain_engine.property_twin.protocols import (
    IdentityWorldModel,
)
from brain_engine.property_twin.twin import PropertyTwin


def _state(**overrides: object) -> TwinState:
    base: dict[str, object] = {
        "property_id": "p1",
        "as_of": date(2026, 5, 10),
        "occupancy_30d": 0.6,
        "adr": 150.0,
        "review_score": 4.5,
        "maintenance_debt": 6.0,
    }
    base.update(overrides)
    return TwinState(**base)  # type: ignore[arg-type]


@pytest.fixture
def twin() -> PropertyTwin:
    return PropertyTwin(world_model=IdentityWorldModel())


def test_empty_actions_returns_single_state_trace(
    twin: PropertyTwin,
) -> None:
    """No actions → trace with start state only."""
    start = _state()
    trace = twin.imagine(start=start, actions=())
    assert trace.states == (start,)
    assert trace.actions == ()


def test_action_walk_produces_n_plus_one_states(
    twin: PropertyTwin,
) -> None:
    """N actions produce N+1 states in the trace."""
    start = _state()
    actions = (
        TwinAction(
            kind="price_change",
            magnitude=10.0,
            effective_on=date(2026, 5, 11),
        ),
        TwinAction(
            kind="price_change",
            magnitude=5.0,
            effective_on=date(2026, 5, 12),
        ),
    )
    trace = twin.imagine(start=start, actions=actions)
    assert len(trace.states) == 3
    assert len(trace.actions) == 2


def test_terminal_state_reflects_action_chain(
    twin: PropertyTwin,
) -> None:
    """Two ``price_change`` actions accumulate."""
    start = _state(adr=100.0)
    actions = (
        TwinAction(
            kind="price_change",
            magnitude=10.0,
            effective_on=date(2026, 5, 11),
        ),
        TwinAction(
            kind="price_change",
            magnitude=15.0,
            effective_on=date(2026, 5, 12),
        ),
    )
    trace = twin.imagine(start=start, actions=actions)
    assert trace.states[-1].adr == 125.0


def test_action_in_past_rejected(twin: PropertyTwin) -> None:
    """Action whose ``effective_on`` precedes the cursor raises."""
    start = _state(as_of=date(2026, 5, 10))
    with pytest.raises(ValueError, match="effective_on"):
        twin.imagine(
            start=start,
            actions=(
                TwinAction(
                    kind="price_change",
                    magnitude=1.0,
                    effective_on=date(2026, 5, 9),
                ),
            ),
        )
