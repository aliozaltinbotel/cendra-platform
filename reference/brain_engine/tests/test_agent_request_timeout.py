"""Agent LLM calls must carry a per-request timeout.

Both ``litellm.acompletion`` call sites in the conversation agent
(:meth:`ConversationService._run_agent` and
:meth:`ConversationService._execute_tool_calls`) used to omit
``timeout=``.  A stalled provider turn therefore hung the ``await``
forever: the broad ``except`` never fired, the SSE stream went silent,
and the nginx ingress reset the idle HTTP/2 stream at its 60s default —
which the browser surfaces as ``ERR_HTTP2_PROTOCOL_ERROR``.

These tests pin the fix's contract:

* every agent completion is invoked with
  ``timeout=_AGENT_REQUEST_TIMEOUT_SECONDS`` (kept under the 60s ingress
  idle ceiling), and
* a raised ``litellm.Timeout`` is caught and converted into the
  fallback reply instead of escaping — so the pipeline still reaches
  ``RUN_FINISHED`` and the stream closes cleanly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
)
from brain_engine.conversation.service import (
    _AGENT_REQUEST_TIMEOUT_SECONDS,
    ConversationService,
)

_ACOMPLETION = "brain_engine.conversation.service.litellm.acompletion"


def _make_state(*, system_prompt: str = "system") -> PipelineState:
    """Minimal pipeline state with a system prompt and empty history."""
    request = ConversationRequest(customer_id="cust1", property_id="prop1")
    state = PipelineState(request=request)
    state.system_prompt = system_prompt
    return state


def _text_response(text: str) -> SimpleNamespace:
    """A completion response with plain content and no tool calls."""
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _timeout() -> litellm.Timeout:
    """A representative provider stall surfaced by litellm."""
    return litellm.Timeout(
        "request timed out",
        model="gpt-4o",
        llm_provider="azure",
    )


# ---------------------------------------------------------------------------
# timeout= reaches both call sites
# ---------------------------------------------------------------------------


async def test_run_agent_passes_timeout_to_acompletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConversationService()
    monkeypatch.setattr(
        service, "_get_enabled_tools", lambda settings, intent_result=None: [],
    )
    acompletion = AsyncMock(return_value=_text_response("hello"))
    monkeypatch.setattr(_ACOMPLETION, acompletion)

    state = await service._run_agent(_make_state(), MagicMock())

    assert state.agent_response == "hello"
    acompletion.assert_awaited_once()
    assert (
        acompletion.await_args.kwargs["timeout"]
        == _AGENT_REQUEST_TIMEOUT_SECONDS
    )


async def test_execute_tool_calls_passes_timeout_to_acompletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConversationService()
    acompletion = AsyncMock(return_value=_text_response("done"))
    monkeypatch.setattr(_ACOMPLETION, acompletion)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system"},
    ]
    # No pending tool calls -> the round runs straight to the next
    # completion, exercising the second call site without invoking tools.
    state = await service._execute_tool_calls(
        _make_state(), [], [], messages,
    )

    assert state.agent_response == "done"
    acompletion.assert_awaited_once()
    assert (
        acompletion.await_args.kwargs["timeout"]
        == _AGENT_REQUEST_TIMEOUT_SECONDS
    )


# ---------------------------------------------------------------------------
# A timed-out turn degrades to the fallback instead of hanging / escaping
# ---------------------------------------------------------------------------


async def test_run_agent_timeout_degrades_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConversationService()
    monkeypatch.setattr(
        service, "_get_enabled_tools", lambda settings, intent_result=None: [],
    )
    monkeypatch.setattr(
        _ACOMPLETION, AsyncMock(side_effect=_timeout()),
    )

    state = await service._run_agent(_make_state(), MagicMock())

    # No exception escapes; the guest still gets a graceful reply, so the
    # pipeline proceeds to RUN_FINISHED and the SSE stream closes cleanly.
    assert state.agent_response.startswith("I apologize")


def test_agent_timeout_stays_under_ingress_idle_ceiling() -> None:
    # nginx ingress resets an idle HTTP/2 stream at its 60s default; the
    # per-call timeout must fire first to convert a stall into a clean reply.
    assert 0 < _AGENT_REQUEST_TIMEOUT_SECONDS < 60
