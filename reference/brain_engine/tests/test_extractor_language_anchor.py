# ruff: noqa: RUF001
# RUF001 (ambiguous unicode) suppressed file-wide — the fixtures use
# the literal Turkish letters the live LLM and detector see.
"""Tests for the deterministic conversation-language anchor.

Tester 2026-06-10: an English guest thread produced a Turkish
``pm_question`` in PM Chat because the extractor LLM *inferred* the
escalation language from a Turkish property context.  The fix
detects the conversation language offline (lingua) from the AI's
own reply — already rendered in the guest's language — and pins the
ISO 639-1 code into the extractor prompt so the LLM cannot drift.

These tests pin the mechanism, not LLM compliance: the detector
resolves the right code and the code reaches the prompt verbatim.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from brain_engine.conversation import missing_info_extractor as mie
from brain_engine.conversation.missing_info_extractor import (
    MissingInfoRequest,
    _detect_conversation_language,
    extract_missing_information,
)
from brain_engine.patterns.language_detector import (
    LanguageDetectorService,
    get_shared_language_detector,
)

# ── deterministic detection ──────────────────────────────────────


def test_detects_english_thread() -> None:
    """An English AI reply resolves to ``en``."""
    text = "I'll follow up with our team and update you as soon as I can."
    assert _detect_conversation_language(text) == "en"


def test_detects_turkish_thread() -> None:
    """A Turkish AI reply resolves to ``tr``."""
    text = "Ekibimizle kontrol edip en kısa sürede size geri döneceğim."
    assert _detect_conversation_language(text) == "tr"


def test_detects_french_thread() -> None:
    """A French AI reply resolves to ``fr`` — any language, not just tr/en."""
    text = (
        "Je vais vérifier avec notre équipe et je reviendrai vers vous "
        "dès que possible avec plus d'informations."
    )
    assert _detect_conversation_language(text) == "fr"


def test_empty_text_falls_back_to_default() -> None:
    """Empty input degrades to the detector default (``en``)."""
    assert _detect_conversation_language("") == "en"


# ── shared singleton ─────────────────────────────────────────────


def test_shared_detector_is_a_singleton() -> None:
    """One process-wide instance so the ~50 MB tables load once."""
    first = get_shared_language_detector()
    second = get_shared_language_detector()
    assert first is second
    assert isinstance(first, LanguageDetectorService)


# ── the ISO code reaches the prompt ──────────────────────────────


def _fake_llm_response(payload: dict[str, str]) -> SimpleNamespace:
    """Build a litellm-shaped response wrapping ``payload`` as JSON."""
    message = SimpleNamespace(content=json.dumps(payload))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.mark.asyncio
async def test_english_thread_pins_en_into_prompt() -> None:
    """The detected ISO code is injected verbatim into the user
    prompt so the LLM writes pm_question in that language."""
    captured: dict[str, object] = {}

    async def _capture(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured["messages"] = kwargs["messages"]
        return _fake_llm_response(
            {
                "missing_information": "- early check-in availability",
                "answered_questions": "",
                "intervention_reason": "early check-in",
                "pm_question": (
                    "The guest is asking whether early check-in is "
                    "possible, but I don't have this information."
                ),
            }
        )

    request = MissingInfoRequest(
        ai_message="I'll check on early check-in and get back to you.",
        messages=[{"role": "user", "content": "Can I check in early at noon?"}],
    )
    with patch.object(mie.litellm, "acompletion", new=AsyncMock(side_effect=_capture)):
        result = await extract_missing_information(request)

    user_prompt = captured["messages"][1]["content"]
    assert "Conversation language (ISO 639-1): en" in user_prompt
    assert result.pm_question.startswith("The guest is asking whether")


@pytest.mark.asyncio
async def test_turkish_thread_pins_tr_into_prompt() -> None:
    """A Turkish AI reply pins ``tr`` — the anchor follows the
    conversation language, not a fixed default."""
    captured: dict[str, object] = {}

    async def _capture(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured["messages"] = kwargs["messages"]
        return _fake_llm_response(
            {
                "missing_information": "- erken giriş",
                "answered_questions": "",
                "intervention_reason": "erken giriş",
                "pm_question": (
                    "Misafir erken giriş mümkün mü diye soruyor ancak "
                    "bu bilgi bende yok."
                ),
            }
        )

    request = MissingInfoRequest(
        ai_message="Kontrol edip en kısa sürede size geri döneceğim.",
        messages=[{"role": "user", "content": "Erken giriş yapabilir miyim?"}],
    )
    with patch.object(mie.litellm, "acompletion", new=AsyncMock(side_effect=_capture)):
        await extract_missing_information(request)

    user_prompt = captured["messages"][1]["content"]
    assert "Conversation language (ISO 639-1): tr" in user_prompt
