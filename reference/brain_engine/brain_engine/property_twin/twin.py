"""Property Twin runtime + imagined rollouts.

The :class:`PropertyTwin` is a thin façade over a
:class:`WorldModel`: callers feed it a starting state and a
sequence of :class:`TwinAction` records; the twin walks the world
model forward and produces a :class:`RolloutTrace`.

The twin is *stateless* between calls — every rollout starts from
the explicit ``state`` argument the caller passes in.  This keeps
the surface trivially deterministic and lets multiple planners
share a single twin instance without lock contention.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from brain_engine.property_twin.models import (
    RolloutTrace,
    TwinAction,
    TwinState,
)
from brain_engine.property_twin.protocols import WorldModel


__all__ = ["PropertyTwin"]


logger = structlog.get_logger(__name__)


class PropertyTwin:
    """Forward-simulating shadow of one property portfolio."""

    def __init__(self, *, world_model: WorldModel) -> None:
        self._world_model = world_model
        self._log = logger.bind(component="property_twin")

    def imagine(
        self,
        *,
        start: TwinState,
        actions: Sequence[TwinAction],
    ) -> RolloutTrace:
        """Walk ``actions`` through the world model from ``start``.

        Args:
            start: Initial :class:`TwinState`.
            actions: Ordered tuple of actions to apply.  An empty
                sequence returns a trace with one state and no
                actions — useful for "what is the state right now"
                queries.
        """
        states: list[TwinState] = [start]
        applied: list[TwinAction] = []
        cursor = start
        for action in actions:
            if action.effective_on < cursor.as_of:
                raise ValueError(
                    "action.effective_on must be >= "
                    "current state's as_of"
                )
            cursor = self._world_model.step(
                state=cursor, action=action,
            )
            states.append(cursor)
            applied.append(action)
        self._log.info(
            "twin.rollout",
            property_id=start.property_id,
            steps=len(actions),
            terminal_adr=cursor.adr,
        )
        return RolloutTrace(
            states=tuple(states),
            actions=tuple(applied),
        )
