"""Tests for the PipelineState memory_facts / conversation_summary fields.

Task 1 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
for the baseline) declares two new Pydantic fields on
:class:`brain_engine.conversation.models.PipelineState`:

* ``memory_facts: list[str]`` — populated by Task 4 once the
  ``memory_system`` dependency is wired into the conversation
  pipeline.  Defaults to ``[]`` so the pre-Task-4 path produces an
  empty ``[ESTABLISHED FACTS]`` section instead of raising.
* ``conversation_summary: str`` — same intent for the
  ``[CONVERSATION SUMMARY]`` section.

These tests pin three guarantees:

1. The new fields exist with the documented defaults.
2. Pydantic accepts explicit values without coercion surprises.
3. ``ConversationService._build_memory_context`` propagates the
   field values into the :class:`ContextAssembler` call so a future
   Task 4 can populate ``state.memory_facts`` and trust that the
   facts will reach the LLM prompt.
"""

from __future__ import annotations

from typing import Any

from brain_engine.context.assembler import AssembledContext
from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
)
from brain_engine.conversation.service import ConversationService

# ---------------------------------------------------------------------------
# Stub assembler — captures the kwargs passed to ``assemble``
# ---------------------------------------------------------------------------


class _StubAssembler:
    """Minimal :class:`ContextAssembler` substitute for unit tests.

    Records the keyword arguments of every ``assemble`` call so the
    tests can assert what flowed in from ``state.memory_facts`` and
    ``state.conversation_summary``.  Returns a stable
    :class:`AssembledContext` so the production return-type contract
    is preserved.
    """

    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}

    def assemble(
        self,
        facts: Any = (),
        summary: str = "",
        recent_messages: Any = (),
    ) -> AssembledContext:
        self.captured = {
            "facts": list(facts),
            "summary": summary,
            "recent_messages": list(recent_messages),
        }
        return AssembledContext(text="STUB")


# ---------------------------------------------------------------------------
# Field declaration
# ---------------------------------------------------------------------------


def test_pipeline_state_has_memory_facts_default_empty() -> None:
    """``memory_facts`` defaults to an empty list, not ``None``."""
    request = ConversationRequest(customer_id="t")
    state = PipelineState(request=request)
    assert state.memory_facts == []
    assert state.conversation_summary == ""


def test_pipeline_state_accepts_memory_facts() -> None:
    """Explicit ``memory_facts`` value passes through Pydantic."""
    request = ConversationRequest(customer_id="t")
    state = PipelineState(
        request=request,
        memory_facts=["fact one", "fact two"],
        conversation_summary="prior turns",
    )
    assert state.memory_facts == ["fact one", "fact two"]
    assert state.conversation_summary == "prior turns"


def test_memory_facts_list_independent_per_state() -> None:
    """Default-factory list is not shared across instances."""
    request_a = ConversationRequest(customer_id="a")
    request_b = ConversationRequest(customer_id="b")
    state_a = PipelineState(request=request_a)
    state_b = PipelineState(request=request_b)

    state_a.memory_facts.append("a-only")
    assert state_b.memory_facts == []


# ---------------------------------------------------------------------------
# _build_memory_context propagation
# ---------------------------------------------------------------------------


def test_build_memory_context_propagates_facts_to_assembler() -> None:
    """Facts on the state reach the ContextAssembler call."""
    stub = _StubAssembler()
    service = ConversationService(context_assembler=stub)
    request = ConversationRequest(customer_id="t")
    state = PipelineState(
        request=request,
        memory_facts=["wifi password is GUEST2026"],
        conversation_summary="prior turns",
    )

    service._build_memory_context(state)

    assert stub.captured["facts"] == ["wifi password is GUEST2026"]
    assert stub.captured["summary"] == "prior turns"


def test_build_memory_context_empty_state_skips_assembler() -> None:
    """Empty facts + empty summary + no recent messages → no call."""
    stub = _StubAssembler()
    service = ConversationService(context_assembler=stub)
    request = ConversationRequest(customer_id="t")
    state = PipelineState(request=request)

    out = service._build_memory_context(state)

    assert out == ""
    assert stub.captured == {}
