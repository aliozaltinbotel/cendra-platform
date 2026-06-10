"""Tests for rag_search Qdrant fallback property scoping.

The Azure Search adapter scopes results to ``property_id`` (line 92
of ``rag_search.py``).  When Azure is unavailable the fallback uses
:class:`SemanticMemory` which is the same Qdrant collection shared
by every property.  Without an explicit ``metadata_filter`` the
bi-encoder returns the semantically-best chunks across all
properties — a cross-property knowledge leak (a paid-late-checkin
chunk from one property could surface in another property's reply).

These tests pin the contract: the Qdrant fallback path MUST pass
``metadata_filter={"property_id": property_id}`` to
:meth:`SemanticMemory.search` whenever a property is in scope, and
MUST pass ``metadata_filter=None`` when the runtime carries no
property (so property-less callers — admin tools, smoke tests —
keep working).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import brain_engine.conversation_tools.rag_search as rag_search_mod
from brain_engine.tools.runtime import ToolRuntime


@pytest.fixture
def fail_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the Azure Search adapter to raise so the Qdrant fallback runs."""
    class _BoomAzureAdapter:
        def __init__(self) -> None:
            raise RuntimeError("azure search offline in test")

    monkeypatch.setattr(
        "brain_engine.integrations.azure_search_adapter.AzureSearchAdapter",
        _BoomAzureAdapter,
    )


@pytest.fixture
def capture_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Replace :class:`SemanticMemory` with a stub that records ``search`` kwargs.

    Returns a dict the test can read after invoking the tool so it
    can assert on the exact arguments the fallback path forwarded
    to Qdrant.
    """
    captured: dict[str, Any] = {"calls": []}

    class _StubSemanticMemory:
        def __init__(self) -> None:
            self.search = AsyncMock(side_effect=self._fake_search)

        async def _fake_search(
            self,
            query: str,
            top_k: int = 5,
            metadata_filter: dict[str, Any] | None = None,
            **_: Any,
        ) -> list[dict[str, Any]]:
            captured["calls"].append(
                {
                    "query": query,
                    "top_k": top_k,
                    "metadata_filter": metadata_filter,
                }
            )
            return []

    monkeypatch.setattr(
        "brain_engine.memory.semantic_memory.SemanticMemory",
        _StubSemanticMemory,
    )
    return captured


@pytest.fixture(autouse=True)
def silence_rag_emitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid emitting AG-UI SSE events from the tool during unit tests."""
    monkeypatch.setattr(rag_search_mod, "emit_rag_hit", MagicMock())


@pytest.mark.asyncio
async def test_qdrant_fallback_scopes_to_property_id(
    fail_azure: None,
    capture_qdrant: dict[str, Any],
) -> None:
    """When the tool is invoked with a property_id the fallback path
    MUST forward ``metadata_filter={"property_id": "<pid>"}`` to
    :meth:`SemanticMemory.search`.

    This is the regression guard against the cross-property leak
    where one property's chunks surfaced in another's reply because
    the bi-encoder had nothing to filter on.
    """
    runtime = ToolRuntime(config={"property_id": "PID-12345"})

    await rag_search_mod.rag_document_search(
        "wifi password",
        runtime=runtime,
    )

    assert len(capture_qdrant["calls"]) == 1
    call = capture_qdrant["calls"][0]
    assert call["query"] == "wifi password"
    assert call["top_k"] == 6
    assert call["metadata_filter"] == {"property_id": "PID-12345"}


@pytest.mark.asyncio
async def test_qdrant_fallback_omits_filter_when_property_id_missing(
    fail_azure: None,
    capture_qdrant: dict[str, Any],
) -> None:
    """No property_id in scope ⇒ ``metadata_filter`` must be ``None``.

    The pre-existing search contract treats ``None`` as "no filter";
    passing ``{"property_id": ""}`` would index empty-string and
    return zero hits, silently breaking property-less callers
    (smoke tests, admin tools).  This pins the explicit ``None``.
    """
    runtime = ToolRuntime(config={})

    await rag_search_mod.rag_document_search(
        "wifi password",
        runtime=runtime,
    )

    assert len(capture_qdrant["calls"]) == 1
    assert capture_qdrant["calls"][0]["metadata_filter"] is None


@pytest.mark.asyncio
async def test_qdrant_fallback_omits_filter_when_runtime_is_none(
    fail_azure: None,
    capture_qdrant: dict[str, Any],
) -> None:
    """Callers without a runtime (legacy / smoke) keep working.

    The tool's signature already accepts ``runtime=None`` (see
    ``_get_property_id`` early return at line 62).  This test pins
    that the Qdrant fallback also treats this as "no filter"
    rather than crashing.
    """
    await rag_search_mod.rag_document_search(
        "wifi password",
        runtime=None,
    )

    assert len(capture_qdrant["calls"]) == 1
    assert capture_qdrant["calls"][0]["metadata_filter"] is None
