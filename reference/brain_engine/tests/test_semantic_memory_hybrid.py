"""Tests for the Task 5 hybrid retrieval wire-up.

Task 5 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
for the baseline) widens
:class:`brain_engine.memory.semantic_memory.SemanticMemory.search`
with an opt-in two-stage path:

* Default (no ``sparse_encoder`` injected, or
  ``BRAIN_HYBRID_RETRIEVAL_ENABLED`` falsy) — bit-for-bit
  identical to the pre-Task-5 dense-only behaviour delegated to
  ``_dense_search``.
* Hybrid (encoder injected and flag truthy) — dense + BM25 sparse
  retrieval fed into Sprint C's
  :func:`reciprocal_rank_fusion`.  ``_sparse_search`` is currently
  a documented :class:`NotImplementedError` because it requires a
  Qdrant collection migration; ``search`` must catch the failure
  and fall back to dense.

These tests stub the dense Qdrant call and the sparse encoder so
the suite never opens a real Qdrant connection.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.memory.semantic_memory import (
    MemoryRecord,
    SemanticMemory,
)

# ---------------------------------------------------------------------------
# Fixture — strip the hybrid env flag between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_hybrid_flag() -> Iterator[None]:
    previous = os.environ.pop("BRAIN_HYBRID_RETRIEVAL_ENABLED", None)
    try:
        yield
    finally:
        os.environ.pop("BRAIN_HYBRID_RETRIEVAL_ENABLED", None)
        if previous is not None:
            os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = previous


@pytest.fixture(autouse=True)
def _stub_external_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace network-bound deps with cheap stubs.

    ``SemanticMemory.__init__`` instantiates an ``AsyncQdrantClient``
    (which spawns a compatibility-check background thread) and a
    ``SentenceTransformer`` (which loads weights from the HuggingFace
    hub).  Neither is acceptable in a unit test — the patches below
    swap both for ``MagicMock`` so construction is instant and
    side-effect free.
    """
    monkeypatch.setattr(
        "brain_engine.memory.semantic_memory.AsyncQdrantClient",
        MagicMock,
    )
    monkeypatch.setattr(
        "brain_engine.memory.semantic_memory.SentenceTransformer",
        MagicMock,
    )


# ---------------------------------------------------------------------------
# Helpers — patched SemanticMemory that does not touch Qdrant
# ---------------------------------------------------------------------------


def _records(n: int) -> list[MemoryRecord]:
    """Build deterministic ``MemoryRecord`` fixtures."""
    return [
        MemoryRecord(
            id=f"r{i}",
            text=f"fact-{i}",
            metadata={},
            score=1.0 - 0.05 * i,
        )
        for i in range(n)
    ]


def _build_memory(
    *,
    dense_records: list[MemoryRecord] | None = None,
    sparse_records: list[MemoryRecord] | Exception | None = None,
    sparse_encoder: Any = None,
) -> SemanticMemory:
    """Construct a ``SemanticMemory`` with mocked retrieval methods.

    ``_ensure_collection`` is short-circuited so no Qdrant calls
    leave the process; ``_dense_search`` and ``_sparse_search`` are
    swapped for ``AsyncMock`` instances configured per-test.
    """
    sm = SemanticMemory(sparse_encoder=sparse_encoder)
    sm._initialized = True  # bypass _ensure_collection

    sm._dense_search = AsyncMock(  # type: ignore[method-assign]
        return_value=list(dense_records or []),
    )
    if isinstance(sparse_records, Exception):
        sm._sparse_search = AsyncMock(  # type: ignore[method-assign]
            side_effect=sparse_records,
        )
    else:
        sm._sparse_search = AsyncMock(  # type: ignore[method-assign]
            return_value=list(sparse_records or []),
        )
    return sm


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_sparse_encoder_default_none() -> None:
    """No kwarg = None — preserves the pre-Task-5 footprint."""
    sm = SemanticMemory()
    assert sm._sparse_encoder is None


def test_constructor_accepts_sparse_encoder() -> None:
    """The new slot is plumbed through to the instance."""
    sentinel = object()
    sm = SemanticMemory(sparse_encoder=sentinel)
    assert sm._sparse_encoder is sentinel


# ---------------------------------------------------------------------------
# search() — dense-only paths
# ---------------------------------------------------------------------------


async def test_search_uses_dense_when_flag_off() -> None:
    """Without the flag the sparse retriever is never touched."""
    sentinel_encoder = object()
    sm = _build_memory(
        dense_records=_records(3),
        sparse_records=[],
        sparse_encoder=sentinel_encoder,
    )

    result = await sm.search(query="hello", top_k=3)

    assert [r.id for r in result] == ["r0", "r1", "r2"]
    sm._dense_search.assert_awaited_once()  # type: ignore[attr-defined]
    sm._sparse_search.assert_not_called()  # type: ignore[attr-defined]


async def test_search_uses_dense_when_no_sparse_encoder() -> None:
    """Flag on but no encoder = dense path, no warnings."""
    os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = "1"
    sm = _build_memory(
        dense_records=_records(2),
        sparse_records=[],
        sparse_encoder=None,
    )

    result = await sm.search(query="hello", top_k=2)

    assert len(result) == 2
    sm._sparse_search.assert_not_called()  # type: ignore[attr-defined]


async def test_search_propagates_metadata_filter_to_dense() -> None:
    """Multi-tenancy filter reaches ``_dense_search`` keyword args."""
    sm = _build_memory(dense_records=_records(1))
    await sm.search(
        query="q",
        top_k=1,
        metadata_filter={"customer_id": "cust1"},
    )
    call = sm._dense_search.await_args  # type: ignore[attr-defined]
    assert call.kwargs["metadata_filter"] == {"customer_id": "cust1"}
    assert call.kwargs["top_k"] == 1


# ---------------------------------------------------------------------------
# search() — hybrid path
# ---------------------------------------------------------------------------


async def test_search_runs_hybrid_when_flag_on_and_encoder_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gates open -> dense + sparse + fusion are all invoked."""
    os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = "1"

    fusion_calls: list[dict[str, Any]] = []

    def _fake_fusion(
        *,
        dense: Any,
        sparse: Any,
        key_of: Any,
        top_n: int | None = None,
    ) -> list[Any]:
        fusion_calls.append(
            {"dense": list(dense), "sparse": list(sparse), "k": top_n},
        )

        class _Item:
            def __init__(self, rec: MemoryRecord) -> None:
                self.item = rec
                self.score = 0.99

        return [_Item(rec) for rec in (list(dense)[:top_n or 1])]

    monkeypatch.setattr(
        "brain_engine.memory.hybrid_search.reciprocal_rank_fusion",
        _fake_fusion,
    )

    sm = _build_memory(
        dense_records=_records(4),
        sparse_records=_records(4),
        sparse_encoder=object(),
    )

    out = await sm.search(query="hello", top_k=2)

    assert len(out) == 2  # _fake_fusion truncates dense pool to top_n
    assert sm._sparse_search.await_count == 1  # type: ignore[attr-defined]
    assert sm._dense_search.await_count == 1  # type: ignore[attr-defined]
    assert fusion_calls[0]["k"] == 2
    # Pool is wider than top_k for fusion headroom.
    assert sm._dense_search.await_args.kwargs["top_k"] == 4


async def test_search_falls_back_to_dense_on_sparse_failure() -> None:
    """Sparse exception -> warn + dense-only fallback, no raise."""
    os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = "1"
    sm = _build_memory(
        dense_records=_records(3),
        sparse_records=NotImplementedError("collection not migrated"),
        sparse_encoder=object(),
    )

    result = await sm.search(query="hello", top_k=3)

    # Three results from the dense-only fallback path.
    assert len(result) == 3
    assert sm._dense_search.await_count == 2  # type: ignore[attr-defined]


async def test_sparse_search_raises_until_migration() -> None:
    """Stub method must surface a clear error pointing to the ticket."""
    sm = SemanticMemory(sparse_encoder=object())
    with pytest.raises(
        NotImplementedError, match="sparse-vector named-config",
    ):
        await sm._sparse_search(
            query="x", top_k=1, metadata_filter=None,
        )


# ---------------------------------------------------------------------------
# Backward compatibility anchor
# ---------------------------------------------------------------------------


async def test_pre_task5_dense_only_signature_preserved() -> None:
    """Old positional + kwarg shape still works."""
    sm = _build_memory(dense_records=_records(2))
    out = await sm.search(
        "query string",
        2,
        0.0,
        None,
    )
    assert len(out) == 2
