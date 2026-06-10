"""Tests for the Phase-5 IntelligentClassifier wiring on BFC.

Pins the contract:

* When ``intelligent_classifier`` is ``None`` (the default), BFC
  behaviour is bit-for-bit identical to the pre-Phase-5 path —
  the result the LLM produces is returned unchanged.
* When wired, IC runs after the primary LLM call and *only fills
  blanks*: a ``decision_type_hint`` the upstream LLM already
  committed to is never overwritten.
* IC failures collapse to a no-op — the BFC result is returned
  unchanged.
* Empty / whitespace messages skip the IC call entirely.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import pytest

from brain_engine.patterns.intelligent_classifier import (
    IntelligentClassification,
    LLMClassificationResult,
)
from brain_engine.patterns.language_detector import DetectionResult
from brain_engine.patterns.scenario_matcher import ScenarioCandidate
from brain_engine.reasoning.business_classifier import (
    BusinessFlagClassifier,
    ClassificationResult,
)


class _StubIntelligentClassifier:
    """Returns a configured :class:`IntelligentClassification`."""

    def __init__(
        self,
        *,
        decision_type: str = "deny",
        raises: bool = False,
        calls_record: list[str] | None = None,
    ) -> None:
        self.decision_type = decision_type
        self.raises = raises
        self.calls = calls_record if calls_record is not None else []

    async def classify(
        self, message: str,
    ) -> IntelligentClassification:
        self.calls.append(message)
        if self.raises:
            raise RuntimeError("simulated IC failure")
        candidates: tuple[ScenarioCandidate, ...] = (
            ScenarioCandidate(
                scenario_id="access_code_release",
                similarity=0.9,
                text="door code request",
            ),
        )
        return IntelligentClassification(
            message=message,
            language=DetectionResult(
                language="en",
                confidence=0.95,
                is_fallback=False,
            ),
            candidates=candidates,
            llm=LLMClassificationResult(
                scenario_id="access_code_release",
                decision_type=self.decision_type,
                confidence=0.85,
                rationale="stub",
            ),
            scenario_id="access_code_release",
            decision_type=self.decision_type,
        )


async def _stub_classify_via_llm(
    self: BusinessFlagClassifier,
    message: str,
    context: str,
) -> ClassificationResult:
    """Return a deterministic BFC result without hitting the LLM."""
    return ClassificationResult(
        flags={},
        confidence=0.9,
        scenario_hint="",
        decision_type_hint="",
    )


@pytest.mark.asyncio
async def test_no_intelligent_classifier_keeps_legacy_behaviour() -> None:
    """Default constructor → IC absent → BFC result unchanged."""
    bfc = BusinessFlagClassifier()
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _stub_classify_via_llm,
    ):
        result = await bfc.classify("Can I get the door code?")
    assert result.decision_type_hint == ""


@pytest.mark.asyncio
async def test_wired_ic_fills_empty_decision_type_hint() -> None:
    """IC's decision_type lands on an otherwise empty hint."""
    record: list[str] = []
    ic = _StubIntelligentClassifier(
        decision_type="deny", calls_record=record,
    )
    bfc = BusinessFlagClassifier(intelligent_classifier=ic)
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _stub_classify_via_llm,
    ):
        result = await bfc.classify(
            "Hello, we need the access code please.",
        )
    assert result.decision_type_hint == "deny"
    assert record == [
        "Hello, we need the access code please.",
    ]


@pytest.mark.asyncio
async def test_wired_ic_does_not_overwrite_existing_hint() -> None:
    """A hint already set by the primary LLM is preserved."""

    async def _bfc_with_existing_hint(
        self: BusinessFlagClassifier,
        message: str,
        context: str,
    ) -> ClassificationResult:
        return ClassificationResult(
            flags={},
            confidence=0.9,
            scenario_hint="",
            decision_type_hint="approve",
        )

    ic = _StubIntelligentClassifier(decision_type="deny")
    bfc = BusinessFlagClassifier(intelligent_classifier=ic)
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _bfc_with_existing_hint,
    ):
        result = await bfc.classify("hi")
    assert result.decision_type_hint == "approve"


@pytest.mark.asyncio
async def test_ic_failure_collapses_to_noop() -> None:
    """IC raising never propagates — BFC result is returned as-is."""
    ic = _StubIntelligentClassifier(raises=True)
    bfc = BusinessFlagClassifier(intelligent_classifier=ic)
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _stub_classify_via_llm,
    ):
        result = await bfc.classify("the door code please")
    assert result.decision_type_hint == ""


@pytest.mark.asyncio
async def test_empty_message_skips_ic_call() -> None:
    """Empty / whitespace input never invokes the IC."""
    record: list[str] = []
    ic = _StubIntelligentClassifier(calls_record=record)
    bfc = BusinessFlagClassifier(intelligent_classifier=ic)
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _stub_classify_via_llm,
    ):
        await bfc.classify("")
        await bfc.classify("   \n  ")
    assert record == []


@pytest.mark.asyncio
async def test_ic_empty_decision_type_does_not_pollute_hint() -> None:
    """When IC returns an empty decision_type, the hint stays empty."""
    ic = _StubIntelligentClassifier(decision_type="")
    bfc = BusinessFlagClassifier(intelligent_classifier=ic)
    with patch.object(
        BusinessFlagClassifier,
        "_classify_via_llm",
        _stub_classify_via_llm,
    ):
        result = await bfc.classify("a request")
    assert result.decision_type_hint == ""


_ = Any, Sequence  # keep type-checker imports alive
