"""Hybrid (dense + sparse) retrieval fusion for SemanticMemory (Sprint C).

A pure cosine-similarity search misses queries that hinge on exact
tokens — door codes, room numbers, currency codes, reservation IDs.
A pure BM25 search misses paraphrases the bi-encoder catches.  The
industry remedy is to run both retrievers and fuse their rankings.

Sprint C ships the fusion layer.  Two pieces:

* :class:`Bm25SparseEncoder` — lazy ``fastembed`` wrapper.  Produces
  sparse ``{indices, values}`` dicts in the format Qdrant 1.10+
  consumes natively.
* :func:`reciprocal_rank_fusion` — combines two ranked candidate
  lists using Reciprocal Rank Fusion (Cormack et al., 2009).  RRF
  is a parameter-light fuser (only ``k`` to tune) and the standard
  baseline for hybrid retrieval; we can swap it for a tuned linear
  combiner later without changing call sites.

Both pieces are gated by ``BRAIN_HYBRID_RETRIEVAL_ENABLED``; when
the flag is off, callers skip hybrid altogether and keep the
existing dense-only retrieval path.  No Qdrant collection schema
change is performed implicitly — opting in requires a separate
migration to add the sparse vector named-config to existing
collections, intentionally a follow-up ticket.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and flag plumbing
# ---------------------------------------------------------------------------


_HYBRID_FLAG_ENV: Final[str] = "BRAIN_HYBRID_RETRIEVAL_ENABLED"
_BM25_MODEL_ENV: Final[str] = "BRAIN_BM25_MODEL"

# Default fastembed BM25 checkpoint.  ``Qdrant/bm25`` is the
# library's reference recipe — language-agnostic, no model weights
# (BM25 is statistical), and the canonical companion to the dense
# bi-encoder served from Qdrant.
DEFAULT_BM25_MODEL: Final[str] = "Qdrant/bm25"

# RRF "k" hyper-parameter.  Cormack et al. settled on ``k=60`` after
# a TREC sweep; this value has been the de-facto default in every
# major hybrid-retrieval library since.  Configurable per call so a
# property with very few cases (cold start) can use a smaller k.
DEFAULT_RRF_K: Final[int] = 60


def hybrid_retrieval_enabled() -> bool:
    """Whether the Sprint C hybrid retrieval path is active.

    Read on every call so a deploy can flip
    ``BRAIN_HYBRID_RETRIEVAL_ENABLED`` without restarting the API
    pod.  Default off — callers keep dense-only retrieval until the
    team explicitly opts in *and* migrates collections to carry a
    BM25 sparse-vector config.
    """
    raw = os.environ.get(_HYBRID_FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def configured_bm25_model() -> str:
    """Return the fastembed BM25 model name to use.

    Honours ``BRAIN_BM25_MODEL`` for ad-hoc benchmarking and falls
    back to :data:`DEFAULT_BM25_MODEL`.
    """
    raw = os.environ.get(_BM25_MODEL_ENV, "").strip()
    return raw or DEFAULT_BM25_MODEL


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SparseVector:
    """Qdrant-compatible sparse vector representation.

    Attributes:
        indices: Token IDs that appear in the encoded text.
        values: BM25 weights matched 1:1 with ``indices``.

    Both tuples are kept the same length and in matching order, so
    callers can hand them straight to ``models.SparseVector(...)``.
    """

    indices: tuple[int, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.indices) != len(self.values):
            raise ValueError(
                "indices and values must have the same length",
            )


# ---------------------------------------------------------------------------
# Sparse encoder
# ---------------------------------------------------------------------------


@runtime_checkable
class SparseEncoderProtocol(Protocol):
    """Minimum surface a sparse encoder must expose for fusion."""

    def encode(self, text: str) -> SparseVector:
        """Return the sparse vector for a single piece of text."""


class Bm25SparseEncoder:
    """fastembed-backed BM25 encoder, lazy-loaded.

    The fastembed import happens on first :meth:`encode` call so
    pods that have not opted into Sprint C do not pay the startup
    cost.  Tests inject a stub matching :class:`SparseEncoderProtocol`
    instead of installing fastembed weights.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or configured_bm25_model()
        self._encoder: object | None = None

    def _load(self) -> object:
        if self._encoder is not None:
            return self._encoder
        # Local import — fastembed is heavy and only needed when the
        # caller actually encodes.  ImportError surfaces the missing
        # dependency clearly instead of failing at module import time.
        from fastembed import SparseTextEmbedding

        logger.info("Loading BM25 sparse encoder: %s", self._model_name)
        self._encoder = SparseTextEmbedding(model_name=self._model_name)
        return self._encoder

    def encode(self, text: str) -> SparseVector:
        encoder = self._load()
        # fastembed exposes ``embed`` returning an iterator of
        # SparseEmbedding; one item per input string.
        embed_iter = encoder.embed([text])  # type: ignore[attr-defined]
        result = next(iter(embed_iter))
        return SparseVector(
            indices=tuple(int(i) for i in result.indices),
            values=tuple(float(v) for v in result.values),
        )


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FusedItem[T]:
    """Output of :func:`reciprocal_rank_fusion` for a single item.

    Attributes:
        item: The candidate object.  Identity is taken from the
            ``key_of`` callable, so equality of underlying objects
            is irrelevant.
        score: RRF score — the sum of ``1 / (k + rank)`` across
            every list the item appeared in.  Higher is better.
        dense_rank: 1-based rank in the dense list, or ``None`` if
            the item was missing from that list.
        sparse_rank: 1-based rank in the sparse list, or ``None``.
    """

    item: T
    score: float
    dense_rank: int | None
    sparse_rank: int | None


def reciprocal_rank_fusion[T](
    *,
    dense: Sequence[T],
    sparse: Sequence[T],
    key_of: Callable[[T], object],
    k: int = DEFAULT_RRF_K,
    top_n: int | None = None,
) -> list[FusedItem[T]]:
    """Fuse two ranked candidate lists with Reciprocal Rank Fusion.

    For each unique item, the RRF score is::

        score = sum(1 / (k + rank_i))

    over every list ``i`` the item appears in (rank is 1-based).
    Items present in both lists win over items present in only one,
    even when the singleton list ranks them first — this is the
    bias that makes RRF strong as a hybrid baseline.

    Args:
        dense: Ranked list from the bi-encoder retrieval.  Index 0
            is the top hit.
        sparse: Ranked list from the BM25 sparse retrieval.
        key_of: Extracts the identity key from a candidate.  Items
            with equal keys are treated as the same row.
        k: RRF damping constant.  Defaults to
            :data:`DEFAULT_RRF_K`.  Must be positive.
        top_n: Optional truncation.  ``None`` returns the full
            fused list.

    Returns:
        List of :class:`FusedItem` sorted by descending score, ties
        broken by ``dense_rank`` ascending (then ``sparse_rank``).

    Raises:
        ValueError: ``k`` is not a positive integer.
    """
    if k <= 0:
        raise ValueError("RRF k must be a positive integer")

    aggregated: dict[object, FusedItem[T]] = {}
    _accumulate(aggregated, dense, key_of, k, "dense")
    _accumulate(aggregated, sparse, key_of, k, "sparse")

    fused = list(aggregated.values())
    fused.sort(
        key=lambda r: (
            -r.score,
            _rank_or_inf(r.dense_rank),
            _rank_or_inf(r.sparse_rank),
        ),
    )
    if top_n is not None:
        fused = fused[:top_n]
    return fused


def _rank_or_inf(rank: int | None) -> float:
    """Sort missing ranks last when scores tie."""
    return float("inf") if rank is None else float(rank)


def _accumulate[T](
    aggregated: dict[object, FusedItem[T]],
    ranked: Iterable[T],
    key_of: Callable[[T], object],
    k: int,
    source: str,
) -> None:
    """Fold one ranked list into the running RRF aggregate."""
    for rank, item in enumerate(ranked, start=1):
        key = key_of(item)
        contribution = 1.0 / (k + rank)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = FusedItem(
                item=item,
                score=contribution,
                dense_rank=rank if source == "dense" else None,
                sparse_rank=rank if source == "sparse" else None,
            )
            continue
        # Item seen in both lists — accumulate score and record the
        # rank for the source we are folding in now.
        new_score = existing.score + contribution
        if source == "dense":
            aggregated[key] = FusedItem(
                item=existing.item,
                score=new_score,
                dense_rank=rank,
                sparse_rank=existing.sparse_rank,
            )
        else:
            aggregated[key] = FusedItem(
                item=existing.item,
                score=new_score,
                dense_rank=existing.dense_rank,
                sparse_rank=rank,
            )


__all__ = [
    "DEFAULT_BM25_MODEL",
    "DEFAULT_RRF_K",
    "Bm25SparseEncoder",
    "FusedItem",
    "SparseEncoderProtocol",
    "SparseVector",
    "configured_bm25_model",
    "hybrid_retrieval_enabled",
    "reciprocal_rank_fusion",
]
