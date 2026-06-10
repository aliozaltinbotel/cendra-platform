"""Tests for :class:`IntelligentClassifier` — Layer 3 composition.

Pins the contract:

* End-to-end run: language detection → retrieval → LLM pick.
* Empty / whitespace input short-circuits with an empty result.
* LLM stub receives the correct language + candidate set.
* When the LLM returns an id outside the candidate set, the
  fallback to top-similarity kicks in.
* When the LLM returns a candidate id, it wins.
* Audit fields (language, candidates, llm) are preserved on the
  result object for downstream consumers.
* Constructor rejects invalid ``top_k``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from brain_engine.patterns.intelligent_classifier import (
    IntelligentClassification,
    IntelligentClassifier,
    LLMClassificationResult,
)
from brain_engine.patterns.language_detector import (
    LanguageDetectorService,
)
from brain_engine.patterns.scenario_matcher import (
    ScenarioCandidate,
    ScenarioMatcher,
    examples_from_mapping,
)

_REGISTRY: dict[str, str] = {
    "access_code_release": (
        "Guest asks for the door code or access credentials "
        "before arriving at the property."
    ),
    "early_checkin": (
        "Guest requests to arrive earlier than the official "
        "check-in time."
    ),
    "late_checkout": (
        "Guest requests to leave the property later than the "
        "official check-out time."
    ),
    "cancellation_request": (
        "Guest wants to cancel the reservation or asks for "
        "a refund."
    ),
    "noise_complaint": (
        "Guest complains about loud neighbours, construction, "
        "or noise at night."
    ),
}


class _RecordingLLM:
    """Stub that records inputs and returns a configured reply."""

    def __init__(
        self,
        reply: LLMClassificationResult,
    ) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def classify(
        self,
        *,
        message: str,
        language: str,
        candidates: Sequence[ScenarioCandidate],
    ) -> LLMClassificationResult:
        self.calls.append(
            (
                message,
                language,
                tuple(c.scenario_id for c in candidates),
            ),
        )
        return self.reply


@pytest.fixture(scope="module")
def matcher() -> ScenarioMatcher:
    return ScenarioMatcher(examples_from_mapping(_REGISTRY))


@pytest.fixture(scope="module")
def detector() -> LanguageDetectorService:
    return LanguageDetectorService()


def _classifier(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
    llm: _RecordingLLM,
    *,
    top_k: int = 5,
) -> IntelligentClassifier:
    return IntelligentClassifier(
        detector=detector,
        matcher=matcher,
        llm_client=llm,
        top_k=top_k,
    )


# ── core behaviour ─────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_end_to_end_returns_llm_choice(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Pipeline runs all three layers and surfaces the LLM pick."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="access_code_release",
            decision_type="inform",
            confidence=0.9,
            rationale="guest asked for the code",
        ),
    )
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify(
        "Hello, could you please send me the door access code "
        "for our apartment? We will arrive tomorrow afternoon.",
    )
    assert result.scenario_id == "access_code_release"
    assert result.decision_type == "inform"
    assert result.language.language == "en"
    assert len(result.candidates) > 0
    # LLM received the language + candidate ids.
    assert len(llm.calls) == 1
    _, lang, ids = llm.calls[0]
    assert lang == "en"
    assert "access_code_release" in ids


@pytest.mark.asyncio
async def test_empty_message_short_circuits(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Empty input yields an empty result without invoking the LLM."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="x",
            decision_type="inform",
            confidence=1.0,
            rationale="should not be called",
        ),
    )
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify("")
    assert result.scenario_id == ""
    assert result.decision_type == ""
    assert result.candidates == ()
    assert llm.calls == []


@pytest.mark.asyncio
async def test_whitespace_only_short_circuits(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Whitespace-only input behaves identically to empty."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="x",
            decision_type="inform",
            confidence=1.0,
            rationale="",
        ),
    )
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify("   \n  ")
    assert result.scenario_id == ""
    assert llm.calls == []


@pytest.mark.asyncio
async def test_llm_returns_unknown_scenario_falls_back_to_top(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Unknown LLM id collapses to the highest-similarity candidate."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="not_a_real_scenario",
            decision_type="inform",
            confidence=0.6,
            rationale="hallucinated id",
        ),
    )
    classifier = _classifier(detector, matcher, llm, top_k=3)
    result = await classifier.classify(
        "Can I have the entry code please?",
    )
    # Top candidate is access_code_release for this query
    assert result.scenario_id == "access_code_release"
    # decision_type still propagates from the LLM
    assert result.decision_type == "inform"


@pytest.mark.asyncio
async def test_llm_returns_empty_scenario_falls_back_to_top(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Empty LLM scenario_id falls back to top candidate."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="",
            decision_type="defer",
            confidence=0.2,
            rationale="not sure",
        ),
    )
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify("door code please")
    assert result.scenario_id == "access_code_release"


@pytest.mark.asyncio
async def test_audit_fields_preserved(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """The result keeps every intermediate signal."""
    reply = LLMClassificationResult(
        scenario_id="access_code_release",
        decision_type="inform",
        confidence=0.85,
        rationale="strong match",
    )
    llm = _RecordingLLM(reply=reply)
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify("the door code please")
    assert result.language.language == "en"
    assert result.candidates  # non-empty
    assert result.llm is reply
    assert result.top_candidate is not None
    assert (
        result.top_candidate.scenario_id == "access_code_release"
    )


@pytest.mark.asyncio
async def test_multilingual_query_threads_language_to_llm(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """Turkish query → language='tr' reaches the LLM stub."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="access_code_release",
            decision_type="inform",
            confidence=0.8,
            rationale="tr door-code request",
        ),
    )
    classifier = _classifier(detector, matcher, llm, top_k=3)
    await classifier.classify(
        "Şifre alabilir miyim lütfen?",
    )
    assert llm.calls
    _, lang, _ = llm.calls[0]
    assert lang == "tr"


# ── validation ─────────────────────────────────────────────── #


def test_constructor_rejects_non_positive_top_k(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """``top_k`` must be positive."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="x",
            decision_type="inform",
            confidence=1.0,
            rationale="",
        ),
    )
    with pytest.raises(ValueError, match="top_k"):
        IntelligentClassifier(
            detector=detector,
            matcher=matcher,
            llm_client=llm,
            top_k=0,
        )


def test_llm_result_rejects_out_of_range_confidence() -> None:
    """``LLMClassificationResult.confidence`` must be in ``[0, 1]``."""
    with pytest.raises(ValueError, match="confidence"):
        LLMClassificationResult(
            scenario_id="x",
            decision_type="inform",
            confidence=1.5,
            rationale="",
        )


def test_classification_requires_detection_result(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """``language`` field must be a :class:`DetectionResult`."""
    llm_reply = LLMClassificationResult(
        scenario_id="x",
        decision_type="inform",
        confidence=1.0,
        rationale="",
    )
    with pytest.raises(TypeError, match="DetectionResult"):
        IntelligentClassification(
            message="hi",
            language="en",  # type: ignore[arg-type]
            candidates=(),
            llm=llm_reply,
        )


@pytest.mark.asyncio
async def test_top_candidate_property_returns_first(
    detector: LanguageDetectorService,
    matcher: ScenarioMatcher,
) -> None:
    """``top_candidate`` returns the first ranked entry."""
    llm = _RecordingLLM(
        reply=LLMClassificationResult(
            scenario_id="access_code_release",
            decision_type="inform",
            confidence=0.8,
            rationale="",
        ),
    )
    classifier = _classifier(detector, matcher, llm)
    result = await classifier.classify(
        "What is my access code?",
    )
    assert result.top_candidate is not None
    assert (
        result.top_candidate == result.candidates[0]
    )


_ = replace
