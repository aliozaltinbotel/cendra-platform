"""Single factory that assembles a fully-wired :class:`ExecutionOrchestrator`.

The factory bridges the resolver layer (Branch 2) and the runtime
hot-path (Branch 3): callers hand it a small bag of stores + engines
already configured at process start, and receive the §10 priority
chain pre-loaded with every resolver tier whose dependencies are
present.  Tiers whose dependencies are missing fall back to the
no-op resolver baked into :class:`ExecutionOrchestrator`, so the
chain stays well-formed even on minimally configured environments
(unit tests, sandbox, Cendra-only deployments).

Why a dedicated factory instead of constructor wiring at the call
site?

* The hot path (``ConversationService``) should not know about the
  resolver protocol — it only consumes :meth:`ExecutionOrchestrator.decide`.
* The resolver constructors expose a couple of optional knobs
  (scenario→action mapping, scenario→staticity-field mapping,
  manual directive store) that are expected to grow as Branches 4+
  ship; centralising them here keeps the API server lean.
* Tests can call :func:`build_execution_orchestrator` with an
  in-memory bag of stores and assert the same chain wiring the
  production server uses, without duplicating boilerplate.
"""

from __future__ import annotations

from collections.abc import Mapping

import structlog

from brain_engine.approval.models import ActionType
from brain_engine.blockers.engine import BlockerEngine
from brain_engine.orchestrator.priority_chain import (
    ExecutionOrchestrator,
    preference_tier_from_owner_profile,
)
from brain_engine.orchestrator.resolvers import (
    DEFAULT_SCENARIO_TO_ACTION,
    DEFAULT_SCENARIO_TO_STATICITY_FIELD,
    FeatureBuilder,
    ManualDirectiveStore,
    blocker_tier_from_engine,
    learned_tier_from_pattern_store,
    manual_tier_from_store,
    safety_tier_from_guard,
)
from brain_engine.owner_profile.store import OwnerProfileStore
from brain_engine.patterns.store import PatternRuleStore
from brain_engine.staticity.guard import StaticityGuard

__all__ = ["build_execution_orchestrator"]


logger = structlog.get_logger(__name__)


def build_execution_orchestrator(
    *,
    owner_profile_store: OwnerProfileStore,
    blocker_engine: BlockerEngine | None = None,
    staticity_guard: StaticityGuard | None = None,
    pattern_store: PatternRuleStore | None = None,
    feature_builder: FeatureBuilder | None = None,
    manual_directive_store: ManualDirectiveStore | None = None,
    scenario_to_action: Mapping[str, ActionType] | None = None,
    scenario_to_staticity_field: Mapping[str, str] | None = None,
) -> ExecutionOrchestrator:
    """Assemble a fully-wired :class:`ExecutionOrchestrator`.

    Tiers whose dependencies are absent fall back to a no-op resolver
    inside :class:`ExecutionOrchestrator`.  This means a caller can
    bring up the orchestrator with only the preference tier wired
    (the minimum the §10 chain needs to short-circuit somewhere
    other than the ask fallback) and incrementally enable richer
    tiers as their backing stores come online.

    Args:
        owner_profile_store: Backing store for the preference tier
            (tier 5).  Required — the chain has no useful behaviour
            without it.
        blocker_engine: Live blocker engine (tier 2).  When absent,
            the blocker tier no-ops.
        staticity_guard: Field-staticity guard (tier 3).  When
            absent, the safety tier no-ops.
        pattern_store: Learned PatternRule store (tier 4).  When
            absent, the learned tier no-ops.
        feature_builder: Feature builder for the learned tier.  When
            ``None`` and ``pattern_store`` is supplied, the resolver
            falls back to :func:`default_feature_builder` (entities
            + PMS snapshot).  Ignored when ``pattern_store`` is
            ``None``.
        manual_directive_store: Manual / immutable directive store
            (tier 1).  When absent, the manual tier no-ops.
        scenario_to_action: Override for the default scenario→action
            mapping consumed by the blocker tier.  ``None`` selects
            :data:`DEFAULT_SCENARIO_TO_ACTION`.
        scenario_to_staticity_field: Override for the default
            scenario→staticity-field mapping consumed by the safety
            tier.  ``None`` selects
            :data:`DEFAULT_SCENARIO_TO_STATICITY_FIELD`.

    Returns:
        The configured :class:`ExecutionOrchestrator`, ready for the
        hot path to call :meth:`ExecutionOrchestrator.decide`.
    """
    log = logger.bind(component="execution_orchestrator_wiring")

    action_map = scenario_to_action or DEFAULT_SCENARIO_TO_ACTION
    staticity_map = (
        scenario_to_staticity_field or DEFAULT_SCENARIO_TO_STATICITY_FIELD
    )

    manual = (
        manual_tier_from_store(manual_directive_store)
        if manual_directive_store is not None
        else None
    )
    blocker = (
        blocker_tier_from_engine(
            blocker_engine,
            scenario_to_action=action_map,
        )
        if blocker_engine is not None
        else None
    )
    safety = (
        safety_tier_from_guard(
            staticity_guard,
            scenario_to_field=staticity_map,
        )
        if staticity_guard is not None
        else None
    )
    learned = (
        learned_tier_from_pattern_store(
            pattern_store,
            feature_builder=feature_builder,
        )
        if pattern_store is not None
        else None
    )

    log.info(
        "orchestrator.assembled",
        manual_wired=manual is not None,
        blocker_wired=blocker is not None,
        safety_wired=safety is not None,
        learned_wired=learned is not None,
    )

    return ExecutionOrchestrator(
        preference=preference_tier_from_owner_profile(owner_profile_store),
        manual=manual,
        blocker=blocker,
        safety=safety,
        learned=learned,
    )


