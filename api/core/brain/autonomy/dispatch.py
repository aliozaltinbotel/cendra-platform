"""Pure dispatch-time autonomy resolution.

This module translates the dispatch identity the runtime gateway already
has (tool id or ``agent:{id}``) into the per-workflow autonomy semantics
the platform should audit at dispatch.  It is intentionally pure:

- workflow-kind vocabulary comes only from the tenant registry;
- unresolved identities fail safe to OBSERVE semantics without touching
  the autonomy store;
- the runtime gateway owns posture, caching and persistence wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.models import AutonomyState, state_rank
from core.brain.autonomy.workflow_kinds import WorkflowKindRegistry

__all__ = [
    "DispatchAutonomy",
    "DispatchSemantics",
    "resolve_dispatch_autonomy",
]


class DispatchSemantics(StrEnum):
    """Dispatch posture implied by one autonomy rung."""

    DRAFT_ONLY = "draft_only"
    HOLD = "hold"
    PROCEED = "proceed"


@dataclass(frozen=True, slots=True)
class DispatchAutonomy:
    """Resolved autonomy state for one dispatch."""

    workflow: str | None
    state: AutonomyState
    semantics: DispatchSemantics
    rationale: str


_SEMANTICS_BY_STATE_RANK = {
    state_rank(AutonomyState.OBSERVE): DispatchSemantics.DRAFT_ONLY,
    state_rank(AutonomyState.SEMI_AUTO): DispatchSemantics.HOLD,
    state_rank(AutonomyState.AUTOPILOT): DispatchSemantics.PROCEED,
}


def _semantics_for(state: AutonomyState) -> DispatchSemantics:
    try:
        return _SEMANTICS_BY_STATE_RANK[state_rank(state)]
    except KeyError as exc:
        raise ValueError(f"unsupported autonomy state: {state!r}") from exc


def resolve_dispatch_autonomy(
    *,
    engine: AutonomyEngine,
    registry: WorkflowKindRegistry,
    property_id: str,
    dispatch_identity: str,
) -> DispatchAutonomy:
    """Resolve one dispatch to workflow rung semantics.

    ``dispatch_identity`` resolves only through the registry alias table.
    Unknown identities fail safe to OBSERVE / DRAFT_ONLY and intentionally
    skip ``engine.state_for`` so pre-seeded deployments stay unchanged.
    """
    workflow = registry.resolve_event(dispatch_identity)
    if workflow is None:
        return DispatchAutonomy(
            workflow=None,
            state=AutonomyState.OBSERVE,
            semantics=DispatchSemantics.DRAFT_ONLY,
            rationale=(f"dispatch identity {dispatch_identity!r} unresolved in workflow registry"),
        )

    state = engine.state_for(property_id=property_id, workflow=workflow)
    semantics = _semantics_for(state)
    return DispatchAutonomy(
        workflow=workflow,
        state=state,
        semantics=semantics,
        rationale=(f"dispatch identity {dispatch_identity!r} resolved to workflow {workflow!r}"),
    )
