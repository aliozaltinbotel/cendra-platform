"""Tests for :meth:`CaseOutcome.from_decision_type` factory.

Mümin round-4 #4 follow-up: live conversations were producing
:class:`DecisionCase` instances with the default empty
:class:`CaseOutcome`, so
:attr:`DecisionCase.has_outcome` was ``False`` and the
PatternExtractor structurally skipped every live case.  The fix
adds a single derivation classmethod shared by the bootstrap and
live paths.  These tests pin the contract:

* deliberate PM decisions (APPROVE / CHARGE / OFFER / RELEASE /
  DENY / BLOCK) → ``PM_APPROVED + successful + approved``;
* ESCALATE → ``ESCALATED + not successful``;
* conversational decisions (INFORM / ASK / QUOTE / DEFER /
  DISPATCH / FETCH_LIVE_DATA) → ``AUTO_RESOLVED + successful``;
* the result always satisfies
  :attr:`DecisionCase.has_outcome` so the case is admitted into
  mining;
* the historical extractor shim
  :func:`_outcome_for_historical` delegates to the new classmethod
  with no behaviour change.
"""

from __future__ import annotations

import pytest

from brain_engine.onboarding.historical_case_extractor import (
    _outcome_for_historical,
)
from brain_engine.patterns.models import (
    CaseOutcome,
    DecisionType,
    ResolutionType,
)


_DELIBERATE = (
    DecisionType.APPROVE,
    DecisionType.CHARGE,
    DecisionType.OFFER,
    DecisionType.RELEASE,
    DecisionType.DENY,
    DecisionType.BLOCK,
)

_CONVERSATIONAL = (
    DecisionType.INFORM,
    DecisionType.ASK,
    DecisionType.QUOTE,
    DecisionType.DEFER,
    DecisionType.DISPATCH,
    DecisionType.FETCH_LIVE_DATA,
)


@pytest.mark.parametrize("decision_type", _DELIBERATE)
def test_deliberate_decisions_map_to_pm_approved(
    decision_type: DecisionType,
) -> None:
    """Every deliberate PM decision collapses to PM_APPROVED."""
    outcome = CaseOutcome.from_decision_type(decision_type)
    assert outcome.resolution_type is ResolutionType.PM_APPROVED
    assert outcome.successful is True
    assert outcome.approved is True
    assert outcome.human_overrode is False


def test_escalate_maps_to_escalated_unsuccessful() -> None:
    """ESCALATE maps to ESCALATED with successful=False."""
    outcome = CaseOutcome.from_decision_type(DecisionType.ESCALATE)
    assert outcome.resolution_type is ResolutionType.ESCALATED
    assert outcome.successful is False
    assert outcome.human_overrode is False


@pytest.mark.parametrize("decision_type", _CONVERSATIONAL)
def test_conversational_decisions_map_to_auto_resolved(
    decision_type: DecisionType,
) -> None:
    """Conversational decisions collapse to AUTO_RESOLVED."""
    outcome = CaseOutcome.from_decision_type(decision_type)
    assert outcome.resolution_type is ResolutionType.AUTO_RESOLVED
    assert outcome.successful is True
    assert outcome.human_overrode is False


@pytest.mark.parametrize(
    "decision_type", _DELIBERATE + _CONVERSATIONAL + (DecisionType.ESCALATE,),
)
def test_every_decision_type_satisfies_has_outcome(
    decision_type: DecisionType,
) -> None:
    """Every supported decision_type produces a non-empty outcome.

    The PatternExtractor admits a case to mining only when
    :attr:`DecisionCase.has_outcome` is True, which it derives
    from ``outcome.resolution_type is not None``.
    """
    outcome = CaseOutcome.from_decision_type(decision_type)
    assert outcome.resolution_type is not None


def test_factory_returns_fresh_instances() -> None:
    """Two factory calls return independent value objects."""
    first = CaseOutcome.from_decision_type(DecisionType.APPROVE)
    second = CaseOutcome.from_decision_type(DecisionType.APPROVE)
    assert first == second
    assert first is not second


def test_deny_outcome_is_positive_signal() -> None:
    """DENY outcome routes into the positive pool for rule mining.

    This is the round-4 #4 invariant: a DENY case must reach the
    PatternExtractor's positive pool so N consistent refusals can
    form a DENY rule.  ``is_positive_signal`` returning True is
    the contract that anchors that routing.
    """
    outcome = CaseOutcome.from_decision_type(DecisionType.DENY)
    assert outcome.is_positive_signal is True
    assert outcome.is_negative_signal is False


def test_block_outcome_is_positive_signal() -> None:
    """BLOCK collapses identically to DENY for the same reason."""
    outcome = CaseOutcome.from_decision_type(DecisionType.BLOCK)
    assert outcome.is_positive_signal is True


def test_historical_shim_delegates_to_classmethod() -> None:
    """:func:`_outcome_for_historical` returns the same value object."""
    for decision_type in (
        DecisionType.APPROVE,
        DecisionType.DENY,
        DecisionType.INFORM,
        DecisionType.ESCALATE,
    ):
        shim = _outcome_for_historical(decision_type)
        canonical = CaseOutcome.from_decision_type(decision_type)
        assert shim == canonical
