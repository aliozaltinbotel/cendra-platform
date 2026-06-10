"""Tests for the missing-info topic override (Aybüke 2026-05-18 fix).

Before this fix, ``_maybe_emit_missing_info`` relied on the
``extract_missing_information`` LLM to pick the ``<topic>`` in
the ``intervention_reason`` template
(``"Guest needs <topic> which is not in the knowledge base"``).
The LLM was free-form and hallucinated topics — Aybüke reported
the extractor picking ``"pricing"`` on an early-checkin
conversation.

The fix (Variant B) uses the FL-16 foundation match's dominant
catalog title as the authoritative topic when available, with
the LLM-extracted ``intervention_reason`` as a fallback only
when the orchestrator is unwired or Q5-A cleared the dominant
entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from brain_engine.conversation.missing_info_extractor import (
    MissingInfoResponse,
)
from brain_engine.conversation.service import (
    _MISSING_INFO_DEDUP,
    _maybe_emit_missing_info,
    _topic_from_foundation_analysis,
)
from brain_engine.streaming import current_emitter as _current_emitter
from brain_engine.streaming.event_types import EventType

# ── _topic_from_foundation_analysis ───────────────────────── #


@dataclass(slots=True)
class _FakeCatalogEntry:
    title: str = ""


@dataclass(slots=True)
class _FakeMatch:
    dominant_catalog_entry: Any | None = None


@dataclass(slots=True)
class _FakeAnalysis:
    foundation_match: Any = field(default_factory=_FakeMatch)


def test_topic_from_foundation_analysis_returns_title() -> None:
    """Catalog entry title is returned verbatim."""
    entry = _FakeCatalogEntry(
        title="Guest asks if early check-in is possible",
    )
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    assert (
        _topic_from_foundation_analysis(analysis)
        == "Guest asks if early check-in is possible"
    )


def test_topic_from_foundation_analysis_none_inputs_return_empty() -> None:
    """Any missing layer in the chain collapses to ``""``."""
    assert _topic_from_foundation_analysis(None) == ""
    assert (
        _topic_from_foundation_analysis(
            _FakeAnalysis(foundation_match=None),
        )
        == ""
    )
    assert (
        _topic_from_foundation_analysis(
            _FakeAnalysis(
                foundation_match=_FakeMatch(dominant_catalog_entry=None),
            ),
        )
        == ""
    )


def test_topic_from_foundation_analysis_empty_title_returns_empty() -> None:
    """Blank title ⇒ ``""`` so the caller falls back to LLM."""
    entry = _FakeCatalogEntry(title="")
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    assert _topic_from_foundation_analysis(analysis) == ""


# ── _maybe_emit_missing_info topic override ──────────────── #


@dataclass(slots=True)
class _CapturedEvent:
    event_type: Any
    payload: dict[str, Any]


class _FakeEmitter:
    def __init__(self) -> None:
        self.events: list[_CapturedEvent] = []

    def emit(self, event_type: Any, payload: dict[str, Any]) -> None:
        self.events.append(_CapturedEvent(event_type, payload))


@dataclass(slots=True)
class _FakeConversation:
    conversation_id: str = ""
    messages: list[Any] = field(default_factory=list)


@pytest.fixture
def captured_emitter() -> _FakeEmitter:
    emitter = _FakeEmitter()
    token = _current_emitter.set_current_emitter(emitter)
    yield emitter
    _current_emitter.reset_current_emitter(token)


@pytest.fixture(autouse=True)
def _reset_dedup_ledger() -> None:
    _MISSING_INFO_DEDUP.clear()
    yield
    _MISSING_INFO_DEDUP.clear()


@pytest.fixture
def _hallucinating_extractor() -> Any:
    """Patch the extractor to mimic Aybüke's bug — LLM says "pricing"."""
    fake_result = MissingInfoResponse(
        missing_information="- pricing details for early arrival",
        answered_questions="",
        intervention_reason=(
            "Guest needs pricing information which is not in the "
            "knowledge base"
        ),
    )
    with patch(
        "brain_engine.conversation.service.extract_missing_information",
        new=AsyncMock(return_value=fake_result),
    ) as patched:
        yield patched


@pytest.mark.asyncio
async def test_catalog_title_overrides_llm_topic(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """Foundation title wins — Aybüke's exact bug fixed."""
    entry = _FakeCatalogEntry(
        title="Guest asks if early check-in is possible",
    )
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    await _maybe_emit_missing_info(
        ai_message="I'll check and get back to you",
        conversation=_FakeConversation(conversation_id="conv-aybuke"),
        foundation_analysis=analysis,
    )
    assert len(captured_emitter.events) == 1
    evt = captured_emitter.events[0]
    assert evt.event_type == EventType.MISSING_INFO_DETECTED
    # Override took effect — no "pricing" anywhere in the question.
    assert "pricing" not in evt.payload["question"].lower()
    assert (
        "Guest asks if early check-in is possible" in evt.payload["question"]
    )
    assert evt.payload["source_field"] == "foundation_dominant_topic"


@pytest.mark.asyncio
async def test_no_foundation_analysis_falls_back_to_llm_topic(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """Pre-fix callers (no kwarg) ⇒ legacy LLM-derived reason wins."""
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=_FakeConversation(conversation_id="conv-no-fa"),
    )
    assert len(captured_emitter.events) == 1
    evt = captured_emitter.events[0]
    # PR #326 sanitises the legacy LLM template down to its bare
    # topic; A1.a (round-2) updates the upstream prompt to stop
    # emitting the boilerplate in the first place.  Both ship the
    # bare-topic contract to PM Chat.
    assert evt.payload["question"] == "pricing information"
    assert evt.payload["source_field"] == "extract_missing_information"


@pytest.mark.asyncio
async def test_unwired_orchestrator_falls_back_to_llm_topic(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """foundation_analysis=None ⇒ same as the missing-kwarg path."""
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=_FakeConversation(conversation_id="conv-none"),
        foundation_analysis=None,
    )
    assert len(captured_emitter.events) == 1
    assert (
        captured_emitter.events[0].payload["source_field"]
        == "extract_missing_information"
    )


@pytest.mark.asyncio
async def test_q5a_cleared_dominant_falls_back_to_llm(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """Q5-A cleared dominant_catalog_entry ⇒ no catalog topic, LLM fallback."""
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=None),
    )
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=_FakeConversation(conversation_id="conv-q5a"),
        foundation_analysis=analysis,
    )
    assert len(captured_emitter.events) == 1
    assert (
        captured_emitter.events[0].payload["source_field"]
        == "extract_missing_information"
    )


@pytest.mark.asyncio
async def test_empty_title_falls_back_to_llm(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """Blank catalog title ⇒ LLM fallback (defense-in-depth)."""
    entry = _FakeCatalogEntry(title="")
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=_FakeConversation(conversation_id="conv-blank"),
        foundation_analysis=analysis,
    )
    assert len(captured_emitter.events) == 1
    assert (
        captured_emitter.events[0].payload["source_field"]
        == "extract_missing_information"
    )


@pytest.mark.asyncio
async def test_dedup_uses_overridden_topic(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """Same catalog topic in two calls ⇒ second one deduped."""
    entry = _FakeCatalogEntry(title="Guest asks if early check-in is possible")
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    conv = _FakeConversation(conversation_id="conv-dedup")
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=conv,
        foundation_analysis=analysis,
    )
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=conv,
        foundation_analysis=analysis,
    )
    assert len(captured_emitter.events) == 1


# ── pm_question: full sentence reaches PM Chat ───────────────── #


@pytest.fixture
def _full_sentence_extractor() -> Any:
    """Patch the extractor to return a full PM-facing sentence.

    Mirrors the live A1.b contract: ``intervention_reason`` is the
    bare topic (dedup / source key) while ``pm_question`` carries the
    complete guest-language sentence PM Chat surfaces.
    """
    fake_result = MissingInfoResponse(
        missing_information="- early check-in availability",
        answered_questions="",
        intervention_reason="early check-in",
        pm_question=(
            "The guest is asking whether an early check-in at noon is "
            "possible, but I don't have this information. Could you let "
            "me know how I should respond?"
        ),
    )
    with patch(
        "brain_engine.conversation.service.extract_missing_information",
        new=AsyncMock(return_value=fake_result),
    ) as patched:
        yield patched


@pytest.mark.asyncio
async def test_pm_question_full_sentence_is_surfaced_to_pm(
    captured_emitter: _FakeEmitter,
    _full_sentence_extractor: Any,
) -> None:
    """The displayed ``question`` is the full sentence, not the bare
    topic — the exact tester 2026-06-10 complaint."""
    await _maybe_emit_missing_info(
        ai_message="I'll check and get back to you",
        conversation=_FakeConversation(conversation_id="conv-pmq"),
    )
    assert len(captured_emitter.events) == 1
    payload = captured_emitter.events[0].payload
    question = payload["question"]
    # Full sentence, not the two-word topic.
    assert question.startswith("The guest is asking whether an early")
    assert question != "early check-in"
    assert len(question.split()) > 5
    # The bare topic is preserved on the machine field.
    assert payload["missing_information"] == "- early check-in availability"
    assert payload["source_field"] == "extract_missing_information"


@pytest.mark.asyncio
async def test_pm_question_surfaced_on_catalog_path(
    captured_emitter: _FakeEmitter,
    _full_sentence_extractor: Any,
) -> None:
    """Even when the foundation catalog supplies the topic, the full
    LLM sentence is what PM Chat reads — and ``source_field`` still
    marks the catalog override."""
    entry = _FakeCatalogEntry(title="early check-in")
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    await _maybe_emit_missing_info(
        ai_message="I'll check and get back to you",
        conversation=_FakeConversation(conversation_id="conv-pmq-cat"),
        foundation_analysis=analysis,
    )
    assert len(captured_emitter.events) == 1
    payload = captured_emitter.events[0].payload
    assert payload["question"].startswith("The guest is asking whether")
    assert payload["source_field"] == "foundation_dominant_topic"


@pytest.mark.asyncio
async def test_pm_question_dedup_still_keyed_on_topic(
    captured_emitter: _FakeEmitter,
    _full_sentence_extractor: Any,
) -> None:
    """Two identical turns ⇒ one emit — dedup is unaffected by the
    new sentence field (fingerprint still keyed on the bare topic)."""
    conv = _FakeConversation(conversation_id="conv-pmq-dedup")
    await _maybe_emit_missing_info(ai_message="I'll check", conversation=conv)
    await _maybe_emit_missing_info(ai_message="I'll check", conversation=conv)
    assert len(captured_emitter.events) == 1


@pytest.mark.asyncio
async def test_missing_pm_question_falls_back_to_bare_topic(
    captured_emitter: _FakeEmitter,
    _hallucinating_extractor: Any,
) -> None:
    """When the LLM omits ``pm_question`` the displayed question
    degrades gracefully to the sanitised bare topic (no empty flag)."""
    await _maybe_emit_missing_info(
        ai_message="I'll check",
        conversation=_FakeConversation(conversation_id="conv-pmq-fallback"),
    )
    assert len(captured_emitter.events) == 1
    assert captured_emitter.events[0].payload["question"] == "pricing information"


@pytest.mark.asyncio
async def test_no_missing_information_skips_emit(
    captured_emitter: _FakeEmitter,
) -> None:
    """Extractor returns empty ⇒ no emit regardless of foundation match."""
    fake_answered = MissingInfoResponse(
        missing_information="",
        answered_questions="- yes early check-in possible",
        intervention_reason="",
    )
    entry = _FakeCatalogEntry(title="Guest asks if early check-in is possible")
    analysis = _FakeAnalysis(
        foundation_match=_FakeMatch(dominant_catalog_entry=entry),
    )
    with patch(
        "brain_engine.conversation.service.extract_missing_information",
        new=AsyncMock(return_value=fake_answered),
    ):
        await _maybe_emit_missing_info(
            ai_message="Yes you can",
            conversation=_FakeConversation(conversation_id="conv-answered"),
            foundation_analysis=analysis,
        )
    assert captured_emitter.events == []
