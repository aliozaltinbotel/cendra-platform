"""Response language must follow the CURRENT guest turn, not history.

The agent reply language is driven by ``response_language`` emitted by
:class:`BusinessFlagClassifier`.  The classifier prompt embeds the last
few history messages under ``CONTEXT``; when that history was Turkish
but the current ``MESSAGE`` is English, the LLM used to copy the
history language and return ``"tr"`` — so an English question received a
Turkish answer (observed on Sandbox UI).

The fix scopes the language instruction to the ``MESSAGE`` (current
turn) and tells the model to ignore the ``CONTEXT`` language.  These
tests pin that the instruction reaches the LLM and that the current
message is the one placed under ``MESSAGE``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from brain_engine.reasoning.business_classifier import (
    _CLASSIFICATION_PROMPT,
    BusinessFlagClassifier,
)

_ACOMPLETION = "brain_engine.reasoning.business_classifier.litellm.acompletion"

_TURKISH_HISTORY = [
    {"role": "user", "content": "WiFi şifresi nedir?"},
    {"role": "assistant", "content": "WiFi şifresi 'maladhin_horba'."},
]


def _llm_response(response_language: str = "en") -> SimpleNamespace:
    """A minimal, parseable classifier JSON payload."""
    payload = json.dumps(
        {
            "flags": {},
            "response_language": response_language,
            "confidence": 0.9,
            "sentiment_score": 3,
            "urgency": "normal",
            "detected_issues": [],
            "suggested_category": "",
            "suggested_subcategory": "",
            "scenario_hint": "",
            "decision_type_hint": "",
        },
    )
    message = SimpleNamespace(content=payload)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _user_prompt(acompletion: AsyncMock) -> str:
    """Return the user-role prompt content the classifier sent."""
    messages = acompletion.await_args.kwargs["messages"]
    return next(m["content"] for m in messages if m["role"] == "user")


def test_prompt_scopes_language_to_current_message_not_context() -> None:
    # The instruction the LLM reads must anchor language to MESSAGE and
    # explicitly disregard the CONTEXT language.
    assert "response_language" in _CLASSIFICATION_PROMPT
    lowered = _CLASSIFICATION_PROMPT.lower()
    assert "language of the message" in lowered
    assert "ignore the language of the context" in lowered


async def test_english_message_over_turkish_history_sends_scoped_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acompletion = AsyncMock(return_value=_llm_response("en"))
    monkeypatch.setattr(_ACOMPLETION, acompletion)

    classifier = BusinessFlagClassifier()
    result = await classifier.classify(
        message="what is the wifi password",
        conversation_history=_TURKISH_HISTORY,
    )

    prompt = _user_prompt(acompletion)
    # Current turn sits under MESSAGE; the Turkish history under CONTEXT.
    assert "MESSAGE: what is the wifi password" in prompt
    assert "WiFi şifresi nedir?" in prompt  # history present, as CONTEXT
    # The scoping instruction travels with the prompt.
    assert "language of the MESSAGE" in prompt
    assert "IGNORE the language of the CONTEXT" in prompt
    # Sanity: the clamped result honours the mocked decision.
    assert result.response_language == "en"
