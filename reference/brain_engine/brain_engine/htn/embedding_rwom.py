"""Embedding-similarity R-WoM value estimator (M21).

Strengthens M16's :class:`brain_engine.htn.RetrievalValueEstimator`
by replacing the cosine-over-bag-of-operator-names with cosine
over learned embedding vectors of the natural-language plan
description.

Why bother:
  * The bag-of-names version requires an *exact* operator-name
    match.  Two operators that *do the same thing* but ship
    under different names contribute zero similarity to each
    other.
  * Embeddings give *semantic* similarity — ``send_message``
    and ``deliver_notification`` can land near each other in the
    embedding space.  The estimator returns a smoother, more
    transferable reward signal.

Public surface:
  * :class:`EmbedderProtocol` — encode(text) → vector.
  * :class:`StubEmbedder` — deterministic hash-based test double.
  * :class:`FastembedAdapter` — lazy-imports the project's
    existing :mod:`fastembed` dep (already in requirements).
  * :class:`EmbeddedPlanRecord` — frozen dataclass: stored
    embedding vector + realised reward.
  * :class:`EmbeddingPlanCorpus` — store of embedded records.
  * :class:`EmbeddingValueEstimator` — implements M12's
    :class:`ValueEstimator` Protocol; cosines the query plan's
    embedding against every corpus embedding and returns the
    similarity-weighted mean reward.
  * :func:`plan_to_text` / :func:`cosine_vec` — pure helpers.

Honest scope:
  * Pure-Python except for the optional ``fastembed`` dep
    (already in :file:`requirements.txt`, lazy-imported only
    when :class:`FastembedAdapter` is constructed).
  * Linear scan over the corpus — adequate for ~hundreds of
    records.  ANN index is a v1.0 follow-up.
  * No fine-tuning of the embedder; the estimator just consumes
    whatever vectors the adapter emits.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

import structlog

from brain_engine.htn.models import HTNPlan, Operator
from brain_engine.htn.tree import PlanTreeNode


__all__ = [
    "DEFAULT_EMBEDDER_DIM",
    "DEFAULT_EMBEDDING_EMPTY_SCORE",
    "DEFAULT_EMBEDDING_MIN_SIMILARITY",
    "EmbeddedPlanRecord",
    "EmbedderProtocol",
    "EmbeddingPlanCorpus",
    "EmbeddingValueEstimator",
    "FastembedAdapter",
    "StubEmbedder",
    "cosine_vec",
    "plan_to_text",
]


DEFAULT_EMBEDDER_DIM: Final[int] = 64
DEFAULT_EMBEDDING_EMPTY_SCORE: Final[float] = 0.0
DEFAULT_EMBEDDING_MIN_SIMILARITY: Final[float] = 0.0


logger = structlog.get_logger(__name__)


class EmbedderProtocol(Protocol):
    """Encode a string into a fixed-length embedding vector."""

    def encode(self, text: str) -> Sequence[float]:
        """Return the embedding for ``text`` as a flat sequence."""
        ...

    @property
    def dim(self) -> int:
        """The embedding dimensionality this encoder produces."""
        ...


def plan_to_text(operators: Sequence[Operator | str]) -> str:
    """Render a plan operator sequence as a single-line description.

    Format: ``"step 1: {name}; step 2: {name}; ..."``.  Stable +
    deterministic so identical plans hash to identical strings,
    which is the only contract embedders need.
    """
    parts: list[str] = []
    for index, item in enumerate(operators, start=1):
        if isinstance(item, Operator):
            name = item.name
        elif isinstance(item, str):
            name = item
        else:
            raise TypeError(
                f"unsupported operator entry: {type(item).__name__}"
            )
        parts.append(f"step {index}: {name}")
    return "; ".join(parts) if parts else "empty plan"


def cosine_vec(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length float sequences."""
    if len(a) != len(b):
        raise ValueError(
            f"vector length mismatch: {len(a)} vs {len(b)}"
        )
    if not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for va, vb in zip(a, b):
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom == 0.0:
        return 0.0
    return dot / denom


class StubEmbedder:
    """Deterministic hash-based :class:`EmbedderProtocol` for tests.

    Stable hash → fixed-length float vector in ``[-1, 1]``.  Two
    identical input strings always produce the same vector;
    different strings produce uncorrelated vectors with high
    probability.  Not a *real* semantic encoder — but adequate
    for unit tests that just need an opaque embedder.
    """

    def __init__(self, *, dim: int = DEFAULT_EMBEDDER_DIM) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, text: str) -> list[float]:
        # SHAKE128 is variable-length (XOF) so the embedder can
        # produce arbitrary ``dim`` values without BLAKE2B's
        # 64-byte cap.
        digest = hashlib.shake_128(
            text.encode("utf-8"),
        ).digest(self._dim * 4)
        # Unpack 4-byte chunks as signed int32 → normalise to
        # [-1, 1] by dividing by 2**31.
        ints = struct.unpack(f"<{self._dim}i", digest)
        return [value / (2**31) for value in ints]


class FastembedAdapter:
    """Lazy-imported wrapper around :mod:`fastembed`.

    The ``fastembed`` dep is *already* in requirements.txt under
    "AI/ML" (sparse encoder for hybrid retrieval) and is loaded
    on demand — we follow the same pattern: the import lives
    inside the constructor, so processes that never construct
    the adapter pay zero startup cost.
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
    ) -> None:
        from fastembed import TextEmbedding

        self._encoder = TextEmbedding(model_name=model_name)
        self._dim_cache: int | None = None

    @property
    def dim(self) -> int:
        if self._dim_cache is None:
            sample = self.encode("dimension probe")
            self._dim_cache = len(sample)
        return self._dim_cache

    def encode(self, text: str) -> list[float]:
        # ``embed`` is a generator — pull the first row.
        embedding = next(iter(self._encoder.embed([text])))
        # ``fastembed`` returns a NumPy array; we stay pure-
        # Python on the consumer side.
        return [float(value) for value in embedding]


@dataclass(frozen=True, slots=True)
class EmbeddedPlanRecord:
    """One past plan stored as ``(embedding, reward)``.

    Attributes:
        embedding: Tuple of floats — the encoder's output.  We
            convert :class:`Sequence` to a tuple at construction
            so the record is fully hashable / immutable.
        reward: Realised reward in ``[0.0, 1.0]``.
    """

    embedding: tuple[float, ...]
    reward: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.reward <= 1.0:
            raise ValueError(
                "reward must be in [0.0, 1.0]"
            )
        if not self.embedding:
            raise ValueError("embedding must be non-empty")


class EmbeddingPlanCorpus:
    """In-memory store of :class:`EmbeddedPlanRecord` rows.

    Construction takes an :class:`EmbedderProtocol`; the corpus
    embeds plan text on insert so retrieval is a flat cosine
    sweep.
    """

    def __init__(
        self,
        *,
        embedder: EmbedderProtocol,
        records: Iterable[EmbeddedPlanRecord] = (),
    ) -> None:
        self._embedder = embedder
        self._records: list[EmbeddedPlanRecord] = list(records)

    def add_text(self, *, text: str, reward: float) -> None:
        """Embed ``text`` and append the resulting record."""
        embedding = self._embedder.encode(text)
        self._records.append(
            EmbeddedPlanRecord(
                embedding=tuple(embedding),
                reward=reward,
            )
        )

    def add_plan(
        self,
        plan: HTNPlan,
        *,
        reward: float,
    ) -> None:
        """Render ``plan`` as text, embed, store."""
        self.add_text(
            text=plan_to_text(plan.operators),
            reward=reward,
        )

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[EmbeddedPlanRecord, ...]:
        """Snapshot tuple of every stored record."""
        return tuple(self._records)


class EmbeddingValueEstimator:
    """:class:`ValueEstimator` driven by embedding cosine similarity."""

    def __init__(
        self,
        *,
        corpus: EmbeddingPlanCorpus,
        embedder: EmbedderProtocol,
        min_similarity: float = (
            DEFAULT_EMBEDDING_MIN_SIMILARITY
        ),
        empty_score: float = DEFAULT_EMBEDDING_EMPTY_SCORE,
    ) -> None:
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
        self._min_similarity = min_similarity
        self._empty_score = empty_score
        self._log = logger.bind(component="embedding_rwom")

    def score(self, node: PlanTreeNode) -> float:
        """Return the similarity-weighted mean reward for ``node``.

        When the corpus is empty, the node's plan is empty, or no
        record clears the similarity floor, returns
        ``empty_score``.  Cosine values are projected to
        ``[0, 1]`` via ``max(0, sim)`` so anti-correlated past
        plans do not yank the score below the empty-score
        baseline.
        """
        records = self._corpus.records()
        if not records:
            return self._empty_score
        if not node.operators:
            return self._empty_score
        query_text = plan_to_text(node.operators)
        query_vec = list(self._embedder.encode(query_text))
        weighted_sum = 0.0
        weight_total = 0.0
        for record in records:
            sim = max(
                0.0,
                cosine_vec(query_vec, list(record.embedding)),
            )
            if sim < self._min_similarity:
                continue
            weighted_sum += sim * record.reward
            weight_total += sim
        if weight_total <= 0.0:
            return self._empty_score
        return weighted_sum / weight_total
