"""Concrete evidence sources wired over existing Brain Engine stores.

The adapters here are the only bridge between the abstract
:mod:`brain_engine.evidence.sources` Protocols and the production
stores (``DecisionCaseStore``, ``PatternRuleStore``).  Everything
else in the :mod:`brain_engine.evidence` package stays pure so that
the composer remains unit-testable without spinning up Postgres.

Both adapters are defensive:

- Unknown / missing scenarios map through :class:`Scenario` via
  ``Scenario(value)`` inside a ``try`` block; a bad value becomes no
  fetch (the composer sees an empty tuple).
- Any exception raised by the upstream store is wrapped in
  :class:`EvidenceSourceError` so the composer can isolate it.
"""

from __future__ import annotations

import structlog

from brain_engine.blockers.engine import BlockerStore
from brain_engine.blockers.models import Blocker
from brain_engine.evidence.errors import EvidenceSourceError
from brain_engine.evidence.models import (
    BlockerPick,
    CasePick,
    EvidenceQuery,
    EvidenceWeight,
    PromptPick,
    RulePick,
)
from brain_engine.gestures.models import GestureContext, MemoryPrompt
from brain_engine.gestures.prompts import MemoryPromptAggregator
from brain_engine.patterns.models import (
    DecisionCase,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.store import (
    DecisionCaseStore,
    PatternRuleStore,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Rule adapter
# ---------------------------------------------------------------------------


class PatternRuleEvidenceAdapter:
    """Bridges :class:`PatternRuleStore` to :class:`RuleEvidenceSource`."""

    def __init__(self, rule_store: PatternRuleStore) -> None:
        self._store = rule_store
        self._log = logger.bind(component="rule_evidence_adapter")

    async def fetch_rules(
        self,
        query: EvidenceQuery,
    ) -> tuple[RulePick, ...]:
        """Return active rules matching the query scope."""
        scenario = _safe_scenario(query.scenario)
        if scenario is None and query.scenario is not None:
            return ()
        scope, scope_id = _derive_scope(query)
        try:
            rules = await self._store.get_active_rules(
                scenario=scenario,
                scope=scope,
                scope_id=scope_id,
            )
        except Exception as exc:
            raise EvidenceSourceError("rules", str(exc)) from exc
        return tuple(_rule_to_pick(r) for r in rules)


def _derive_scope(
    query: EvidenceQuery,
) -> tuple[PatternScope | None, str | None]:
    """Pick the narrowest scope implied by the query."""
    if query.property_id is not None:
        return PatternScope.PROPERTY, query.property_id
    if query.owner_id is not None:
        return PatternScope.OWNER, query.owner_id
    return None, None


def _rule_to_pick(rule: PatternRule) -> RulePick:
    """Convert a :class:`PatternRule` into a :class:`RulePick`."""
    return RulePick(
        pattern_id=rule.pattern_id,
        scenario=rule.scenario.value,
        scope=rule.scope.value,
        scope_id=rule.scope_id,
        confidence=rule.confidence,
        support_count=rule.support_count,
        counterexample_ratio=rule.counterexample_ratio,
        risk_level=rule.risk_level.value,
        execution_mode=rule.execution_mode.value,
        weight=EvidenceWeight.SUPPORTING,
        action_type=rule.action.action_type.value,
    )


# ---------------------------------------------------------------------------
# Case adapter
# ---------------------------------------------------------------------------


class DecisionCaseEvidenceAdapter:
    """Bridges :class:`DecisionCaseStore` to :class:`CaseEvidenceSource`."""

    def __init__(self, case_store: DecisionCaseStore) -> None:
        self._store = case_store
        self._log = logger.bind(component="case_evidence_adapter")

    async def fetch_cases(
        self,
        query: EvidenceQuery,
    ) -> tuple[CasePick, ...]:
        """Return prior cases matching scenario + property/owner."""
        scenario = _safe_scenario(query.scenario)
        if scenario is None and query.scenario is not None:
            return ()
        try:
            cases = await self._store.search(
                scenario=scenario,
                property_id=query.property_id,
                owner_id=query.owner_id,
                limit=query.limit,
            )
        except Exception as exc:
            raise EvidenceSourceError("cases", str(exc)) from exc
        return tuple(
            _case_to_pick(c) for c in cases
            if c.case_id != query.decision_id
        )


def _case_to_pick(case: DecisionCase) -> CasePick:
    """Convert a :class:`DecisionCase` into a :class:`CasePick`."""
    weight = _weight_for_case(case)
    return CasePick(
        case_id=case.case_id,
        scenario=case.scenario.value,
        stage=case.stage.value,
        decision_type=case.decision.action_type.value,
        weight=weight,
        resolution_type=(
            case.outcome.resolution_type.value
            if case.outcome.resolution_type is not None
            else None
        ),
        revenue_impact=case.outcome.revenue_impact,
        occurred_at=case.created_at,
    )


def _weight_for_case(case: DecisionCase) -> EvidenceWeight:
    """Translate a case outcome into an evidence weight."""
    if case.outcome.is_positive_signal:
        return EvidenceWeight.SUPPORTING
    if case.outcome.is_negative_signal:
        return EvidenceWeight.CONTRADICTING
    return EvidenceWeight.NEUTRAL


# ---------------------------------------------------------------------------
# Blocker adapter
# ---------------------------------------------------------------------------


class BlockerEvidenceAdapter:
    """Bridges :class:`BlockerStore` to :class:`BlockerEvidenceSource`."""

    def __init__(self, blocker_store: BlockerStore) -> None:
        self._store = blocker_store
        self._log = logger.bind(component="blocker_evidence_adapter")

    async def fetch_blockers(
        self,
        query: EvidenceQuery,
    ) -> tuple[BlockerPick, ...]:
        """Return active blockers for the query's property scope."""
        if not query.property_id:
            return ()
        try:
            blockers = await self._store.get_active(
                property_id=query.property_id,
            )
        except Exception as exc:
            raise EvidenceSourceError("blockers", str(exc)) from exc
        return tuple(_blocker_to_pick(b) for b in blockers)


def _blocker_to_pick(blocker: Blocker) -> BlockerPick:
    """Convert a :class:`Blocker` into a :class:`BlockerPick`."""
    return BlockerPick(
        blocker_id=blocker.blocker_id,
        blocker_type=blocker.blocker_type.value,
        severity=blocker.severity.value,
        reason=blocker.description,
        introduced_at=blocker.created_at,
        resolves_on=None,
    )


# ---------------------------------------------------------------------------
# Prompt adapter
# ---------------------------------------------------------------------------


class MemoryPromptEvidenceAdapter:
    """Bridges :class:`MemoryPromptAggregator` to :class:`PromptEvidenceSource`."""

    def __init__(self, aggregator: MemoryPromptAggregator) -> None:
        self._aggregator = aggregator
        self._log = logger.bind(component="prompt_evidence_adapter")

    async def fetch_prompts(
        self,
        query: EvidenceQuery,
    ) -> tuple[PromptPick, ...]:
        """Return ranked memory prompts for the query scope."""
        scenario = _safe_scenario(query.scenario)
        if scenario is None or not query.property_id:
            return ()
        context = GestureContext(
            property_id=query.property_id,
            scenario=scenario,
            guest_id=query.guest_id,
            owner_id=query.owner_id,
        )
        try:
            prompts = await self._aggregator.collect(context)
        except Exception as exc:
            raise EvidenceSourceError("prompts", str(exc)) from exc
        return tuple(_prompt_to_pick(p) for p in prompts)


def _prompt_to_pick(prompt: MemoryPrompt) -> PromptPick:
    """Convert a :class:`MemoryPrompt` into a :class:`PromptPick`."""
    return PromptPick(
        prompt_id=prompt.prompt_id,
        source=prompt.source.value,
        kind=prompt.kind.value,
        text=prompt.text,
        relevance=prompt.relevance,
        reference_id=prompt.reference_id,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_scenario(raw: str | None) -> Scenario | None:
    """Coerce a free-form scenario string into the enum."""
    if raw is None:
        return None
    try:
        return Scenario(raw)
    except ValueError:
        return None
