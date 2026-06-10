"""Tests for the Sprint C hybrid retrieval fusion.

Three groups of guarantees:

* **Flag plumbing** — ``hybrid_retrieval_enabled`` and
  ``configured_bm25_model`` honour the env vars and fall back to
  the documented defaults when nothing is set.
* **SparseVector value object** — keeps indices and values aligned
  through dataclass invariants.
* **Reciprocal Rank Fusion** — items present in both lists outscore
  items present in only one (the bias that makes RRF strong); a
  smaller ``k`` widens the score gap; ``top_n`` truncates without
  reordering; missing-from-both-lists is unrepresentable.

The fastembed-backed BM25 encoder is *not* exercised here — the
weights live behind a network download and unit tests inject
``SparseEncoderProtocol`` stubs at the call sites that need them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from brain_engine.memory.hybrid_search import (
    DEFAULT_BM25_MODEL,
    DEFAULT_RRF_K,
    Bm25SparseEncoder,
    FusedItem,
    SparseEncoderProtocol,
    SparseVector,
    configured_bm25_model,
    hybrid_retrieval_enabled,
    reciprocal_rank_fusion,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Doc:
    """Tiny candidate type used in the fusion tests."""

    doc_id: str


@pytest.fixture(autouse=True)
def _reset_hybrid_env() -> Iterator[None]:
    """Strip Sprint C env vars before each test to avoid leakage."""
    snapshot = {
        key: os.environ.pop(key, None)
        for key in ("BRAIN_HYBRID_RETRIEVAL_ENABLED", "BRAIN_BM25_MODEL")
    }
    try:
        yield
    finally:
        for key in (
            "BRAIN_HYBRID_RETRIEVAL_ENABLED",
            "BRAIN_BM25_MODEL",
        ):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert hybrid_retrieval_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = raw
    assert hybrid_retrieval_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_HYBRID_RETRIEVAL_ENABLED"] = raw
    assert hybrid_retrieval_enabled() is False


def test_default_bm25_model_when_env_unset() -> None:
    assert configured_bm25_model() == DEFAULT_BM25_MODEL


def test_bm25_model_env_override() -> None:
    os.environ["BRAIN_BM25_MODEL"] = "vendor/other-bm25"
    assert configured_bm25_model() == "vendor/other-bm25"


# ---------------------------------------------------------------------------
# SparseVector
# ---------------------------------------------------------------------------


def test_sparse_vector_round_trip() -> None:
    sv = SparseVector(indices=(1, 7, 9), values=(0.4, 0.2, 0.1))
    assert sv.indices == (1, 7, 9)
    assert sv.values == (0.4, 0.2, 0.1)


def test_sparse_vector_rejects_misaligned_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        SparseVector(indices=(1, 2), values=(0.1,))


def test_sparse_vector_is_frozen() -> None:
    sv = SparseVector(indices=(1,), values=(0.5,))
    with pytest.raises(Exception):
        sv.values = (0.7,)  # type: ignore[misc]


def test_bm25_encoder_satisfies_protocol() -> None:
    """Real encoder declares the documented Sprint C surface."""
    encoder = Bm25SparseEncoder()
    assert isinstance(encoder, SparseEncoderProtocol)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def test_rrf_items_in_both_lists_outscore_singletons() -> None:
    """The defining bias of RRF — overlap wins."""
    dense = [_Doc("a"), _Doc("b"), _Doc("c")]
    sparse = [_Doc("b"), _Doc("d"), _Doc("a")]

    fused = reciprocal_rank_fusion(
        dense=dense,
        sparse=sparse,
        key_of=lambda d: d.doc_id,
    )

    by_id = {f.item.doc_id: f for f in fused}
    # b: dense rank 2 + sparse rank 1
    # a: dense rank 1 + sparse rank 3
    # Both should outscore d (sparse only) and c (dense only).
    assert by_id["b"].score > by_id["d"].score
    assert by_id["b"].score > by_id["c"].score
    assert by_id["a"].score > by_id["d"].score
    assert by_id["a"].score > by_id["c"].score


def test_rrf_records_per_source_ranks() -> None:
    dense = [_Doc("a"), _Doc("b")]
    sparse = [_Doc("b")]

    fused = reciprocal_rank_fusion(
        dense=dense,
        sparse=sparse,
        key_of=lambda d: d.doc_id,
    )

    by_id = {f.item.doc_id: f for f in fused}
    assert by_id["a"].dense_rank == 1
    assert by_id["a"].sparse_rank is None
    assert by_id["b"].dense_rank == 2
    assert by_id["b"].sparse_rank == 1


def test_rrf_empty_inputs_return_empty() -> None:
    fused = reciprocal_rank_fusion(
        dense=[],
        sparse=[],
        key_of=lambda d: d.doc_id,
    )
    assert fused == []


def test_rrf_only_dense_returns_dense_in_order() -> None:
    dense = [_Doc("a"), _Doc("b"), _Doc("c")]
    fused = reciprocal_rank_fusion(
        dense=dense,
        sparse=[],
        key_of=lambda d: d.doc_id,
    )
    assert [f.item.doc_id for f in fused] == ["a", "b", "c"]
    assert all(f.sparse_rank is None for f in fused)


def test_rrf_top_n_truncates_after_sort() -> None:
    dense = [_Doc(str(i)) for i in range(5)]
    sparse = [_Doc(str(i)) for i in range(4, -1, -1)]

    fused = reciprocal_rank_fusion(
        dense=dense,
        sparse=sparse,
        key_of=lambda d: d.doc_id,
        top_n=2,
    )

    assert len(fused) == 2


def test_rrf_smaller_k_widens_score_gap() -> None:
    """Lower k amplifies the contribution of high ranks."""
    dense = [_Doc("a"), _Doc("b"), _Doc("c"), _Doc("d"), _Doc("e")]
    sparse = [_Doc("a"), _Doc("b"), _Doc("c"), _Doc("d"), _Doc("e")]

    big_k = reciprocal_rank_fusion(
        dense=dense, sparse=sparse, key_of=lambda d: d.doc_id, k=120,
    )
    small_k = reciprocal_rank_fusion(
        dense=dense, sparse=sparse, key_of=lambda d: d.doc_id, k=10,
    )

    big_gap = big_k[0].score - big_k[-1].score
    small_gap = small_k[0].score - small_k[-1].score
    assert small_gap > big_gap


def test_rrf_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        reciprocal_rank_fusion(
            dense=[_Doc("a")],
            sparse=[],
            key_of=lambda d: d.doc_id,
            k=0,
        )


def test_default_rrf_k_anchor() -> None:
    """Anchor: the documented default stays at the literature value."""
    assert DEFAULT_RRF_K == 60


def test_fused_item_is_frozen() -> None:
    """Sort key + score must not be mutated after fusion."""
    fused = reciprocal_rank_fusion(
        dense=[_Doc("a")],
        sparse=[],
        key_of=lambda d: d.doc_id,
    )
    item: FusedItem[_Doc] = fused[0]
    with pytest.raises(Exception):
        item.score = 999.0  # type: ignore[misc]
