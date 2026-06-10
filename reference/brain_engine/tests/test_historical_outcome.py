"""Tests for ``_outcome_for_historical`` — bootstrap PM-decision outcomes.

Mümin 2026-05-08 round-3 (complaint #C):
    "10 pre_booking + 10 in_stay fake messages were inserted on
    323133.  Negative PM responses for pre_booking, positive for
    in_stay.  After bootstrap there should have been DENY records
    for pre_booking, but only the in_stay ASK rule formed."

Root cause was in
``brain_engine.onboarding.historical_case_extractor._outcome_for_historical``:
the mapping returned ``successful=False`` + ``PM_DENIED`` for
DENY/BLOCK decisions, so :meth:`CaseOutcome.is_negative_signal`
returned True and ``PatternExtractor._split_by_signal`` routed the
cases to the counterexample pool — they never reached the
action-grouping loop, so a DENY rule was structurally impossible.

The fix treats every PM-authored deliberate decision as a
successful PM action: the action_type carries grant vs refuse, and
the outcome only records that PM took a deliberate action.  Same
code runs for every property, so the bug (and the fix) is
property-agnostic — these tests pin both halves of that contract.

Live overrides are unaffected — ``human_overrode=True`` upstream
still routes overridden cases to the negative pool via
:meth:`CaseOutcome.is_negative_signal`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.onboarding.historical_case_extractor import (
    _outcome_for_historical,
)
from brain_engine.patterns.extractor import PatternExtractor
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.store import InMemoryDecisionCaseStore

# ---------------------------------------------------------------------------
# _outcome_for_historical — direct unit contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision_type",
    [
        DecisionType.APPROVE,
        DecisionType.CHARGE,
        DecisionType.OFFER,
        DecisionType.RELEASE,
        DecisionType.DENY,
        DecisionType.BLOCK,
    ],
)
def test_deliberate_decisions_are_pm_approved_successful(
    decision_type: DecisionType,
) -> None:
    """Every deliberate PM decision becomes a successful PM_APPROVED outcome.

    Critically includes ``DENY`` and ``BLOCK`` — the previous
    mapping marked them ``successful=False`` and routed cases to
    the counterexample pool, structurally preventing DENY rules
    from forming.
    """
    outcome = _outcome_for_historical(decision_type)
    assert outcome.successful is True
    assert outcome.approved is True
    assert outcome.resolution_type is ResolutionType.PM_APPROVED
    assert outcome.is_positive_signal is True
    assert outcome.human_overrode is False


def test_escalate_remains_unsuccessful() -> None:
    """ESCALATE was not a successful resolution — unchanged by the fix."""
    outcome = _outcome_for_historical(DecisionType.ESCALATE)
    assert outcome.successful is False
    assert outcome.resolution_type is ResolutionType.ESCALATED


@pytest.mark.parametrize(
    "decision_type",
    [
        DecisionType.INFORM,
        DecisionType.ASK,
        DecisionType.QUOTE,
        DecisionType.DEFER,
        DecisionType.DISPATCH,
        DecisionType.FETCH_LIVE_DATA,
    ],
)
def test_conversational_decisions_remain_auto_resolved(
    decision_type: DecisionType,
) -> None:
    """Conversational decisions still collapse to AUTO_RESOLVED."""
    outcome = _outcome_for_historical(decision_type)
    assert outcome.successful is True
    assert outcome.resolution_type is ResolutionType.AUTO_RESOLVED


# ---------------------------------------------------------------------------
# Signal routing — DENY no longer lands in the counterexample pool
# ---------------------------------------------------------------------------


def test_deny_outcome_is_positive_signal_not_negative() -> None:
    """Anchor: ``is_positive_signal`` wins so DENY reaches action grouping.

    ``elif`` in :meth:`PatternExtractor._split_by_signal` means a
    case checked positive-first cannot fall through to negative,
    so the rule mining loop sees the DENY group rather than
    treating these cases as counter-evidence.
    """
    outcome = _outcome_for_historical(DecisionType.DENY)
    assert outcome.is_positive_signal is True


# ---------------------------------------------------------------------------
# Integration — DENY cases form a DENY rule end-to-end
# ---------------------------------------------------------------------------


def _make_deny_case(*, property_id: str, stage: BookingStage) -> DecisionCase:
    """Build a historical-style DENY case for a given property + stage."""
    return DecisionCase(
        stage=stage,
        scenario=Scenario.LATE_CHECKOUT,
        property_id=property_id,
        owner_id="",
        decision=DecisionAction(action_type=DecisionType.DENY, params={}),
        outcome=_outcome_for_historical(DecisionType.DENY),
        source=CaseSource.HISTORICAL,
        pms_snapshot={"stage": stage.value},
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


async def _seed_store(
    cases: list[DecisionCase],
) -> InMemoryDecisionCaseStore:
    """Seed an in-memory store with the supplied cases."""
    store = InMemoryDecisionCaseStore()
    for case in cases:
        await store.store(case)
    return store


@pytest.mark.asyncio
async def test_ten_deny_cases_form_a_deny_rule_via_extractor() -> None:
    """End-to-end: 10 historical DENY cases produce a DENY rule.

    Reproduces Mümin's exact scenario — 10 PM-denied messages,
    no positives or negatives mixed in — and asserts the extractor
    emits a DENY rule.  Pre-fix this was structurally impossible
    because the cases landed in the counterexample pool and never
    reached :meth:`PatternExtractor._group_by_action`.
    """
    cases = [
        _make_deny_case(property_id="prop-A", stage=BookingStage.PRE_ARRIVAL)
        for _ in range(10)
    ]
    store = await _seed_store(cases)
    extractor = PatternExtractor(
        store=store, min_support=3, min_confidence=0.5,
    )

    result = await extractor.extract_patterns(
        scenario=Scenario.LATE_CHECKOUT,
        property_id="prop-A",
        owner_id="",
    )

    assert result.positive_cases == 10
    assert result.negative_cases == 0
    deny_rules = [
        rule
        for rule in result.rules
        if rule.action.action_type is DecisionType.DENY
    ]
    assert len(deny_rules) == 1, (
        "DENY rule did not form — outcome routing regressed"
    )
    assert deny_rules[0].support_count == 10


# ---------------------------------------------------------------------------
# Property-agnostic — fix applies regardless of the property_id used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "property_id",
    ["323133", "prop-X-uuid-1234", "tenant-2/property-9"],
)
async def test_deny_rule_forms_for_any_property_id(property_id: str) -> None:
    """Same outcome routing must work for any property identifier.

    Mümin reproduced the bug on 323133 but ``_outcome_for_historical``
    has no property-specific branching — the bug, and the fix, are
    global.  Parametrising over arbitrary identifiers (numeric,
    UUID-like, hierarchical) anchors that contract so future
    refactors cannot reintroduce a property-scoped regression.
    """
    cases = [
        _make_deny_case(property_id=property_id, stage=BookingStage.PRE_ARRIVAL)
        for _ in range(10)
    ]
    store = await _seed_store(cases)
    extractor = PatternExtractor(
        store=store, min_support=3, min_confidence=0.5,
    )

    result = await extractor.extract_patterns(
        scenario=Scenario.LATE_CHECKOUT,
        property_id=property_id,
        owner_id="",
    )

    deny_rules = [
        rule
        for rule in result.rules
        if rule.action.action_type is DecisionType.DENY
    ]
    assert len(deny_rules) == 1
    assert deny_rules[0].scope_id == property_id


# ---------------------------------------------------------------------------
# Live override behaviour — unaffected by the historical-outcome fix
# ---------------------------------------------------------------------------


def test_live_override_still_lands_in_negative_pool() -> None:
    """``human_overrode=True`` upstream remains a negative signal.

    The historical-outcome change only collapses synthesised
    bootstrap outcomes; live cases that record an explicit PM
    override (the engine acted, the PM disagreed) still satisfy
    :meth:`CaseOutcome.is_negative_signal` and therefore feed the
    counterexample pool used to penalise the engine's choice.
    """
    overridden = CaseOutcome(
        human_overrode=True,
        resolution_type=ResolutionType.PM_MODIFIED,
    )
    assert overridden.is_negative_signal is True
    assert overridden.is_positive_signal is False
