"""World-model + observation-store Protocols.

The :class:`WorldModel` Protocol is the seam where the v1.0
DreamerV3 + LinOSS implementation will plug in.  v0.1 ships the
:class:`IdentityWorldModel` baseline that returns the input state
unchanged plus a small heuristic adjustment derived from action
magnitude — enough for tests and for downstream callers that want
to wire an end-to-end skeleton today.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol

from brain_engine.property_twin.models import (
    TwinAction,
    TwinObservation,
    TwinState,
)


__all__ = [
    "IdentityWorldModel",
    "ObservationStore",
    "WorldModel",
]


class WorldModel(Protocol):
    """Predict the next :class:`TwinState` under an action."""

    def step(
        self,
        *,
        state: TwinState,
        action: TwinAction,
    ) -> TwinState:
        """Return the simulated next state."""
        ...


class ObservationStore(Protocol):
    """Append-only history of realised observations."""

    def record(self, observation: TwinObservation) -> None:
        """Append an observation."""
        ...

    def history(
        self,
        property_id: str,
    ) -> Sequence[TwinObservation]:
        """Return the recorded history for ``property_id``."""
        ...


class IdentityWorldModel:
    """Baseline :class:`WorldModel` for tests + bootstrap callers.

    Applies a small, deterministic adjustment per action kind
    (``"price_change"`` shifts ADR; ``"maintenance_dispatch"``
    drains maintenance_debt; everything else leaves the state
    untouched).  No randomness — the same input pair always
    produces the same output state, so tests and audit replays
    are reproducible.
    """

    def step(
        self,
        *,
        state: TwinState,
        action: TwinAction,
    ) -> TwinState:
        """Return a heuristic next state for ``action`` on ``state``."""
        next_date = action.effective_on + timedelta(days=1)
        if action.kind == "price_change":
            return _adjust_adr(
                state=state,
                action=action,
                next_date=next_date,
            )
        if action.kind == "maintenance_dispatch":
            return _drain_maintenance(
                state=state,
                action=action,
                next_date=next_date,
            )
        return _passthrough(state=state, next_date=next_date)


def _adjust_adr(
    *,
    state: TwinState,
    action: TwinAction,
    next_date,
) -> TwinState:
    new_adr = max(0.0, state.adr + action.magnitude)
    return TwinState(
        property_id=state.property_id,
        as_of=next_date,
        occupancy_30d=state.occupancy_30d,
        adr=new_adr,
        review_score=state.review_score,
        maintenance_debt=state.maintenance_debt,
        latent=dict(state.latent),
    )


def _drain_maintenance(
    *,
    state: TwinState,
    action: TwinAction,
    next_date,
) -> TwinState:
    drained = max(
        0.0, state.maintenance_debt - abs(action.magnitude),
    )
    return TwinState(
        property_id=state.property_id,
        as_of=next_date,
        occupancy_30d=state.occupancy_30d,
        adr=state.adr,
        review_score=state.review_score,
        maintenance_debt=drained,
        latent=dict(state.latent),
    )


def _passthrough(
    *,
    state: TwinState,
    next_date,
) -> TwinState:
    return TwinState(
        property_id=state.property_id,
        as_of=next_date,
        occupancy_30d=state.occupancy_30d,
        adr=state.adr,
        review_score=state.review_score,
        maintenance_debt=state.maintenance_debt,
        latent=dict(state.latent),
    )
