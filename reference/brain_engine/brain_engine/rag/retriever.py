"""Retrievers — document retrieval strategies for RAG.

Provides base retriever protocol and implementations:
SimpleRetriever (keyword-based) and EnsembleRetriever
(combine multiple retrievers with weighted scoring).

Based on: LangChain BaseRetriever / EnsembleRetriever.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from brain_engine.rag.loaders import Document
from brain_engine.streaming.current_emitter import (
    reset_current_emitter,
    set_current_emitter,
)
from brain_engine.streaming.emit_helpers import emit_rag_hit

logger = logging.getLogger(__name__)


def _result_to_doc_dict(r: "RetrievalResult") -> dict[str, Any]:
    """Render a RetrievalResult as a doc dict for emit_rag_hit."""
    return {
        "id": getattr(r.document, "doc_id", "") or "",
        "title": str(r.document.metadata.get("title", "")) if r.document.metadata else "",
        "score": float(r.score),
        "excerpt": getattr(r.document, "page_content", "") or "",
    }


@dataclass
class RetrievalResult:
    """A retrieved document with relevance score.

    Attributes:
        document: The retrieved document.
        score: Relevance score (0.0 to 1.0).
        retriever_name: Which retriever found this.
    """

    document: Document
    score: float = 0.0
    retriever_name: str = ""


class BaseRetriever(ABC):
    """Abstract base for document retrievers."""

    @property
    def name(self) -> str:
        """Retriever identifier."""
        return self.__class__.__name__

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Retrieve relevant documents for a query.

        Args:
            query: Search query text.
            top_k: Maximum results to return.

        Returns:
            Ranked list of RetrievalResult.
        """
        ...

    async def add_documents(
        self,
        documents: list[Document],
    ) -> int:
        """Add documents to the retriever's index.

        Args:
            documents: Documents to index.

        Returns:
            Number of documents added.
        """
        return 0


class SimpleRetriever(BaseRetriever):
    """Keyword-based retriever using TF-IDF-like scoring.

    Indexes documents in memory and scores them by keyword
    overlap with the query. Fast, no external dependencies.

    Args:
        documents: Initial documents to index.
    """

    def __init__(
        self,
        documents: list[Document] | None = None,
    ) -> None:
        self._documents: list[Document] = list(documents or [])

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Retrieve documents by keyword scoring.

        Args:
            query: Search query.
            top_k: Max results.

        Returns:
            Scored results sorted by relevance.
        """
        t0 = time.perf_counter()
        query_terms = _tokenize(query)
        if not query_terms:
            emit_rag_hit(
                query=query,
                source="simple_retriever",
                docs=[],
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
            return []

        scored = [
            RetrievalResult(
                document=doc,
                score=_keyword_score(doc.page_content, query_terms),
                retriever_name=self.name,
            )
            for doc in self._documents
        ]

        scored.sort(key=lambda r: r.score, reverse=True)
        results = [r for r in scored[:top_k] if r.score > 0]
        emit_rag_hit(
            query=query,
            source="simple_retriever",
            docs=[_result_to_doc_dict(r) for r in results],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return results

    async def add_documents(
        self,
        documents: list[Document],
    ) -> int:
        """Add documents to the index.

        Args:
            documents: Documents to add.

        Returns:
            Number added.
        """
        self._documents.extend(documents)
        return len(documents)

    @property
    def document_count(self) -> int:
        """Number of indexed documents."""
        return len(self._documents)


class EnsembleRetriever(BaseRetriever):
    """Combine multiple retrievers with weighted scoring.

    Merges results from multiple retrievers using reciprocal
    rank fusion (RRF) or weighted score averaging.

    Args:
        retrievers: List of retrievers to combine.
        weights: Weight per retriever (default: equal).
        strategy: Merge strategy (``"rrf"`` or ``"weighted"``).
    """

    def __init__(
        self,
        retrievers: list[BaseRetriever],
        weights: list[float] | None = None,
        strategy: str = "rrf",
    ) -> None:
        self._retrievers = retrievers
        self._weights = weights or [1.0] * len(retrievers)
        self._strategy = strategy

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Retrieve and merge results from all retrievers.

        Args:
            query: Search query.
            top_k: Max combined results.

        Returns:
            Merged results sorted by combined score.
        """
        t0 = time.perf_counter()
        all_results = await self._gather_results(query, top_k)

        if self._strategy == "rrf":
            merged = _reciprocal_rank_fusion(
                all_results, self._weights,
            )
        else:
            merged = _weighted_average(
                all_results, self._weights,
            )

        merged.sort(key=lambda r: r.score, reverse=True)
        results = merged[:top_k]
        emit_rag_hit(
            query=query,
            source="ensemble_retriever",
            docs=[_result_to_doc_dict(r) for r in results],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return results

    async def _gather_results(
        self,
        query: str,
        top_k: int,
    ) -> list[list[RetrievalResult]]:
        """Gather results from all retrievers.

        Sub-retriever emissions are suppressed so the ensemble emits a
        single RAG_HIT event rather than N+1 per query.

        Args:
            query: Search query.
            top_k: Per-retriever limit.

        Returns:
            List of result lists.
        """
        token = set_current_emitter(None)  # type: ignore[arg-type]
        try:
            results: list[list[RetrievalResult]] = []
            for retriever in self._retrievers:
                r = await retriever.retrieve(query, top_k=top_k)
                results.append(r)
            return results
        finally:
            reset_current_emitter(token)

    async def add_documents(
        self,
        documents: list[Document],
    ) -> int:
        """Add documents to all sub-retrievers.

        Args:
            documents: Documents to add.

        Returns:
            Total documents added across all retrievers.
        """
        total = 0
        for retriever in self._retrievers:
            total += await retriever.add_documents(documents)
        return total


# ── Scoring helpers ──────────────────────────────────────────────────── #


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase terms.

    Args:
        text: Input text.

    Returns:
        Set of unique lowercase tokens.
    """
    return set(re.findall(r"\w+", text.lower()))


def _keyword_score(
    content: str,
    query_terms: set[str],
) -> float:
    """Score document by keyword overlap with query.

    Uses Jaccard-like coefficient: shared terms / query terms.

    Args:
        content: Document text.
        query_terms: Query tokens.

    Returns:
        Score between 0.0 and 1.0.
    """
    doc_terms = _tokenize(content)
    overlap = query_terms & doc_terms
    if not query_terms:
        return 0.0
    return len(overlap) / len(query_terms)


def _reciprocal_rank_fusion(
    result_lists: list[list[RetrievalResult]],
    weights: list[float],
    k: int = 60,
) -> list[RetrievalResult]:
    """Merge results using Reciprocal Rank Fusion.

    RRF score = sum(weight / (k + rank)) across retrievers.

    Args:
        result_lists: Results from each retriever.
        weights: Per-retriever weights.
        k: RRF constant (default 60).

    Returns:
        Deduplicated merged results.
    """
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}

    for results, weight in zip(result_lists, weights):
        for rank, result in enumerate(results):
            doc_key = _doc_key(result.document)
            rrf_score = weight / (k + rank + 1)
            scores[doc_key] = scores.get(doc_key, 0) + rrf_score
            docs[doc_key] = result.document

    return [
        RetrievalResult(
            document=docs[key],
            score=score,
            retriever_name="ensemble",
        )
        for key, score in scores.items()
    ]


def _weighted_average(
    result_lists: list[list[RetrievalResult]],
    weights: list[float],
) -> list[RetrievalResult]:
    """Merge results using weighted score average.

    Args:
        result_lists: Results from each retriever.
        weights: Per-retriever weights.

    Returns:
        Merged results.
    """
    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    docs: dict[str, Document] = {}

    for results, weight in zip(result_lists, weights):
        for result in results:
            doc_key = _doc_key(result.document)
            scores[doc_key] = (
                scores.get(doc_key, 0) + result.score * weight
            )
            counts[doc_key] = counts.get(doc_key, 0) + 1
            docs[doc_key] = result.document

    return [
        RetrievalResult(
            document=docs[key],
            score=scores[key] / max(counts[key], 1),
            retriever_name="ensemble",
        )
        for key in scores
    ]


def _doc_key(doc: Document) -> str:
    """Generate a dedup key for a document.

    Args:
        doc: Document to key.

    Returns:
        String key based on content hash.
    """
    return str(hash(doc.page_content[:200]))
