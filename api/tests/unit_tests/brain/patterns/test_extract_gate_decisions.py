"""Tests for the structured ``gate_decisions`` surface on /extract.

Mümin 2026-05-08 round-3 feedback complained that ``/patterns/extract``
exposes only an unstructured ``skipped_reasons`` list of strings such
as ``"action_inform_confidence_0.43_below_0.60"``, which a PM has to
parse with regex to understand why a candidate did not become a rule.
DEFER cases get even less surface — ``defer_count`` is a number with
no breakdown of which gate (support / confidence) the deferred bucket
hit.

The fix introduces a parallel ``gate_decisions`` tuple on
:class:`core.brain.patterns.extractor.ExtractionResult` and a
matching :class:`GateDecisionDTO` on the API response.  Each entry
covers one action group with structured fields: ``action``,
``accepted``, ``reason``, ``support_count``, ``counterexample_count``,
``confidence``, ``min_support`` and ``min_confidence``.

These tests pin three contracts:

* Accepted action emits ``accepted=True`` with ``reason=None`` and
  the actual confidence.
* Action skipped on support emits ``reason="insufficient_support"``
  with ``confidence=None`` (gate fired before computation).
* Action skipped on confidence emits ``reason="low_confidence"``
  with the computed confidence rounded to 4 dp.

The existing ``skipped_reasons`` surface is left intact for
backward compatibility — clients that have not migrated to the
structured form keep working.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from core.brain.patterns.extractor import (
    GateDecision,
    PatternExtractor,
)
from core.brain.patterns.models import (
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
)
from core.brain.patterns.store import InMemoryDecisionCaseStore


def _positive_outcome() -> CaseOutcome:
    """Outcome that satisfies ``is_learnable`` AND ``is_positive_signal``."""
    return CaseOutcome(
        resolution_type=ResolutionType.PM_APPROVED,
        approved=True,
        successful=True,
    )


def _negative_outcome() -> CaseOutcome:
    """Outcome that satisfies ``is_learnable`` AND ``is_negative_signal``."""
    return CaseOutcome(
        resolution_type=ResolutionType.PM_DENIED,
        approved=False,
        successful=False,
    )


def _make_case(
    *,
    scenario: Scenario = "access_code_release",
    action: DecisionType = DecisionType.INFORM,
    property_id: str = "prop-1",
    owner_id: str = "owner-1",
    outcome: CaseOutcome | None = None,
    pms_snapshot: dict | None = None,
) -> DecisionCase:
    """Build a learnable :class:`DecisionCase` with PM_APPROVED by default."""
    return DecisionCase(
        stage="in_stay",
        scenario=scenario,
        property_id=property_id,
        owner_id=owner_id,
        decision=DecisionAction(action_type=action, params={}),
        outcome=outcome if outcome is not None else _positive_outcome(),
        source=CaseSource.LIVE,
        pms_snapshot=pms_snapshot or {},
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


def _seed_store(
    cases: list[DecisionCase],
) -> InMemoryDecisionCaseStore:
    """Insert ``cases`` into a fresh in-memory store and return it."""
    store = InMemoryDecisionCaseStore()
    for case in cases:
        store.store(case)
    return store


# ---------------------------------------------------------------------------
# ExtractionResult — new gate_decisions field
# ---------------------------------------------------------------------------


def test_extraction_result_gate_decisions_defaults_empty() -> None:
    """Default ``gate_decisions`` is an empty tuple."""
    from core.brain.patterns.extractor import ExtractionResult

    result = ExtractionResult()
    assert result.gate_decisions == ()


def test_gate_decision_dataclass_is_frozen() -> None:
    """:class:`GateDecision` must be immutable for safe sharing."""
    decision = GateDecision(
        action="inform",
        accepted=True,
        reason=None,
        support_count=10,
        counterexample_count=2,
        confidence=0.83,
        min_support=3,
        min_confidence=0.6,
    )
    with pytest.raises(FrozenInstanceError):
        decision.action = "approve"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Accepted action — gate decision carries the rule's confidence
# ---------------------------------------------------------------------------


def test_accepted_action_emits_gate_decision_with_confidence() -> None:
    """Group that forms a rule emits ``accepted=True`` GateDecision."""
    cases = [_make_case() for _ in range(10)]
    store = _seed_store(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.5,
    )

    result = extractor.extract_patterns(
        scenario="access_code_release",
        property_id="prop-1",
        owner_id="owner-1",
    )

    inform_decisions = [g for g in result.gate_decisions if g.action == "inform"]
    assert len(inform_decisions) == 1
    decision = inform_decisions[0]
    assert decision.accepted is True
    assert decision.reason is None
    assert decision.support_count == 10
    assert decision.counterexample_count == 0
    assert decision.confidence is not None
    assert 0.0 < decision.confidence <= 1.0
    assert decision.min_support == 3
    assert decision.min_confidence == 0.5


# ---------------------------------------------------------------------------
# Skipped on insufficient support
# ---------------------------------------------------------------------------


def test_insufficient_support_emits_gate_decision_without_confidence() -> None:
    """Support gate fires before confidence — reason marks the gate."""
    # 5 INFORM cases (passes min_support=3) and 1 DENY case (fails
    # min_support=3 since DENY is not the DEFER lower bar).  The
    # DENY group will be marked insufficient_support without ever
    # computing confidence.
    cases = [
        *(_make_case(action=DecisionType.INFORM) for _ in range(5)),
        _make_case(action=DecisionType.DENY),
    ]
    store = _seed_store(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.5,
    )

    result = extractor.extract_patterns(
        scenario="access_code_release",
        property_id="prop-1",
        owner_id="owner-1",
    )

    deny_decisions = [g for g in result.gate_decisions if g.action == "deny"]
    assert len(deny_decisions) == 1
    decision = deny_decisions[0]
    assert decision.accepted is False
    assert decision.reason == "insufficient_support"
    assert decision.support_count == 1
    assert decision.confidence is None
    assert decision.min_support == 3


# ---------------------------------------------------------------------------
# Skipped on low confidence
# ---------------------------------------------------------------------------


def test_low_confidence_emits_gate_decision_with_actual_score() -> None:
    """Confidence gate emits a decision carrying the computed score."""
    # 5 INFORM positive + 5 negative makes confidence ~ 0.5, below
    # the 0.9 threshold we set.  The INFORM group should reach the
    # confidence gate (support=5 >= 3) and be rejected with the
    # actual score available on the GateDecision.
    cases = [
        *(_make_case(action=DecisionType.INFORM) for _ in range(5)),
        *(_make_case(action=DecisionType.INFORM, outcome=_negative_outcome()) for _ in range(5)),
    ]
    store = _seed_store(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.9,
    )

    result = extractor.extract_patterns(
        scenario="access_code_release",
        property_id="prop-1",
        owner_id="owner-1",
    )

    inform_decisions = [g for g in result.gate_decisions if g.action == "inform"]
    assert len(inform_decisions) == 1
    decision = inform_decisions[0]
    assert decision.accepted is False
    assert decision.reason == "low_confidence"
    assert decision.confidence is not None
    assert decision.confidence < 0.9
    assert decision.support_count == 5
    assert decision.counterexample_count == 5
    assert decision.min_confidence == 0.9


# ---------------------------------------------------------------------------
# DEFER lower bar — accepted with single supporting case
# ---------------------------------------------------------------------------


def test_defer_uses_lower_support_threshold() -> None:
    """DEFER group passes the support gate at ``support=1``.

    Without :data:`_MIN_SUPPORT_DEFER` the DEFER group would fall
    to ``insufficient_support`` at ``min_support=3``.  With it,
    the GateDecision carries ``min_support=1`` and reaches the
    confidence stage (so ``confidence`` is computed, not ``None``).
    Whether the group ultimately becomes a rule depends on the
    Wilson-bounded confidence at small samples — that is a separate
    gate, not the carve-out we are pinning here.
    """
    cases = [
        *(_make_case(action=DecisionType.INFORM) for _ in range(5)),
        _make_case(action=DecisionType.DEFER),
    ]
    store = _seed_store(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.5,
    )

    result = extractor.extract_patterns(
        scenario="access_code_release",
        property_id="prop-1",
        owner_id="owner-1",
    )

    defer_decisions = [g for g in result.gate_decisions if g.action == "defer"]
    assert len(defer_decisions) == 1
    decision = defer_decisions[0]
    assert decision.support_count == 1
    assert decision.min_support == 1
    # Lower bar must be visible to the consumer regardless of
    # whether the confidence stage subsequently rejects the group.
    assert decision.confidence is not None


# ---------------------------------------------------------------------------
# Backward compatibility — skipped_reasons still populated
# ---------------------------------------------------------------------------


def test_skipped_reasons_remains_populated_alongside_decisions() -> None:
    """Existing string surface stays intact for legacy clients."""
    cases = [
        *(_make_case(action=DecisionType.INFORM) for _ in range(5)),
        _make_case(action=DecisionType.DENY),
    ]
    store = _seed_store(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.5,
    )

    result = extractor.extract_patterns(
        scenario="access_code_release",
        property_id="prop-1",
        owner_id="owner-1",
    )

    assert any("deny" in reason for reason in result.skipped_reasons)
    assert len(result.gate_decisions) >= 1
