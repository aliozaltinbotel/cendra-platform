"""Semantic Memory - Long-term factual knowledge backed by vector search.

Wraps a Qdrant vector database for storing and retrieving domain knowledge
via semantic similarity search. Dense embeddings come from the
dedicated embedding pod through the Embedder seam (see embedder.py) —
no model weights in the api image (Batch 3 deployment decision).

Inspired by Mem0's production-ready approach to persistent AI memory:
- Automatic embedding generation
- Metadata-enriched storage
- Similarity-based retrieval with configurable thresholds
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from core.brain.memory.embedder import Embedder, RemoteEmbedder
from core.brain.memory.embedding_config import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    resolve_embedding_dim,
    resolve_embedding_model,
)
from core.brain.memory.observe import emit_memory_retrieved

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "semantic_memory"
# Re-exported for backwards compatibility with callers that imported
# the module-private constants directly. The single source of truth
# now lives in :mod:`core.brain.memory.embedding_config`.
_DEFAULT_EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL
_DEFAULT_EMBEDDING_DIM = DEFAULT_EMBEDDING_DIM


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """A single record retrieved from semantic memory.

    Attributes:
        id: Unique identifier for the record.
        text: The stored text content.
        metadata: Associated metadata (source, tags, timestamps, etc.).
        score: Similarity score from the search (higher is more relevant).
    """

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class SemanticMemory:
    """Long-term factual knowledge store backed by Qdrant vector database.

    Stores text with auto-generated embeddings and retrieves via semantic
    similarity search. Designed for domain knowledge, policies, FAQs,
    and any information the agent needs across sessions.

    Args:
        collection_name: Qdrant collection name for this memory store.
        qdrant_url: Qdrant server URL. Defaults to localhost.
        qdrant_api_key: Optional API key for Qdrant Cloud.
        embedding_model: Embedding model name served by the embedding pod.
        embedding_dim: Dimensionality of the embedding vectors.
        embedder: Injected Embedder; defaults to RemoteEmbedder from env.
        client: Injected sync Qdrant client; defaults to QdrantClient(url).
    """

    def __init__(
        self,
        collection_name: str = _DEFAULT_COLLECTION,
        qdrant_url: str = "http://localhost:6333",
        qdrant_api_key: str | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
        sparse_encoder: Any = None,
        embedder: Embedder | None = None,
        client: QdrantClient | None = None,
    ) -> None:
        # Env-driven defaults via embedding_config (Sprint A wiring).
        # Explicit kwargs still win — keeps the constructor backward
        # compatible with callers and tests that pin a specific model.
        if embedding_model is None:
            embedding_model = resolve_embedding_model()
        if embedding_dim is None:
            embedding_dim = resolve_embedding_dim()

        self.collection_name = collection_name
        self.embedding_dim = embedding_dim

        self._client = client if client is not None else QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        self._encoder = embedder if embedder is not None else RemoteEmbedder(model=embedding_model)
        self._initialized = False
        # Task 5 of CLAUDE_CODE_WIRING_FIX_PLAN.md — optional handle on
        # a Sprint C ``Bm25SparseEncoder`` (or any
        # ``SparseEncoderProtocol`` implementation).  Declared as
        # ``Any`` to keep the dependency direction one-way: the Sprint
        # C module already imports nothing from this file, and we
        # avoid forcing fastembed onto every pod that does not opt
        # into hybrid retrieval.  ``None`` keeps the pre-Task-5 path
        # bit-for-bit identical (``search`` falls through to
        # ``_dense_search`` regardless of the hybrid flag).
        self._sparse_encoder = sparse_encoder

    def _ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not already exist."""
        if self._initialized:
            return

        collections = self._client.get_collections()
        existing_names = [c.name for c in collections.collections]

        if self.collection_name not in existing_names:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.embedding_dim,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection: %s", self.collection_name)

        self._initialized = True

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text.

        Delegates to the injected :class:`Embedder` (the embedding pod);
        vectors arrive L2-normalised, matching the reference's
        ``normalize_embeddings=True`` behaviour.
        """
        return self._encoder.encode(text)

    def store(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> str:
        """Store a text entry with its embedding in the vector database.

        Args:
            text: The text content to store.
            metadata: Optional metadata dict (source, tags, category, etc.).
            record_id: Optional explicit ID. Auto-generated if not provided.

        Returns:
            The ID of the stored record.
        """
        self._ensure_collection()

        rid = record_id or str(uuid.uuid4())
        vector = self._embed(text)

        payload = {
            "text": text,
            **(metadata or {}),
        }

        self._client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=rid,
                    vector=vector,
                    payload=payload,
                ),
            ],
        )

        logger.info("Stored semantic memory id=%s: %s", rid, text[:80])
        return rid

    def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Search semantic memory by similarity to a query.

        Default path is dense bi-encoder retrieval — bit-for-bit
        identical to the pre-Task-5 behaviour.  When the Sprint C
        flag ``BRAIN_HYBRID_RETRIEVAL_ENABLED`` is truthy *and* a
        ``sparse_encoder`` was injected at construction, the search
        also runs a BM25 sparse retrieval and fuses the two ranked
        lists via Reciprocal Rank Fusion.

        Hybrid retrieval requires the Qdrant collection to carry a
        sparse-vector named-config — that migration is intentionally
        a separate ticket so this commit ships zero side effects in
        production.  Until the migration lands, ``_sparse_search``
        raises :class:`NotImplementedError` with a clear message and
        the caller sees a graceful fallback to dense-only results.

        Args:
            query: The search query text.
            top_k: Maximum number of results to return.
            score_threshold: Minimum similarity score to include.
            metadata_filter: Optional Qdrant filter conditions on
                metadata.

        Returns:
            List of MemoryRecord objects sorted by descending
            similarity (or descending fused score in hybrid mode).
        """
        self._ensure_collection()

        # Lazy import so pods that have not opted into hybrid never
        # pay the import cost of the Sprint C fusion module.
        from core.brain.memory.hybrid_search import (
            hybrid_retrieval_enabled,
            reciprocal_rank_fusion,
        )

        if hybrid_retrieval_enabled() and self._sparse_encoder is not None:
            try:
                # Wider initial pool gives RRF headroom to surface
                # candidates that one retriever ranks at the bottom
                # but the other puts near the top.
                pool_size = top_k * 2
                dense_records = self._dense_search(
                    query=query,
                    top_k=pool_size,
                    score_threshold=score_threshold,
                    metadata_filter=metadata_filter,
                )
                sparse_records = self._sparse_search(
                    query=query,
                    top_k=pool_size,
                    metadata_filter=metadata_filter,
                )
                fused = reciprocal_rank_fusion(
                    dense=dense_records,
                    sparse=sparse_records,
                    key_of=lambda r: r.id,
                    top_n=top_k,
                )
                return [
                    MemoryRecord(
                        id=item.item.id,
                        text=item.item.text,
                        metadata=item.item.metadata,
                        score=item.score,
                    )
                    for item in fused
                ]
            except Exception as exc:  # fail open — never block dense path
                logger.warning(
                    "Hybrid retrieval failed, falling back to dense-only: %s (%s)",
                    exc,
                    type(exc).__name__,
                )

        return self._dense_search(
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            metadata_filter=metadata_filter,
        )

    def _dense_search(
        self,
        *,
        query: str,
        top_k: int,
        score_threshold: float,
        metadata_filter: dict[str, Any] | None,
    ) -> list[MemoryRecord]:
        """Bi-encoder retrieval — the pre-Task-5 path verbatim.

        Extracted from the public ``search`` so the new orchestrator
        can also call it on the hybrid fallback path without
        duplicating the Qdrant query construction.
        """
        query_vector = self._embed(query)

        qdrant_filter = None
        if metadata_filter:
            conditions = [
                models.FieldCondition(
                    key=key,
                    match=models.MatchValue(value=value),
                )
                for key, value in metadata_filter.items()
            ]
            qdrant_filter = models.Filter(must=conditions)

        t0 = time.perf_counter()
        response = self._client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=qdrant_filter,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        results = response.points

        records = [
            MemoryRecord(
                id=str(hit.id),
                text=hit.payload.get("text", "") if hit.payload else "",
                metadata={k: v for k, v in (hit.payload or {}).items() if k != "text"},
                score=hit.score,
            )
            for hit in results
        ]

        emit_memory_retrieved(
            tier="semantic",
            query=query,
            hits=[{"id": r.id, "score": float(r.score), "excerpt": r.text} for r in records],
            latency_ms=latency_ms,
        )

        logger.debug(
            "Semantic search for '%s' returned %d results",
            query[:60],
            len(records),
        )
        return records

    def _sparse_search(
        self,
        *,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None,
    ) -> list[MemoryRecord]:
        """BM25 sparse retrieval through Qdrant's named-vector API.

        Requires the collection to have been migrated to carry a
        sparse vector named-config (Qdrant 1.10+ feature).  Until
        that migration ticket lands the method raises a clear
        :class:`NotImplementedError`; ``search`` catches it and
        falls back to dense-only retrieval, preserving the
        pre-Task-5 behaviour.
        """
        raise NotImplementedError(
            "Sparse search requires a Qdrant collection migration "
            "to add the BM25 sparse-vector named-config (Qdrant "
            "1.10+).  Track the migration ticket before flipping "
            "BRAIN_HYBRID_RETRIEVAL_ENABLED in production.",
        )

    def delete(self, record_id: str) -> None:
        """Delete a record from semantic memory by ID.

        Args:
            record_id: The ID of the record to delete.
        """
        self._ensure_collection()

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(
                points=[record_id],
            ),
        )
        logger.info("Deleted semantic memory id=%s", record_id)

    def count(self) -> int:
        """Return the total number of records in the collection."""
        self._ensure_collection()
        info = self._client.get_collection(self.collection_name)
        return info.points_count or 0

    def close(self) -> None:
        """Close the Qdrant client connection."""
        self._client.close()
        logger.info("Semantic memory connection closed.")
