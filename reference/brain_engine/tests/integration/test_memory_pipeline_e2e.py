"""End-to-end smoke for the memory wiring chain (Task 8).

Final piece of CLAUDE_CODE_WIRING_FIX_PLAN.md — proves that the
chain Tasks 1-5 + Task 7 + the factory follow-up land
end-to-end on the conversation pipeline:

* Task 1 declared ``state.memory_facts`` on ``PipelineState``.
* Task 2 added ``memory_system`` DI to ``ConversationService``.
* Task 3 aliased ``app.state.memory_system`` to the lifespan memory.
* Task 4 introduced ``_load_memory_context`` between
  ``_append_pm_facts`` and ``_classify`` so the bi-encoder hits
  ``MemorySystem.semantic.search`` and stores the results on
  ``state.memory_facts``.
* Task 5 wrapped ``SemanticMemory.search`` with optional hybrid
  retrieval — invisible here because the flag stays off.
* Sprint A (yesterday) shipped a cross-encoder reranker the new
  pipeline stage consults when ``BRAIN_RERANKER_ENABLED=1``.
* Factory follow-up (today) finally wires the four collaborators
  through ``create_full_system`` so the runtime path exists at all.

This module exercises the *runtime* path the production lifespan
takes — building a real ``MemorySystem`` is intentionally avoided
(no Redis, no Qdrant), but every other piece of the chain (the
``ConversationService``, ``_load_memory_context``,
``_build_memory_context``, and the ``ContextAssembler`` it feeds)
runs as production code.

The contract the smoke pins:

1. With ``BRAIN_MEMORY_RETRIEVAL_ENABLED=1`` and a memory system
   injected, the bi-encoder result text reaches the assembled
   context block and is therefore visible to the LLM agent.
2. Multi-tenancy isolation works: the metadata filter passed to
   ``MemorySystem.semantic.search`` includes both ``customer_id``
   and ``property_id``.
3. With either flag off (``BRAIN_MEMORY_RETRIEVAL_ENABLED`` unset
   or ``memory_system`` not injected), the ``[ESTABLISHED FACTS]``
   block is empty — exactly the pre-Task-4 production behaviour.
4. With ``BRAIN_RERANKER_ENABLED=1`` the Sprint A reranker is
   consulted; without it the bi-encoder ranking reaches the state
   verbatim.
5. The ``ContextAssembler.AssembledContext.text`` actually carries
   the fact inside ``[ESTABLISHED FACTS]`` so ``_assemble_prompt``
   downstream injects it into the system prompt.

Tests do not require Redis / Qdrant / litellm — every external
collaborator is stubbed at the narrowest seam.  Sentence-transformer
weights are not loaded.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.context.assembler import AssembledContext
from brain_engine.conversation.models import (
    ConversationMessage,
    ConversationRequest,
    PipelineState,
    SenderType,
)
from brain_engine.conversation.service import ConversationService
from brain_engine.memory.semantic_memory import MemoryRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_env() -> Iterator[None]:
    """Strip every memory / reranker env flag between tests.

    The autouse fixture also clears whatever the test sets so the
    next file in the same pytest run cannot inherit a partially
    enabled smoke setup.
    """
    keys = (
        "BRAIN_MEMORY_RETRIEVAL_ENABLED",
        "BRAIN_RERANKER_ENABLED",
        "BRAIN_HYBRID_RETRIEVAL_ENABLED",
    )
    snapshot = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


def _build_memory_system(
    *,
    facts: list[MemoryRecord] | None = None,
    episodes: list[Any] | None = None,
) -> MagicMock:
    """Stub ``MemorySystem`` with awaitable ``semantic`` / ``episodic``.

    The shape mirrors :class:`brain_engine.memory.factory.MemorySystem`'s
    ``.semantic`` and ``.episodic`` attributes that
    ``_load_memory_context`` consults.
    """
    memory: Any = MagicMock(name="MemorySystem")
    memory.semantic.search = AsyncMock(return_value=facts or [])
    memory.episodic.get_recent = AsyncMock(return_value=episodes or [])
    return memory


def _build_request(
    *,
    customer_id: str = "tenant1",
    property_id: str = "prop1",
    guest_id: str = "guest_john",
    message: str = "What's the WiFi password?",
) -> ConversationRequest:
    """Compact request fixture covering the multi-tenancy keys."""
    return ConversationRequest(
        customer_id=customer_id,
        property_id=property_id,
        guest_id=guest_id,
        messages=[
            ConversationMessage(
                sender_type=SenderType.GUEST,
                text=message,
            ),
        ],
    )


def _build_state(
    request: ConversationRequest,
    *,
    cleaned_message: str | None = None,
) -> PipelineState:
    """Pipeline state pre-loaded to the point ``_load_memory_context`` runs."""
    state = PipelineState(request=request)
    state.cleaned_message = cleaned_message or request.messages[0].text
    return state


# ---------------------------------------------------------------------------
# Smoke 1 — fact reaches the assembled context with both gates open
# ---------------------------------------------------------------------------


async def test_memory_fact_reaches_assembled_context_text() -> None:
    """End-to-end smoke for Task 4 + Task 1 + ContextAssembler.

    With both ``memory_system`` injected and the env flag on, a
    fact returned by ``MemorySystem.semantic.search`` must reach
    the rendered ``AssembledContext.text`` produced by
    ``_build_memory_context``.  This is what the LLM agent actually
    sees in its system prompt — so passing this test means
    Task 4's wiring is alive end-to-end.
    """
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    fact_text = "WiFi password is GUEST2026"
    memory = _build_memory_system(
        facts=[
            MemoryRecord(
                id="r1",
                text=fact_text,
                metadata={"customer_id": "tenant1"},
                score=0.95,
            ),
        ],
    )
    service = ConversationService(memory_system=memory)
    state = _build_state(_build_request())

    await service._load_memory_context(state)
    rendered = service._build_memory_context(state)

    assert state.memory_facts == [fact_text]
    assert fact_text in rendered


async def test_metadata_filter_carries_customer_and_property() -> None:
    """Multi-tenancy guard reaches the bi-encoder call.

    ``_load_memory_context`` must build a ``metadata_filter`` that
    pins both the tenant and the property scope so one customer
    cannot retrieve another's facts.
    """
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory_system(facts=[])
    service = ConversationService(memory_system=memory)
    state = _build_state(
        _build_request(
            customer_id="cust42",
            property_id="prop9",
        ),
    )

    await service._load_memory_context(state)

    call = memory.semantic.search.await_args
    assert call.kwargs["metadata_filter"] == {
        "customer_id": "cust42",
        "property_id": "prop9",
    }


# ---------------------------------------------------------------------------
# Smoke 2 — no-op paths preserve pre-Task-4 behaviour
# ---------------------------------------------------------------------------


async def test_no_memory_system_keeps_facts_empty() -> None:
    """Pre-Task-3 deployments (no lifespan alias) — empty facts."""
    service = ConversationService(memory_system=None)
    state = _build_state(_build_request())

    await service._load_memory_context(state)
    rendered = service._build_memory_context(state)

    assert state.memory_facts == []
    # ``[ESTABLISHED FACTS]`` block must be absent from the layout
    # when no facts populated.  Recent guest messages still flow
    # through ``[RECENT MESSAGES]`` — that is unrelated to memory.
    assert "ESTABLISHED FACTS" not in rendered


async def test_flag_off_keeps_facts_empty_even_with_memory_system() -> None:
    """Lifespan alias on but env flag off — no-op, no I/O."""
    memory = _build_memory_system(
        facts=[MemoryRecord(id="r1", text="leak", score=0.9)],
    )
    service = ConversationService(memory_system=memory)
    state = _build_state(_build_request())

    await service._load_memory_context(state)

    assert state.memory_facts == []
    memory.semantic.search.assert_not_called()


# ---------------------------------------------------------------------------
# Smoke 3 — Sprint A reranker integration
# ---------------------------------------------------------------------------


async def test_reranker_invoked_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RERANKER_ENABLED on -> reranker rescores the bi-encoder output."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    os.environ["BRAIN_RERANKER_ENABLED"] = "1"

    fake_reranker = MagicMock()
    fake_reranker.rerank = MagicMock(return_value=[])
    monkeypatch.setattr(
        "brain_engine.memory.reranker.build_default_reranker",
        lambda: fake_reranker,
    )

    memory = _build_memory_system(
        facts=[
            MemoryRecord(id="r1", text="some fact", score=0.9),
        ],
    )
    service = ConversationService(memory_system=memory)
    state = _build_state(_build_request())

    await service._load_memory_context(state)

    fake_reranker.rerank.assert_called_once()
    kwargs = fake_reranker.rerank.call_args.kwargs
    # Top-N final must come from the documented constant (8) so the
    # reranker has to actually trim the candidate list.
    assert kwargs["top_n"] == 8


async def test_reranker_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RERANKER_ENABLED off -> reranker module is never imported."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    builder_calls: list[None] = []
    monkeypatch.setattr(
        "brain_engine.memory.reranker.build_default_reranker",
        lambda: builder_calls.append(None) or None,
    )
    memory = _build_memory_system(
        facts=[MemoryRecord(id="r1", text="bare fact", score=0.9)],
    )
    service = ConversationService(memory_system=memory)
    state = _build_state(_build_request())

    await service._load_memory_context(state)

    assert state.memory_facts == ["bare fact"]
    assert builder_calls == []


# ---------------------------------------------------------------------------
# Smoke 4 — assembled context shape is real ContextAssembler output
# ---------------------------------------------------------------------------


async def test_assembled_context_is_real_assembler_output() -> None:
    """The ``_build_memory_context`` return value comes from the real
    ``ContextAssembler`` — not a stub — so the layout produced here
    is exactly what the LLM prompt sees in production.
    """
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    fact = "Late checkout policy: free until 13:00"
    memory = _build_memory_system(
        facts=[MemoryRecord(id="r1", text=fact, score=0.9)],
    )
    service = ConversationService(memory_system=memory)
    state = _build_state(_build_request())

    await service._load_memory_context(state)
    rendered = service._build_memory_context(state)

    assert isinstance(rendered, str)
    # ``ContextAssembler`` emits the section header before the body.
    # Pin the header literal so a change to the layout template
    # surfaces immediately in this smoke.
    assert "ESTABLISHED FACTS" in rendered
    assert fact in rendered


async def test_assembler_returns_assembled_context_dataclass() -> None:
    """Sanity: the production assembler still returns
    :class:`AssembledContext`; ``_build_memory_context`` only
    extracts ``.text`` so a typo on either side surfaces here."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory_system(
        facts=[MemoryRecord(id="r1", text="anchor", score=0.9)],
    )
    service = ConversationService(memory_system=memory)

    raw = service._context_assembler.assemble(
        facts=["anchor"],
        summary="",
        recent_messages=(),
    )
    assert isinstance(raw, AssembledContext)
    assert raw.facts_count == 1
