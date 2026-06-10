"""Cross-encoder reranker layer for semantic retrieval (Sprint A).

A bi-encoder (the embedding model used by ``SemanticMemory``) is fast
but coarse: it scores ``query`` and ``candidate`` independently and
trusts cosine similarity to capture relevance.  A cross-encoder
reads both pieces in the same forward pass and is markedly more
accurate at the cost of being too slow to score the full corpus.

The standard pattern is two-stage retrieval:

1. Bi-encoder fetches the top-K (typically K=20) by cosine similarity.
2. Cross-encoder rescores those K candidates and returns the top-N
   (typically N=5) actually surfaced to the agent.

This module provides the second stage as an optional, env-flagged
component.  When :func:`reranker_enabled` is false (the default), the
reranker is a documented no-op and existing retrieval flows behave
exactly as before — Sprint A is purely additive until the team flips
the ``BRAIN_RERANKER_ENABLED`` flag.

Default model: ``BAAI/bge-reranker-v2-m3`` (multilingual, MIT license,
~568 MB on disk).  The model is lazy-loaded on first use so the
import cost stays out of pod startup unless the flag is on, and
overridable through ``BRAIN_RERANKER_MODEL`` for benchmarking.

The cross-encoder is injected through a ``CrossEncoderProtocol`` so
unit tests can stub the scorer without downloading a 568 MB model.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Generic, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_RERANKER_FLAG_ENV: Final[str] = "BRAIN_RERANKER_ENABLED"
_RERANKER_MODEL_ENV: Final[str] = "BRAIN_RERANKER_MODEL"

# Sprint A default model.  bge-reranker-v2-m3 is multilingual (the
# Botel guest base spans EN, TR, DE, FR, RU and more), MIT-licensed,
# and the SOTA non-API reranker on MTEB-style benchmarks at 568 MB.
DEFAULT_RERANKER_MODEL: Final[str] = "BAAI/bge-reranker-v2-m3"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class CrossEncoderProtocol(Protocol):
    """Minimum surface a reranker scorer must expose.

    Mirrors the signature of
    ``sentence_transformers.CrossEncoder.predict`` so a real model
    drops in unchanged, but lets tests stub the scorer with a tiny
    callable that returns deterministic floats.
    """

    def predict(
        self,
        sentences: list[tuple[str, str]],
    ) -> list[float]:
        """Return one relevance score per ``(query, candidate)`` pair."""


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RerankedItem(Generic[T]):
    """One candidate paired with its cross-encoder score.

    Attributes:
        item: The original candidate object — opaque to the reranker.
        text: The text shown to the cross-encoder for scoring.
        score: Relevance score returned by the cross-encoder.  Higher
            means more relevant; absolute scale depends on the model.
    """

    item: T
    text: str
    score: float


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def reranker_enabled() -> bool:
    """Whether the Sprint A reranker layer is active.

    Read on every call so a deploy can flip
    ``BRAIN_RERANKER_ENABLED`` without restarting the API pod.
    Default off — the reranker is bypassed and callers get the
    bi-encoder ordering they had pre-Sprint-A.
    """
    raw = os.environ.get(_RERANKER_FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def configured_model_name() -> str:
    """Return the model name the reranker should load.

    Honours ``BRAIN_RERANKER_MODEL`` for ad-hoc benchmarking and
    falls back to :data:`DEFAULT_RERANKER_MODEL` otherwise.
    """
    raw = os.environ.get(_RERANKER_MODEL_ENV, "").strip()
    return raw or DEFAULT_RERANKER_MODEL


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


class CrossEncoderReranker(Generic[T]):
    """Two-stage retrieval reranker.

    Args:
        scorer: A :class:`CrossEncoderProtocol` instance.  In
            production this is a ``sentence_transformers.CrossEncoder``
            wrapping :data:`DEFAULT_RERANKER_MODEL`.  Tests pass a
            lightweight stub.

    The reranker is intentionally agnostic about the candidate type
    ``T``: callers extract the text used for scoring through a
    ``text_of`` callable when invoking :meth:`rerank`, so every memory
    tier (semantic, episodic, knowledge graph) can reuse this class.
    """

    def __init__(self, scorer: CrossEncoderProtocol) -> None:
        self._scorer = scorer

    def rerank(
        self,
        *,
        query: str,
        candidates: list[T],
        text_of: Callable[[T], str],
        top_n: int | None = None,
    ) -> list[RerankedItem[T]]:
        """Rescore ``candidates`` against ``query`` and return top-N.

        Args:
            query: The original search query.
            candidates: First-stage results from the bi-encoder, in
                arbitrary order.  Empty list returns empty list.
            text_of: Extracts the scoring text from a candidate.
            top_n: Maximum number of items to return.  ``None``
                returns every candidate, sorted by score descending.

        Returns:
            :class:`RerankedItem` list sorted by descending score,
            truncated to ``top_n``.
        """
        if not candidates:
            return []

        pairs = [(query, text_of(c)) for c in candidates]
        scores = self._scorer.predict(pairs)

        if len(scores) != len(candidates):
            raise RuntimeError(
                "scorer returned "
                f"{len(scores)} scores for {len(candidates)} "
                "candidates",
            )

        scored = [
            RerankedItem(item=cand, text=text, score=float(score))
            for cand, (_, text), score in zip(
                candidates, pairs, scores, strict=True,
            )
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        if top_n is not None:
            scored = scored[:top_n]
        return scored


def build_default_reranker() -> CrossEncoderReranker[object] | None:
    """Construct a reranker with the production cross-encoder.

    Returns ``None`` when :func:`reranker_enabled` is false so
    callers can keep the no-op cheap (no model download, no GPU
    memory) without branching on the flag at every call site.

    The lazy import keeps ``sentence_transformers.CrossEncoder``
    out of the cold-start path of pods that do not opt into Sprint A.
    """
    if not reranker_enabled():
        return None
    # Local import — sentence_transformers is heavy and only needed
    # when the flag is on.  Failure to import (e.g. older library
    # version on dev laptop) surfaces as a clear ImportError.
    from sentence_transformers import CrossEncoder  # noqa: PLC0415

    model_name = configured_model_name()
    logger.info("Loading cross-encoder reranker: %s", model_name)
    scorer = CrossEncoder(model_name)
    return CrossEncoderReranker(scorer=scorer)


__all__ = [
    "DEFAULT_RERANKER_MODEL",
    "CrossEncoderProtocol",
    "CrossEncoderReranker",
    "RerankedItem",
    "build_default_reranker",
    "configured_model_name",
    "reranker_enabled",
]
