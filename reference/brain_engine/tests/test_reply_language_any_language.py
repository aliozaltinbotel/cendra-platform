"""Brain Engine must reply in the guest's own language — ANY language.

Previously the reply language was clamped to a hardcoded ``{"tr", "en"}``
whitelist (``business_classifier._SUPPORTED_RESPONSE_LANGUAGES``) and the
system-prompt directive only fired for non-English, so a German / Russian
/ French guest could never get a reply in their language.  These tests
pin the new behaviour:

* :func:`_normalize_language` sanitises the classifier code by FORMAT
  only (any two-letter code), never against a fixed language set.
* The classifier propagates any detected language (e.g. ``"de"``)
  instead of collapsing it to ``"en"``.
* :func:`_reply_language_instruction` mirrors the guest's language by
  default and honours an explicit customer override.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from brain_engine.conversation.service import _reply_language_instruction
from brain_engine.reasoning.business_classifier import (
    BusinessFlagClassifier,
    _normalize_language,
)

_ACOMPLETION = "brain_engine.reasoning.business_classifier.litellm.acompletion"


# -- _normalize_language: format-only, no whitelist ---------------------


@pytest.mark.parametrize("code", ["de", "ru", "fr", "es", "it", "ar", "ja", "zh"])
def test_normalize_accepts_any_two_letter_code(code: str) -> None:
    """Any ISO 639-1 style code survives — no fixed language set."""
    assert _normalize_language(code) == code


def test_normalize_lowercases_and_trims() -> None:
    assert _normalize_language("  DE ") == "de"


def test_normalize_takes_leading_two_of_a_locale_tag() -> None:
    """A ``fr-FR`` locale tag reduces to its language subtag."""
    assert _normalize_language("fr-FR") == "fr"


@pytest.mark.parametrize("raw", ["", "  ", None, "1", "!", "x"])
def test_normalize_falls_back_to_en_on_empty_or_malformed(raw: object) -> None:
    """Empty / malformed output is the ONLY path to the ``en`` default."""
    assert _normalize_language(raw) == "en"


# -- classifier propagates any language ---------------------------------


def _llm_response(response_language: str) -> SimpleNamespace:
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


@pytest.mark.parametrize("lang", ["de", "ru", "fr"])
async def test_classifier_propagates_non_tr_en_language(
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
) -> None:
    """A German / Russian / French detection is no longer collapsed to
    ``en`` — proving the ``{tr, en}`` clamp is gone."""
    acompletion = AsyncMock(return_value=_llm_response(lang))
    monkeypatch.setattr(_ACOMPLETION, acompletion)

    classifier = BusinessFlagClassifier()
    result = await classifier.classify(message="Wie ist das WLAN-Passwort?")

    assert result.response_language == lang


# -- _reply_language_instruction: mirror by default, override wins ------


def test_instruction_mirrors_guest_when_no_override() -> None:
    """Empty customer setting → mirror the guest's own language."""
    instruction = _reply_language_instruction("")
    assert "same language as the" in instruction
    assert "whatever language it is" in instruction


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_instruction_blank_setting_is_mirror(blank: object) -> None:
    """Blank / whitespace / ``None`` all mean auto-mirror."""
    assert "same language as the" in _reply_language_instruction(blank)  # type: ignore[arg-type]


def test_instruction_explicit_override_wins() -> None:
    """A pinned customer language forces that language verbatim."""
    instruction = _reply_language_instruction("tr")
    assert "Respond in tr." in instruction
    assert "same language as the" not in instruction
