"""Tests for the J fix — semantic refusal reason in DEFER/DENY rationale.

Mümin 2026-05-08 round-3 (complaint #J):
    "DEFER records should contain information about why they're
    DEFER, in /patterns/rules endpoint."

Pre-fix the rationale string was tautological — "PM deferred
(waited / did not respond) for {scenario} — {N} supporting case(s)
— when {conditions}".  The PM had no way to tell whether the
deferrals were caused by missing documents, unpaid balances,
pending owner approval, or hard policy blocks.

The fix threads supporting :class:`DecisionCase` instances into
:func:`brain_engine.patterns.extractor._build_rationale` and reads
``case.extracted_entities['refusal_signals']`` (populated by the
bootstrap pipeline via :class:`RefusalExtractor`).  For DEFER /
DENY / BLOCK rules the dominant ``RefusalType`` becomes a
human-readable "most often because …" clause embedded in the
rationale.

Property-agnostic — the aggregation walks per-case data, not
property identifiers, so the same enrichment applies for every
property the bootstrap pipeline replays.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.patterns.extractor import (
    _build_rationale,
    _summarise_refusal_signals,
)
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _case_with_refusal(
    *,
    refusal_type: str,
    conditional: str = "",
    property_id: str = "prop-1",
    action: DecisionType = DecisionType.DEFER,
) -> DecisionCase:
    """Build a learnable case carrying one structured refusal signal."""
    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id=property_id,
        owner_id="",
        decision=DecisionAction(action_type=action, params={}),
        outcome=CaseOutcome(
            approved=True,
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
        extracted_entities={
            "refusal_signals": [
                {
                    "type": refusal_type,
                    "language": "en",
                    "trigger": "cannot share without ID",
                    "conditional": conditional,
                    "confidence": 0.9,
                },
            ],
        },
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


def _case_no_refusal(
    *,
    action: DecisionType = DecisionType.DEFER,
) -> DecisionCase:
    """Build a case whose extracted_entities carry no refusal signals."""
    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id="prop-1",
        owner_id="",
        decision=DecisionAction(action_type=action, params={}),
        outcome=CaseOutcome(
            approved=True,
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
        extracted_entities={},
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# _summarise_refusal_signals — aggregator unit contract
# ---------------------------------------------------------------------------


def test_summarise_returns_empty_for_no_signals() -> None:
    """No refusal signals → empty string (caller falls back gracefully)."""
    assert _summarise_refusal_signals([_case_no_refusal()]) == ""


def test_summarise_returns_empty_for_empty_iterable() -> None:
    """Empty input must not blow up — empty string keeps the call safe."""
    assert _summarise_refusal_signals([]) == ""


def test_summarise_picks_dominant_type() -> None:
    """The most-frequent refusal type wins."""
    cases = [
        *(
            _case_with_refusal(refusal_type="requires_document")
            for _ in range(3)
        ),
        _case_with_refusal(refusal_type="requires_payment"),
    ]
    summary = _summarise_refusal_signals(cases)
    assert summary.startswith("most often because ")
    assert "document" in summary.lower()
    assert "payment" not in summary.lower()


def test_summarise_includes_first_conditional_example() -> None:
    """Conditional clause is appended in quotes when present."""
    cases = [
        _case_with_refusal(
            refusal_type="requires_document",
            conditional="without passport",
        ),
        _case_with_refusal(refusal_type="requires_document"),
    ]
    summary = _summarise_refusal_signals(cases)
    assert '(e.g. "without passport")' in summary


def test_summarise_tie_break_is_alphabetical() -> None:
    """Equal counts collapse deterministically by enum-value order."""
    cases = [
        _case_with_refusal(refusal_type="requires_payment"),
        _case_with_refusal(refusal_type="hard_block"),
    ]
    summary = _summarise_refusal_signals(cases)
    # ``hard_block`` sorts before ``requires_payment`` alphabetically.
    assert "blocked by an explicit property policy" in summary


def test_summarise_handles_unknown_type_gracefully() -> None:
    """Unknown refusal types are slug-formatted, not silently dropped."""
    cases = [_case_with_refusal(refusal_type="custom_new_type")]
    summary = _summarise_refusal_signals(cases)
    assert "custom new type" in summary


@pytest.mark.parametrize(
    "malformed",
    [
        {"extracted_entities": {"refusal_signals": "not-a-list"}},
        {"extracted_entities": {"refusal_signals": [None, 42, "str"]}},
        {"extracted_entities": {"refusal_signals": [{"no_type": "yes"}]}},
    ],
)
def test_summarise_tolerates_malformed_signal_payload(
    malformed: dict,
) -> None:
    """Bootstrap data corruption never raises — empty result fall-through."""
    case = DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id="prop-1",
        owner_id="",
        decision=DecisionAction(action_type=DecisionType.DEFER, params={}),
        outcome=CaseOutcome(
            approved=True,
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
        extracted_entities=malformed["extracted_entities"],
    )
    assert _summarise_refusal_signals([case]) == ""


# ---------------------------------------------------------------------------
# _build_rationale — semantic clause emitted only for refusal-aware actions
# ---------------------------------------------------------------------------


def test_defer_rationale_includes_refusal_reason() -> None:
    """DEFER rule rationale carries the dominant refusal phrase."""
    cases = [
        _case_with_refusal(
            refusal_type="requires_document",
            conditional="until passport arrives",
            action=DecisionType.DEFER,
        )
        for _ in range(5)
    ]
    rationale = _build_rationale(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        action_type=DecisionType.DEFER,
        conditions={"stage": {"operator": "eq", "value": "in_stay"}},
        support=5,
        counterexamples=0,
        cases=cases,
    )
    assert "PM deferred (waited / did not respond)" in rationale
    assert "most often because" in rationale
    assert "document" in rationale.lower()


def test_deny_rationale_includes_refusal_reason() -> None:
    """DENY rule rationale also surfaces the refusal context."""
    cases = [
        _case_with_refusal(
            refusal_type="hard_block",
            action=DecisionType.DENY,
        )
        for _ in range(4)
    ]
    rationale = _build_rationale(
        scenario=Scenario.LATE_CHECKOUT,
        action_type=DecisionType.DENY,
        conditions={},
        support=4,
        counterexamples=0,
        cases=cases,
    )
    assert "chose deny" in rationale
    assert "blocked by an explicit property policy" in rationale


def test_inform_rationale_omits_refusal_reason() -> None:
    """Non-refusal actions stay focused on what happened, not why."""
    cases = [
        _case_with_refusal(
            refusal_type="requires_document",
            action=DecisionType.INFORM,
        )
        for _ in range(5)
    ]
    rationale = _build_rationale(
        scenario=Scenario.LATE_CHECKOUT,
        action_type=DecisionType.INFORM,
        conditions={"stage": {"operator": "eq", "value": "in_stay"}},
        support=5,
        counterexamples=0,
        cases=cases,
    )
    assert "most often because" not in rationale


def test_rationale_without_cases_uses_pre_j_format() -> None:
    """Backward compat — callers that don't pass ``cases`` keep old shape."""
    rationale = _build_rationale(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        action_type=DecisionType.DEFER,
        conditions={},
        support=3,
        counterexamples=0,
    )
    assert "most often because" not in rationale
    assert "PM deferred" in rationale


def test_rationale_falls_back_when_cases_have_no_signals() -> None:
    """No refusal signals → no semantic clause, structural rationale only."""
    cases = [_case_no_refusal() for _ in range(5)]
    rationale = _build_rationale(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        action_type=DecisionType.DEFER,
        conditions={},
        support=5,
        counterexamples=0,
        cases=cases,
    )
    assert "most often because" not in rationale
    assert "PM deferred" in rationale


# ---------------------------------------------------------------------------
# Property-agnostic — same rationale enrichment for any property identifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "property_id",
    ["323133", "uuid-prop-9", "tenant-2/property-7"],
)
def test_rationale_enrichment_is_property_agnostic(property_id: str) -> None:
    """Aggregation reads case fields, not property identifiers.

    The enrichment must produce the same semantic clause regardless
    of which property the cases belong to — Mümin's complaint
    reproduced on 323133 but the fix has to work on every property
    that ever runs through bootstrap.
    """
    cases = [
        _case_with_refusal(
            refusal_type="requires_payment",
            property_id=property_id,
            action=DecisionType.DEFER,
        )
        for _ in range(3)
    ]
    rationale = _build_rationale(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        action_type=DecisionType.DEFER,
        conditions={},
        support=3,
        counterexamples=0,
        cases=cases,
    )
    assert "PM was waiting for the guest payment to clear" in rationale
