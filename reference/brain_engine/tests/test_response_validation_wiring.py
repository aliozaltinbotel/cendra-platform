"""Tests for the AG-UI response-validation step (R3).

The Cendra adapter path runs every LLM draft through
:meth:`brain_engine.guardrails.pipeline.GuardrailPipeline.validate_response`
(see ``cendra_adapter._validate_guest_response``).  The AG-UI /
Sandbox path historically skipped that step — the LLM output went
straight into the SSE TEXT_MESSAGE_CONTENT stream — which let
through the Tier-1 / Tier-2 / Tier-3 violations Sandbox UI
captured on 2026-05-18 (WiFi-password leak in Inquiry status; the
fake "dispatched repair team" reply).

This module pins the new wiring:

* :func:`_response_validation_enabled` reads the env flag on every
  turn and defaults off.
* :meth:`ConversationService._validate_agent_response` is a
  three-guard no-op (flag off / pipeline ``None`` / empty draft).
* When all guards open it forwards the draft to the injected
  pipeline, applies the ``cleaned_response`` and surfaces failure
  metadata on the state (``response_flags.is_need_attention`` plus
  ``response_validation_failures``).
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
)
from brain_engine.conversation.service import (
    ConversationService,
    _response_validation_enabled,
)

# -- env flag contract ---------------------------------------------------


def test_response_validation_flag_default_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var is unset by default → validation is off so legacy
    deployments stay byte-identical to the pre-R3 behaviour."""
    monkeypatch.delenv("BRAIN_RESPONSE_VALIDATION_ENABLED", raising=False)
    assert _response_validation_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on"])
def test_response_validation_flag_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    truthy: str,
) -> None:
    """The flag mirrors the existing ``_GUARDRAIL_FALSY`` convention:
    anything outside the falsy set turns the gate on."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", truthy)
    assert _response_validation_enabled() is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off"])
def test_response_validation_flag_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    falsy: str,
) -> None:
    """The full set of documented falsy strings keeps the gate off."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", falsy)
    assert _response_validation_enabled() is False


# -- _validate_agent_response: three-guard no-op contract ----------------


def _bare_service(
    *,
    pipeline: Any = None,
) -> ConversationService:
    """Build a ConversationService skeleton without touching infra.

    ``ConversationService.__init__`` touches Redis / Postgres /
    classifiers — none of which the unit under test needs.  We
    bypass it with ``__new__`` and attach only the fields the
    validator reads.
    """
    svc = ConversationService.__new__(ConversationService)
    svc._guardrail_pipeline = pipeline
    return svc


def _state_with_draft(draft: str = "We have free Wi-Fi") -> PipelineState:
    """Build a PipelineState carrying a non-empty draft reply."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        guest_id="G1",
        message="what is the wifi password",
    )
    state = PipelineState(request=request)
    state.agent_response = draft
    return state


def test_validate_skips_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default deploy: flag off → validator never calls the pipeline,
    state stays untouched."""
    monkeypatch.delenv("BRAIN_RESPONSE_VALIDATION_ENABLED", raising=False)
    pipeline = MagicMock()
    pipeline.validate_response = MagicMock()
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft()

    svc._validate_agent_response(state)

    pipeline.validate_response.assert_not_called()
    assert state.response_flags.is_need_attention is False
    assert state.response_validation_failures == []


def test_validate_skips_when_pipeline_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but pipeline not injected → still a no-op."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "true")
    svc = _bare_service(pipeline=None)
    state = _state_with_draft()

    svc._validate_agent_response(state)

    assert state.response_flags.is_need_attention is False
    assert state.response_validation_failures == []


def test_validate_skips_when_draft_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on, pipeline injected, but the LLM produced no draft —
    skip rather than ask the pipeline to "validate" an empty string.
    Postprocessing handles the empty-reply case separately."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "true")
    pipeline = MagicMock()
    pipeline.validate_response = MagicMock()
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft(draft="   ")

    svc._validate_agent_response(state)

    pipeline.validate_response.assert_not_called()


# -- _validate_agent_response: enforcement effects -----------------------


def test_validate_replaces_with_cleaned_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pipeline returns a different ``cleaned_response``
    (Lexical scrubbed a forbidden token, Format trimmed a fence,
    …), the validator forwards the cleaned text onto
    ``state.agent_response`` so the AG-UI bridge streams the safe
    version to the guest."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "1")

    class _Result:
        passed = True
        cleaned_response = "Cleaned reply"
        failures: ClassVar[list[dict[str, str]]] = []

    pipeline = MagicMock()
    pipeline.validate_response = MagicMock(return_value=_Result())
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft(draft="Raw reply")

    svc._validate_agent_response(state)

    pipeline.validate_response.assert_called_once()
    assert state.agent_response == "Cleaned reply"
    assert state.response_flags.is_need_attention is False


def test_validate_flags_need_attention_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``passed=False`` the pipeline surfaces failures the
    downstream PM panel must see — pin both the routing flag and
    the structured failure list."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "1")

    class _Result:
        passed = False
        cleaned_response = "Reply that still leaked WiFi 1234"
        failures: ClassVar[list[dict[str, str]]] = [
            {
                "check": "lexical:sensitive_disclosure",
                "message": "Reply contains WiFi password",
                "severity": "HIGH",
            },
        ]

    pipeline = MagicMock()
    pipeline.validate_response = MagicMock(return_value=_Result())
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft()

    svc._validate_agent_response(state)

    assert state.response_flags.is_need_attention is True
    assert state.response_validation_failures == [
        {
            "check": "lexical:sensitive_disclosure",
            "message": "Reply contains WiFi password",
            "severity": "HIGH",
        }
    ]


def test_validate_forwards_property_and_knowledge_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pipeline must receive property_id (for tenant-scoped
    checks) and the property knowledge text (for the Tier-3
    hallucination check)."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "1")

    class _Result:
        passed = True
        cleaned_response = "ok"
        failures: ClassVar[list[dict[str, str]]] = []

    pipeline = MagicMock()
    pipeline.validate_response = MagicMock(return_value=_Result())
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft()
    state.property_knowledge = "# Property facts\nWiFi password: 1234"

    svc._validate_agent_response(state)

    call = pipeline.validate_response.call_args
    assert call.args[0] == "We have free Wi-Fi"
    assert call.kwargs["context"]["property_id"] == "P1"
    assert call.kwargs["knowledge_base"].startswith("# Property facts")


def test_validate_swallows_pipeline_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A library-level error in the pipeline must NEVER kill the
    reply — the AG-UI handler still needs to stream ``agent_response``
    to the guest.  The validator logs the failure and returns."""
    monkeypatch.setenv("BRAIN_RESPONSE_VALIDATION_ENABLED", "1")
    pipeline = MagicMock()
    pipeline.validate_response = MagicMock(
        side_effect=RuntimeError("pipeline broke"),
    )
    svc = _bare_service(pipeline=pipeline)
    state = _state_with_draft(draft="Original reply")

    svc._validate_agent_response(state)

    assert state.agent_response == "Original reply"
    assert state.response_flags.is_need_attention is False
