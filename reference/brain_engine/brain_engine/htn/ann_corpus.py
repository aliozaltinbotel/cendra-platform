"""ANN-backed retrieval index for the embedding R-WoM scorer.

Final closure for M21's deferred ANN-index TODO.  The
v0.1 :class:`EmbeddingPlanCorpus` does a linear scan over every
record — adequate for ~hundreds of records, but production
corpora may grow into the thousands.  This module ships an
exact / approximate k-nearest-neighbour wrapper built on
``sklearn.neighbors.NearestNeighbors`` (already in
requirements.txt under "AI/ML" — no new dep).

What this delivers
------------------

  * :class:`ANNEmbeddingPlanCorpus` — drop-in subclass of
    :class:`EmbeddingPlanCorpus` that rebuilds a sklearn KNN
    index on every record insertion and exposes
    :meth:`top_k(query_vec, k)`.
  * :class:`ANNEmbeddingValueEstimator` — :class:`ValueEstimator`
    implementation that consults the corpus's :meth:`top_k`
    instead of looping over every record.

Pure-Python on the call site; sklearn is lazy-imported in
:meth:`ANNEmbeddingPlanCorpus._rebuild_index` so processes that
never construct an ANN corpus pay zero startup cost.  The
default metric is ``cosine`` so the score path matches the
linear-scan version's geometry exactly.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from brain_engine.htn.embedding_rwom import (
    DEFAULT_EMBEDDING_EMPTY_SCORE,
    DEFAULT_EMBEDDING_MIN_SIMILARITY,
    EmbeddedPlanRecord,
    EmbedderProtocol,
    EmbeddingPlanCorpus,
    plan_to_text,
)
from brain_engine.htn.tree import PlanTreeNode


__all__ = [
    "ANNEmbeddingPlanCorpus",
    "ANNEmbeddingValueEstimator",
    "DEFAULT_ANN_TOP_K",
]


DEFAULT_ANN_TOP_K: Final[int] = 8


logger = structlog.get_logger(__name__)


class ANNEmbeddingPlanCorpus(EmbeddingPlanCorpus):
    """Embedding corpus that maintains a sklearn ANN index.

    Inherits :class:`EmbeddingPlanCorpus`'s ``add_text`` /
    ``add_plan`` API and adds :meth:`top_k` for fast nearest-
    neighbour lookup.  The index rebuilds on every insertion;
    for very-large corpora callers should batch inserts via
    :meth:`bulk_add` to amortise the rebuild cost.
    """

    def __init__(
        self,
        *,
        embedder: EmbedderProtocol,
        records: list[EmbeddedPlanRecord] | None = None,
    ) -> None:
        super().__init__(
            embedder=embedder,
            records=records or [],
        )
        self._index: Any | None = None
        self._index_size: int = 0
        # Build the initial index if records were preloaded.
        if records:
            self._rebuild_index()

    def add_text(self, *, text: str, reward: float) -> None:
        """Append a record and rebuild the ANN index."""
        super().add_text(text=text, reward=reward)
        self._rebuild_index()

    def bulk_add(
        self,
        *,
        items: list[tuple[str, float]],
    ) -> None:
        """Append many records, rebuilding the index only once."""
        for text, reward in items:
            super().add_text(text=text, reward=reward)
        self._rebuild_index()

    def top_k(
        self,
        *,
        query_vec: list[float],
        k: int,
    ) -> list[tuple[EmbeddedPlanRecord, float]]:
        """Return ``k`` closest records as ``(record, cosine)`` pairs.

        Returns an empty list when the corpus is empty.  ``k`` is
        capped to the corpus size so callers never have to clamp
        themselves.
        """
        if k < 1:
            raise ValueError("k must be positive")
        records = self.records()
        if not records:
            return []
        if self._index is None or self._index_size != len(records):
            self._rebuild_index()
        index = self._index
        if index is None:  # pragma: no cover — guarded above
            return []
        capped_k = min(k, len(records))
        # NearestNeighbors expects 2-D input; we wrap the query
        # vector in a single-row list.
        distances, indices = index.kneighbors(
            [query_vec], n_neighbors=capped_k,
        )
        out: list[tuple[EmbeddedPlanRecord, float]] = []
        for dist, idx in zip(distances[0], indices[0]):
            # sklearn's "cosine" metric returns 1 − cos(θ); flip
            # to cosine similarity in [-1, 1].  We further clamp
            # to ≥ 0 so anti-correlated past plans don't yank
            # the weighted-mean below zero.
            similarity = max(0.0, 1.0 - float(dist))
            out.append((records[int(idx)], similarity))
        return out

    # ── internals ─────────────────────────────────────────── #

    def _rebuild_index(self) -> None:
        records = self.records()
        if not records:
            self._index = None
            self._index_size = 0
            return
        # Lazy import — sklearn is heavy but is already in the
        # production stack via fastembed's transitive deps.
        from sklearn.neighbors import NearestNeighbors

        matrix = [list(record.embedding) for record in records]
        index = NearestNeighbors(
            n_neighbors=min(len(records), DEFAULT_ANN_TOP_K),
            metric="cosine",
            algorithm="brute",
        )
        index.fit(matrix)
        self._index = index
        self._index_size = len(records)


class ANNEmbeddingValueEstimator:
    """Embedding-similarity :class:`ValueEstimator` using ANN top-k.

    Behaviour mirrors
    :class:`brain_engine.htn.embedding_rwom.EmbeddingValueEstimator`
    but consults the corpus's :meth:`ANNEmbeddingPlanCorpus.top_k`
    instead of iterating every record.  Output is identical for
    small corpora; for large corpora the saving is O(log n) vs
    O(n).
    """

    def __init__(
        self,
        *,
        corpus: ANNEmbeddingPlanCorpus,
        embedder: EmbedderProtocol,
        top_k: int = DEFAULT_ANN_TOP_K,
        min_similarity: float = (
            DEFAULT_EMBEDDING_MIN_SIMILARITY
        ),
        empty_score: float = DEFAULT_EMBEDDING_EMPTY_SCORE,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError(
                "min_similarity must be in [0.0, 1.0]"
            )
        if not 0.0 <= empty_score <= 1.0:
            raise ValueError(
                "empty_score must be in [0.0, 1.0]"
            )
        self._corpus = corpus
        self._embedder = embedder
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._empty_score = empty_score
        self._log = logger.bind(component="ann_embedding_rwom")

    def score(self, node: PlanTreeNode) -> float:
        """Return the ANN-weighted mean reward for ``node``."""
        if not node.operators:
            return self._empty_score
        query_vec = list(
            self._embedder.encode(plan_to_text(node.operators))
        )
        neighbours = self._corpus.top_k(
            query_vec=query_vec, k=self._top_k,
        )
        if not neighbours:
            return self._empty_score
        weighted_sum = 0.0
        weight_total = 0.0
        for record, similarity in neighbours:
            if similarity < self._min_similarity:
                continue
            weighted_sum += similarity * record.reward
            weight_total += similarity
        if weight_total <= 0.0:
            return self._empty_score
        return weighted_sum / weight_total
