"""Tests for :class:`LiteLLMClassifierClient` — production LLM client.

Pins the contract:

* Empty candidates ⇒ zero-confidence result, no LLM call.
* Valid JSON response parses into the typed result.
* Markdown-fenced JSON parses (LLMs love to add ```` ```json ````).
* Invalid JSON ⇒ zero-confidence rationale.
* Out-of-range / non-numeric ``confidence`` clamped to ``[0, 1]``.
* Invalid ``decision_type`` rejected, blanks the field.
* Transport error ⇒ zero-confidence rationale, no raise.
* Constructor rejects invalid model / temperature / max_tokens.

These tests do NOT hit a live LLM — ``litellm.acompletion`` is
monkey-patched per test.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from brain_engine.patterns.intelligent_classifier import (
    LLMClassificationResult,
)
from brain_engine.patterns.litellm_classifier_client import (
    LiteLLMClassifierClient,
)
from brain_engine.patterns.scenario_matcher import ScenarioCandidate


def _candidate(scenario_id: str) -> ScenarioCandidate:
    return ScenarioCandidate(
        scenario_id=scenario_id,
        similarity=0.8,
        text=f"trigger for {scenario_id}",
    )


def _fake_response(text: str) -> Any:
    """Build a litellm-compatible response shape."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
            ),
        ],
    )


# ── empty / validation ────────────────────────────────────── #


@pytest.mark.asyncio
async def test_empty_candidates_returns_zero_confidence() -> None:
    """No candidates ⇒ no LLM call, zero confidence."""
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(),
    )
    assert result.scenario_id == ""
    assert result.confidence == 0.0


def test_constructor_rejects_empty_model() -> None:
    with pytest.raises(ValueError, match="model"):
        LiteLLMClassifierClient(model="")


def test_constructor_rejects_negative_temperature() -> None:
    with pytest.raises(ValueError, match="temperature"):
        LiteLLMClassifierClient(temperature=-0.1)


def test_constructor_rejects_excessive_temperature() -> None:
    with pytest.raises(ValueError, match="temperature"):
        LiteLLMClassifierClient(temperature=2.5)


def test_constructor_rejects_zero_max_tokens() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        LiteLLMClassifierClient(max_tokens=0)


# ── happy-path parsing ────────────────────────────────────── #


@pytest.mark.asyncio
async def test_valid_json_parses_to_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean JSON response maps to the typed result."""
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_response(
            '{"scenario_id":"access_code_release",'
            '"decision_type":"inform",'
            '"confidence":0.87,'
            '"rationale":"guest asked for the code"}',
        )

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="Can I get the door code?",
        language="en",
        candidates=(_candidate("access_code_release"),),
    )
    assert isinstance(result, LLMClassificationResult)
    assert result.scenario_id == "access_code_release"
    assert result.decision_type == "inform"
    assert 0.86 < result.confidence < 0.88
    assert result.rationale.startswith("guest asked")
    # Prompt + language threaded through.
    assert "en" in captured["messages"][1]["content"]
    assert (
        "access_code_release"
        in captured["messages"][1]["content"]
    )


@pytest.mark.asyncio
async def test_markdown_fenced_json_still_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMs sometimes wrap JSON in ``` fences — the parser tolerates it."""
    fenced = (
        "Sure, here's the result:\n"
        "```json\n"
        '{"scenario_id":"early_checkin",'
        '"decision_type":"approve",'
        '"confidence":0.7,'
        '"rationale":"early checkin request"}\n'
        "```"
    )

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response(fenced)

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="early please",
        language="en",
        candidates=(_candidate("early_checkin"),),
    )
    assert result.scenario_id == "early_checkin"
    assert result.decision_type == "approve"


# ── defensive parsing ─────────────────────────────────────── #


@pytest.mark.asyncio
async def test_non_json_response_returns_zero_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returned prose with no JSON object ⇒ zero confidence."""

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response("Sorry, I cannot classify this.")

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("access_code_release"),),
    )
    assert result.scenario_id == ""
    assert result.confidence == 0.0
    assert "JSON" in result.rationale


@pytest.mark.asyncio
async def test_invalid_decision_type_is_blanked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision-type values outside the valid set blank the field."""

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response(
            '{"scenario_id":"x",'
            '"decision_type":"hallucinated_decision",'
            '"confidence":0.5,'
            '"rationale":""}',
        )

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("x"),),
    )
    assert result.decision_type == ""


@pytest.mark.asyncio
async def test_confidence_clamped_to_unit_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confidence outside ``[0, 1]`` is clamped, not rejected."""

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response(
            '{"scenario_id":"x","decision_type":"inform",'
            '"confidence":1.7,"rationale":""}',
        )

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("x"),),
    )
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_non_numeric_confidence_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage confidence falls back to ``0.5`` (mid-range)."""

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response(
            '{"scenario_id":"x","decision_type":"inform",'
            '"confidence":"not a number","rationale":""}',
        )

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("x"),),
    )
    assert result.confidence == 0.5


# ── transport errors ──────────────────────────────────────── #


@pytest.mark.asyncio
async def test_transport_error_returns_zero_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure is translated, not propagated."""

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        boom,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("x"),),
    )
    assert result.scenario_id == ""
    assert result.confidence == 0.0
    assert "RuntimeError" in result.rationale


@pytest.mark.asyncio
async def test_rationale_truncated_to_200_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long rationales are truncated so the audit log stays bounded."""
    long_text = "x" * 500

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response(
            '{"scenario_id":"x","decision_type":"inform",'
            '"confidence":0.5,'
            f'"rationale":"{long_text}"}}',
        )

    monkeypatch.setattr(
        "brain_engine.patterns.litellm_classifier_client."
        "litellm.acompletion",
        fake_acompletion,
    )
    client = LiteLLMClassifierClient()
    result = await client.classify(
        message="hi",
        language="en",
        candidates=(_candidate("x"),),
    )
    assert len(result.rationale) <= 200


_ = Sequence  # re-export keeps import alive for type-checkers
