"""Tests for the Stage-2 LLM hint pathway in DecisionClassifier.

Stage 2 closes Aybüke's classification-quality concern by letting
``BusinessFlagClassifier`` (the existing per-turn LLM call) emit
``scenario_hint`` and ``decision_type_hint`` fields.
``DecisionClassifier`` consumes them through three defensive
layers — feature-flag gate, enum validation, fall-back to the
keyword chain — so the keyword path stays available as a safety
net.

These tests pin the contracts the hint pathway offers:

1. **Backward compatibility** — empty hint behaves exactly like
   the pre-Stage-2 keyword chain.
2. **Hint priority** — a valid hint wins over the keyword chain,
   even when the keyword chain detected a different scenario.
3. **Defensive validation** — invalid (non-enum) hints fall
   through to keywords without raising.
4. **Feature flag** — ``BRAIN_LLM_HINTS_ENABLED=false`` restores
   pure keyword behaviour at runtime.
5. **Decision-type hint** — same defensive pattern for
   ``decision_type_hint``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from brain_engine.conversation.models import BusinessFlags
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.models import DecisionType, Scenario


@pytest.fixture
def classifier() -> DecisionClassifier:
    return DecisionClassifier()


@pytest.fixture
def hints_enabled() -> Iterator[None]:
    """Ensure the hint feature flag is ON for the test."""
    previous = os.environ.pop("BRAIN_LLM_HINTS_ENABLED", None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("BRAIN_LLM_HINTS_ENABLED", None)
        else:
            os.environ["BRAIN_LLM_HINTS_ENABLED"] = previous


@pytest.fixture
def hints_disabled() -> Iterator[None]:
    """Force the hint feature flag OFF for the test."""
    previous = os.environ.get("BRAIN_LLM_HINTS_ENABLED")
    os.environ["BRAIN_LLM_HINTS_ENABLED"] = "false"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("BRAIN_LLM_HINTS_ENABLED", None)
        else:
            os.environ["BRAIN_LLM_HINTS_ENABLED"] = previous


# ---------------------------------------------------------------------------
# Backward compatibility — empty hint reuses keyword chain
# ---------------------------------------------------------------------------


def test_empty_scenario_hint_falls_back_to_general(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    """No hint + no business flags ⇒ GENERAL (keyword chain retired)."""
    result = classifier.classify(
        business_flags=BusinessFlags(),
        message_text="What is the door code?",
    )
    assert result.scenario is Scenario.GENERAL


def test_empty_decision_hint_falls_back_to_inform(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    """No decision hint + non-empty response ⇒ INFORM default."""
    result = classifier.classify(
        business_flags=BusinessFlags(),
        message_text="hi",
        response_text="we will get back to you closer to your check-in",
    )
    assert result.decision_type is DecisionType.INFORM


# ---------------------------------------------------------------------------
# Hint priority — valid hint wins
# ---------------------------------------------------------------------------


def test_valid_scenario_hint_overrides_keyword_chain(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    flags = BusinessFlags(scenario_hint="amenity_exception")
    result = classifier.classify(
        business_flags=flags,
        message_text="some unrelated message",
    )
    assert result.scenario is Scenario.AMENITY_EXCEPTION


def test_valid_scenario_hint_wins_when_keyword_disagrees(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    flags = BusinessFlags(scenario_hint="lost_item")
    result = classifier.classify(
        business_flags=flags,
        message_text="What is the door code?",
    )
    assert result.scenario is Scenario.LOST_ITEM


def test_valid_decision_type_hint_overrides_keyword_chain(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    flags = BusinessFlags(decision_type_hint="defer")
    result = classifier.classify(
        business_flags=flags,
        message_text="hi",
        response_text="here is the door code 1234",
    )
    assert result.decision_type is DecisionType.DEFER


# ---------------------------------------------------------------------------
# Defensive validation — invalid hint never raises
# ---------------------------------------------------------------------------


def test_unknown_scenario_hint_falls_back_to_general(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    """Invalid hint silently falls through to the flag-derived path."""
    flags = BusinessFlags(scenario_hint="not_a_real_scenario_value")
    result = classifier.classify(
        business_flags=flags,
        message_text="What is the door code?",
    )
    assert result.scenario is Scenario.GENERAL


def test_unknown_decision_type_hint_falls_back_to_inform(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    """Invalid decision hint silently falls through to INFORM."""
    flags = BusinessFlags(decision_type_hint="not_a_real_decision")
    result = classifier.classify(
        business_flags=flags,
        message_text="hi",
        response_text="we will get back to you closer to your check-in",
    )
    assert result.decision_type is DecisionType.INFORM


def test_scenario_hint_normalises_case_and_whitespace(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    flags = BusinessFlags(scenario_hint="  Amenity_Exception  ")
    result = classifier.classify(
        business_flags=flags, message_text="anything",
    )
    assert result.scenario is Scenario.AMENITY_EXCEPTION


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_disabled_feature_flag_ignores_scenario_hint(
    classifier: DecisionClassifier,
    hints_disabled: None,
) -> None:
    """``BRAIN_LLM_HINTS_ENABLED=false`` ⇒ hint is not applied."""
    flags = BusinessFlags(scenario_hint="amenity_exception")
    result = classifier.classify(
        business_flags=flags,
        message_text="What is the door code?",
    )
    # Hint would have routed us to AMENITY_EXCEPTION when enabled;
    # flag off, no business flags set, no keyword fallback ⇒ GENERAL.
    assert result.scenario is Scenario.GENERAL


def test_disabled_feature_flag_ignores_decision_type_hint(
    classifier: DecisionClassifier,
    hints_disabled: None,
) -> None:
    """``BRAIN_LLM_HINTS_ENABLED=false`` ⇒ decision hint not applied."""
    flags = BusinessFlags(decision_type_hint="defer")
    result = classifier.classify(
        business_flags=flags,
        message_text="hi",
        response_text="approved, you can do that",
    )
    # Hint would have forced DEFER; flag off, no tools, non-empty
    # response ⇒ INFORM.
    assert result.decision_type is DecisionType.INFORM


# ---------------------------------------------------------------------------
# classify_all integration — hint applies to decision_type fan-out
# ---------------------------------------------------------------------------


def test_classify_all_applies_decision_type_hint_to_every_case(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    """The decision_type hint propagates to every fan-out case."""
    flags = BusinessFlags(
        is_additional_services=True,
        decision_type_hint="defer",
    )
    msg = "We need extra towels"
    result = classifier.classify_all(
        business_flags=flags, message_text=msg,
    )
    # is_additional_services without sub-keywords yields a single
    # SPECIAL_REQUEST scenario.  The decision_type hint forces
    # DEFER on every emitted case.
    assert len(result) >= 1
    assert all(c.decision_type is DecisionType.DEFER for c in result)


def test_classify_all_general_fallback_uses_scenario_hint(
    classifier: DecisionClassifier,
    hints_enabled: None,
) -> None:
    # Message that nothing in the keyword chain detects → empty
    # scenario set → fallback to single classify() call → hint
    # rescues the case from Scenario.GENERAL.
    flags = BusinessFlags(scenario_hint="amenity_exception")
    result = classifier.classify_all(
        business_flags=flags, message_text="ahoj",
    )
    assert len(result) == 1
    assert result[0].scenario is Scenario.AMENITY_EXCEPTION
