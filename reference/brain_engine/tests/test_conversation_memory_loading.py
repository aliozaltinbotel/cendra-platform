"""Tests for the Task 4 ``_load_memory_context`` pipeline stage.

Task 4 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
for the baseline) introduces the first read-side consumer of the
cognitive memory system.  Whenever ``BRAIN_MEMORY_RETRIEVAL_ENABLED``
is truthy *and* a ``memory_system`` was injected via Tasks 2 + 3,
the conversation pipeline now:

* Issues a bi-encoder semantic search scoped to
  ``(customer_id, property_id)`` — the multi-tenancy guard.
* Optionally rescores the candidates through the Sprint A
  cross-encoder when ``BRAIN_RERANKER_ENABLED`` is on.
* Populates ``state.memory_facts`` (Task 1 field) with the final
  top-K texts.
* Builds a flat ``state.conversation_summary`` from episodic
  memory once the conversation has more than five turns.
* Logs and continues with empty facts on any I/O failure.

These tests pin every branch of that contract through stubbed
``MemorySystem`` collaborators — no Redis, no Qdrant, no 568 MB
reranker checkpoint touched.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.conversation.models import (
    ConversationMessage,
    ConversationRequest,
    PipelineState,
    SenderType,
)
from brain_engine.conversation.service import (
    ConversationService,
    _build_memory_filter,
    _memory_retrieval_enabled,
    _record_text,
    _summarize_episodes,
)

# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _StubRecord:
    """Mimics ``MemoryRecord`` with the single ``text`` attribute."""

    def __init__(self, rid: str, text: str) -> None:
        self.id = rid
        self.text = text


class _StubEpisode:
    """Mimics ``Episode`` with the single ``content`` attribute."""

    def __init__(self, content: str) -> None:
        self.content = content


def _build_memory(
    *,
    records: list[_StubRecord] | Exception | None = None,
    episodes: list[_StubEpisode] | Exception | None = None,
) -> MagicMock:
    """Return a stub MemorySystem whose I/O calls are mocked.

    ``records`` / ``episodes`` may be a list (returned verbatim) or
    an Exception (raised by the corresponding mock).  ``None``
    defaults to an empty list so callers only configure what their
    test cares about.
    """
    memory: Any = MagicMock(name="MemorySystem")
    if isinstance(records, Exception):
        memory.semantic.search = AsyncMock(side_effect=records)
    else:
        memory.semantic.search = AsyncMock(return_value=records or [])
    if isinstance(episodes, Exception):
        memory.episodic.get_recent = AsyncMock(side_effect=episodes)
    else:
        memory.episodic.get_recent = AsyncMock(
            return_value=episodes or [],
        )
    return memory


def _make_state(
    *,
    cleaned_message: str = "test query",
    customer_id: str = "tenant1",
    property_id: str = "prop1",
    history_length: int = 1,
) -> PipelineState:
    """Build a PipelineState ready to feed ``_load_memory_context``."""
    messages = [
        ConversationMessage(
            sender_type=SenderType.GUEST,
            text=f"msg {i}",
        )
        for i in range(history_length)
    ]
    request = ConversationRequest(
        customer_id=customer_id,
        property_id=property_id,
        messages=messages,
    )
    state = PipelineState(request=request)
    state.cleaned_message = cleaned_message
    return state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_memory_env() -> Iterator[None]:
    snapshot = {
        key: os.environ.pop(key, None)
        for key in (
            "BRAIN_MEMORY_RETRIEVAL_ENABLED",
            "BRAIN_RERANKER_ENABLED",
        )
    }
    try:
        yield
    finally:
        for key in (
            "BRAIN_MEMORY_RETRIEVAL_ENABLED",
            "BRAIN_RERANKER_ENABLED",
        ):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing — module-level helpers
# ---------------------------------------------------------------------------


def test_memory_retrieval_flag_off_by_default() -> None:
    assert _memory_retrieval_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_memory_retrieval_flag_truthy(raw: str) -> None:
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = raw
    assert _memory_retrieval_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_memory_retrieval_flag_falsy(raw: str) -> None:
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = raw
    assert _memory_retrieval_enabled() is False


# ---------------------------------------------------------------------------
# _build_memory_filter
# ---------------------------------------------------------------------------


def test_filter_includes_both_customer_and_property() -> None:
    request = ConversationRequest(
        customer_id="cust1", property_id="prop1",
    )
    out = _build_memory_filter(request)
    assert out == {"customer_id": "cust1", "property_id": "prop1"}


def test_filter_omits_empty_property() -> None:
    request = ConversationRequest(customer_id="cust1")
    assert _build_memory_filter(request) == {"customer_id": "cust1"}


def test_filter_omits_empty_customer() -> None:
    request = ConversationRequest(customer_id="")
    request.property_id = "prop1"  # Pydantic allows mutation
    assert _build_memory_filter(request) == {"property_id": "prop1"}


# ---------------------------------------------------------------------------
# _record_text + _summarize_episodes
# ---------------------------------------------------------------------------


def test_record_text_handles_attr_records() -> None:
    assert _record_text(_StubRecord("r1", "hello")) == "hello"


def test_record_text_handles_dict_records() -> None:
    assert _record_text({"text": "world"}) == "world"


def test_record_text_returns_empty_for_unknown_shape() -> None:
    assert _record_text(42) == ""


def test_summarize_episodes_concatenates_content() -> None:
    eps = [_StubEpisode("first"), _StubEpisode("second")]
    assert _summarize_episodes(eps) == "first second"


def test_summarize_episodes_empty_list_returns_empty() -> None:
    assert _summarize_episodes([]) == ""


def test_summarize_episodes_skips_blank_content() -> None:
    eps = [_StubEpisode(""), _StubEpisode("real")]
    assert _summarize_episodes(eps) == "real"


# ---------------------------------------------------------------------------
# _load_memory_context — short-circuits
# ---------------------------------------------------------------------------


async def test_load_memory_context_noop_without_memory_system() -> None:
    """No memory_system -> nothing happens, no exception."""
    service = ConversationService(memory_system=None)
    state = _make_state()
    await service._load_memory_context(state)
    assert state.memory_facts == []
    assert state.conversation_summary == ""


async def test_load_memory_context_noop_without_flag() -> None:
    """Flag off -> memory_system never called, defaults preserved."""
    memory = _build_memory(records=[_StubRecord("r1", "fact")])
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    assert state.memory_facts == []
    memory.semantic.search.assert_not_called()


async def test_load_memory_context_noop_when_cleaned_message_empty() -> None:
    """No query -> short-circuit before any MemorySystem call."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(records=[_StubRecord("r1", "fact")])
    service = ConversationService(memory_system=memory)
    state = _make_state(cleaned_message="")

    await service._load_memory_context(state)

    assert state.memory_facts == []
    memory.semantic.search.assert_not_called()


# ---------------------------------------------------------------------------
# _load_memory_context — happy paths
# ---------------------------------------------------------------------------


async def test_load_memory_context_populates_facts() -> None:
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(
        records=[
            _StubRecord("r1", "Guest prefers late checkout"),
            _StubRecord("r2", "Property has WiFi password ABC"),
        ],
    )
    service = ConversationService(memory_system=memory)
    state = _make_state(cleaned_message="late checkout please")

    await service._load_memory_context(state)

    assert state.memory_facts == [
        "Guest prefers late checkout",
        "Property has WiFi password ABC",
    ]


async def test_load_memory_context_passes_filter_to_semantic_search() -> None:
    """Multi-tenancy filter reaches MemorySystem.semantic.search."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(records=[])
    service = ConversationService(memory_system=memory)
    state = _make_state(
        customer_id="cust42",
        property_id="prop9",
    )

    await service._load_memory_context(state)

    call = memory.semantic.search.call_args
    assert call.kwargs["metadata_filter"] == {
        "customer_id": "cust42",
        "property_id": "prop9",
    }
    assert call.kwargs["query"] == "test query"


async def test_load_memory_context_caps_at_top_k_when_no_reranker() -> None:
    """Bi-encoder returns 20 candidates, only 8 reach the state."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    records = [_StubRecord(f"r{i}", f"fact-{i}") for i in range(20)]
    memory = _build_memory(records=records)
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    assert len(state.memory_facts) == 8
    assert state.memory_facts == [f"fact-{i}" for i in range(8)]


# ---------------------------------------------------------------------------
# Conversation summary branch
# ---------------------------------------------------------------------------


async def test_summary_skipped_when_short_conversation() -> None:
    """5 messages or fewer -> no summary built, episodic untouched."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(
        records=[],
        episodes=[_StubEpisode("ep1")],
    )
    service = ConversationService(memory_system=memory)
    state = _make_state(history_length=3)

    await service._load_memory_context(state)

    assert state.conversation_summary == ""
    memory.episodic.get_recent.assert_not_called()


async def test_summary_built_when_conversation_long_enough() -> None:
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(
        records=[],
        episodes=[_StubEpisode("alpha"), _StubEpisode("beta")],
    )
    service = ConversationService(memory_system=memory)
    state = _make_state(history_length=10)

    await service._load_memory_context(state)

    assert state.conversation_summary == "alpha beta"
    memory.episodic.get_recent.assert_awaited_once()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_search_failure_keeps_pipeline_alive() -> None:
    """Qdrant down -> warn + empty facts, no exception escapes."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(records=RuntimeError("Qdrant unreachable"))
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    assert state.memory_facts == []
    assert state.conversation_summary == ""


async def test_episodic_failure_does_not_fail_facts_path() -> None:
    """Episodic outage -> empty summary, but facts still populate."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    memory = _build_memory(
        records=[_StubRecord("r1", "fact")],
        episodes=RuntimeError("Redis down"),
    )
    service = ConversationService(memory_system=memory)
    state = _make_state(history_length=10)

    await service._load_memory_context(state)

    assert state.memory_facts == ["fact"]
    assert state.conversation_summary == ""


# ---------------------------------------------------------------------------
# Reranker integration
# ---------------------------------------------------------------------------


async def test_reranker_invoked_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RERANKER_ENABLED on -> reranker.rerank called once."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    os.environ["BRAIN_RERANKER_ENABLED"] = "1"

    rerank_mock = MagicMock(return_value=[])
    fake_reranker = MagicMock()
    fake_reranker.rerank = rerank_mock

    monkeypatch.setattr(
        "brain_engine.memory.reranker.build_default_reranker",
        lambda: fake_reranker,
    )

    memory = _build_memory(records=[_StubRecord("r1", "fact")])
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    rerank_mock.assert_called_once()
    kwargs = rerank_mock.call_args.kwargs
    assert kwargs["query"] == "test query"
    assert kwargs["top_n"] == 8


async def test_reranker_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RERANKER_ENABLED off -> reranker is never built or called."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    builder_calls: list[None] = []
    monkeypatch.setattr(
        "brain_engine.memory.reranker.build_default_reranker",
        lambda: builder_calls.append(None) or None,
    )

    memory = _build_memory(records=[_StubRecord("r1", "fact")])
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    assert state.memory_facts == ["fact"]
    assert builder_calls == []


async def test_reranker_build_failure_falls_back_to_bi_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If build_default_reranker raises, facts still come from bi-enc."""
    os.environ["BRAIN_MEMORY_RETRIEVAL_ENABLED"] = "1"
    os.environ["BRAIN_RERANKER_ENABLED"] = "1"

    def _explode() -> Any:
        raise RuntimeError("checkpoint missing")

    monkeypatch.setattr(
        "brain_engine.memory.reranker.build_default_reranker",
        _explode,
    )

    memory = _build_memory(records=[_StubRecord("r1", "fact")])
    service = ConversationService(memory_system=memory)
    state = _make_state()

    await service._load_memory_context(state)

    assert state.memory_facts == ["fact"]
