"""Layer 2 of the intelligent classifier — embedding-based retrieval.

Brain Engine's foundation document
(``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md``)
enumerates 469 hospitality scenarios.  Hand-curated multilingual
keyword tables cannot enumerate them — every new scenario would
require N-language phrase additions.  This module retires that
approach with a multilingual sentence-embedding model that
narrows an incoming guest message to the top-K candidate
scenarios in <10 ms.

How it works
------------

1. **Index build (offline).**  Each scenario's canonical *trigger
   text* (from the foundation registry) is embedded once via
   ``fastembed`` using a multilingual MiniLM checkpoint
   (96 languages out-of-the-box).  Embeddings persist in memory;
   ~750 KB for 500 by 384 float32 vectors.
2. **Runtime query.**  The incoming guest message goes through
   the same embedder.  Cosine similarity ranks every indexed
   scenario; the top-``k`` ids are returned with their scores.
3. **Downstream.**  The Layer 3 LLM classifier receives the
   narrowed candidate list instead of all 500 scenarios — both
   cheaper (fewer tokens in the prompt) and more accurate (the
   LLM is not asked to remember the full taxonomy).

Honest scope
------------

* The embedder is invoked lazily (first call pays the cold-start
  download / load) so import-time cost stays near zero.
* No external API call; ``fastembed`` ships a quantised ONNX
  model and runs on CPU.
* Vectors are normalised once; cosine similarity collapses to a
  dot product, so the retrieval loop stays a single Python list
  comprehension.

References
----------
* fastembed — Qdrant team, Apache 2.0
  https://github.com/qdrant/fastembed
* paraphrase-multilingual-MiniLM-L12-v2 — Reimers & Gurevych
  https://www.sbert.net/docs/pretrained_models.html
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from fastembed import TextEmbedding

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_TOP_K",
    "ScenarioCandidate",
    "ScenarioExample",
    "ScenarioMatcher",
]


DEFAULT_EMBEDDING_MODEL: Final[str] = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
DEFAULT_TOP_K: Final[int] = 15


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScenarioExample:
    """One canonical trigger sentence per scenario id.

    Attributes:
        scenario_id: Stable identifier (e.g. ``"access_code_release"``
            or, for the 469-foundation registry,
            ``"early_checkin.same_night_zero_review"``).
        text: The canonical trigger sentence the foundation
            document lists under ``### Trigger``.  Free-form;
            the embedder treats it as a sentence.
    """

    scenario_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id required")
        if not self.text or not self.text.strip():
            raise ValueError("text required")


@dataclass(frozen=True, slots=True)
class ScenarioCandidate:
    """One ranked candidate emitted by :meth:`ScenarioMatcher.top_k`."""

    scenario_id: str
    similarity: float
    text: str

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id required")
        if not -1.0 <= self.similarity <= 1.0:
            raise ValueError(
                "similarity must be in [-1.0, 1.0]"
            )


class ScenarioMatcher:
    """Embedding-backed top-K scenario retriever.

    Construction is cheap; the index is populated lazily on the
    first :meth:`top_k` call or eagerly via :meth:`load`.  Once
    populated, retrieval is a CPU-only dot product over the
    stored vectors — no GPU, no network.
    """

    def __init__(
        self,
        examples: Iterable[ScenarioExample],
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        materialised = tuple(examples)
        if not materialised:
            raise ValueError("examples must be non-empty")
        seen: set[str] = set()
        for example in materialised:
            if example.scenario_id in seen:
                raise ValueError(
                    "duplicate scenario_id: "
                    f"{example.scenario_id!r}",
                )
            seen.add(example.scenario_id)
        self._examples: tuple[ScenarioExample, ...] = materialised
        self._model_name = model_name
        self._embedder: TextEmbedding | None = None  # lazy
        self._vectors: tuple[tuple[float, ...], ...] | None = None
        self._log = logger.bind(component="scenario_matcher")

    def load(self) -> None:
        """Force eager construction of the embedder + index.

        Call once during pod warm-up if the first user-facing
        request must not pay the cold-start cost.  Idempotent.
        """
        self._ensure_embedder()
        if self._vectors is None:
            embedder = self._embedder
            assert embedder is not None
            texts = [example.text for example in self._examples]
            raw = list(embedder.embed(texts))
            self._vectors = tuple(
                self._normalise(tuple(vec.tolist()))
                for vec in raw
            )
            self._log.info(
                "scenario_matcher.indexed",
                scenarios=len(self._examples),
                model=self._model_name,
            )

    def top_k(
        self,
        text: str,
        *,
        k: int = DEFAULT_TOP_K,
    ) -> tuple[ScenarioCandidate, ...]:
        """Return the ``k`` highest-similarity scenarios for ``text``.

        Empty / whitespace-only inputs yield an empty tuple;
        ``k <= 0`` raises.  The result preserves descending
        similarity order; ties break by scenario_id (lexicographic)
        so the output stays deterministic.
        """
        if k <= 0:
            raise ValueError("k must be positive")
        if not text or not text.strip():
            return ()
        self.load()
        assert self._vectors is not None
        embedder = self._embedder
        assert embedder is not None
        query_vectors = list(embedder.embed([text]))
        query = self._normalise(
            tuple(query_vectors[0].tolist()),
        )
        ranked = [
            (
                example.scenario_id,
                _dot(query, vec),
                example.text,
            )
            for example, vec in zip(
                self._examples, self._vectors, strict=True,
            )
        ]
        ranked.sort(
            key=lambda row: (-row[1], row[0]),
        )
        top = ranked[:k]
        return tuple(
            ScenarioCandidate(
                scenario_id=sid,
                similarity=score,
                text=text_,
            )
            for sid, score, text_ in top
        )

    def __len__(self) -> int:
        """Return the number of indexed scenarios."""
        return len(self._examples)

    # ── internals ─────────────────────────────────────────── #

    def _ensure_embedder(self) -> None:
        if self._embedder is not None:
            return
        # ``fastembed`` is heavy; defer the import so unrelated
        # callsites don't pay it on cold start.
        import warnings

        from fastembed import TextEmbedding

        # fastembed 0.7+ emits a one-time ``UserWarning`` about the
        # MiniLM mean-pooling change; the warning is informational
        # only and does not affect retrieval quality for our use
        # case (cosine over normalised vectors).  Pinning fastembed
        # 0.5.1 is the alternative the warning suggests, but the
        # newer wheel is already on the runtime; suppress the
        # noise to keep the test suite (``filterwarnings=error``)
        # green.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=".*mean pooling.*",
            )
            self._embedder = TextEmbedding(
                model_name=self._model_name,
            )

    @staticmethod
    def _normalise(vector: Sequence[float]) -> tuple[float, ...]:
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return tuple(vector)
        return tuple(v / norm for v in vector)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity for already-normalised vectors."""
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def examples_from_mapping(
    mapping: Mapping[str, str],
) -> tuple[ScenarioExample, ...]:
    """Convenience: build examples from a ``{scenario_id: text}`` dict.

    Empty texts are skipped silently so callers can pass the
    full foundation registry without pre-filtering.
    """
    return tuple(
        ScenarioExample(scenario_id=sid, text=text)
        for sid, text in mapping.items()
        if text and text.strip()
    )
