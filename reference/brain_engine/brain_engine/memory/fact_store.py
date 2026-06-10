"""FactStore — persistent storage for established facts with deduplication.

Maintains a dedicated Qdrant collection ("established_facts") that stores
structured facts extracted from guest conversations via Mem0.  New facts
are checked against existing ones using cosine similarity — if a match
exceeds the dedup threshold (default 0.92), the fact is treated as a
duplicate and skipped.

This is the long-term fact memory that feeds into the ContextAssembler's
[ESTABLISHED FACTS] section.  Unlike episodic memory (which decays),
facts here persist until explicitly contradicted or removed.

Usage:
    store = FactStore(qdrant_url="http://qdrant:6333")
    result = await store.store_facts(facts, property_id="PROP001")
    matched = await store.search("allergic to cats", property_id="PROP001")
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)

_COLLECTION_NAME: Final[str] = "established_facts"
_DEDUP_THRESHOLD: Final[float] = 0.92
_DEFAULT_TOP_K: Final[int] = 10
_EMBEDDING_DIM: Final[int] = 1536  # text-embedding-3-small


@dataclass(frozen=True, slots=True)
class StoredFact:
    """A fact persisted in the FactStore.

    Attributes:
        fact_id: Unique identifier (from ExtractedFact or generated).
        content: The fact text.
        fact_type: Category — preference, rule, info, incident.
        property_id: Which property this fact belongs to.
        entity_id: Guest/booking/contact this fact is about.
        confidence: Extraction confidence (0.0 — 1.0).
        source: Where this fact was extracted from (episode_id, etc.).
        created_at: ISO timestamp when stored.
        metadata: Arbitrary extra data.
    """

    fact_id: str
    content: str
    fact_type: str = "info"
    property_id: str = ""
    entity_id: str = ""
    confidence: float = 1.0
    source: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Result of a batch store_facts() operation.

    Attributes:
        added: Facts that were new and stored.
        duplicates: Facts that matched existing entries (skipped).
        errors: Facts that failed to store.
        total: Total facts processed.
    """

    added: int = 0
    duplicates: int = 0
    errors: int = 0
    total: int = 0


class FactStore:
    """Persistent vector-backed store for established facts.

    Wraps Qdrant for vector similarity search and deduplication.
    Falls back gracefully if Qdrant is unavailable — returns empty
    results instead of raising.

    Args:
        qdrant_url: Qdrant server URL.
        collection_name: Qdrant collection name.
        dedup_threshold: Cosine similarity threshold for deduplication.
            Facts above this score are treated as duplicates.
        embedding_func: Async function that converts text to a vector.
            If None, a stub is used (for testing / deferred init).
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        collection_name: str = _COLLECTION_NAME,
        dedup_threshold: float = _DEDUP_THRESHOLD,
        embedding_func: Any = None,
    ) -> None:
        self._qdrant_url = qdrant_url
        self._collection = collection_name
        self._dedup_threshold = dedup_threshold
        self._embed = embedding_func
        self._client: Any = None

    # ── Public API ───────────────────────────────────────────── #

    async def store_facts(
        self,
        facts: Sequence[StoredFact],
        property_id: str = "",
    ) -> StoreResult:
        """Store facts with deduplication.

        For each fact:
          1. Embed the fact content.
          2. Search existing facts for near-duplicates (similarity > threshold).
          3. If duplicate found — skip.
          4. Otherwise — upsert into Qdrant.

        Args:
            facts: Facts to store.
            property_id: Scope dedup search to this property.

        Returns:
            StoreResult with counts of added / duplicates / errors.
        """
        if not facts:
            return StoreResult()

        added = 0
        duplicates = 0
        errors = 0

        for fact in facts:
            try:
                is_dup = await self._is_duplicate(
                    fact.content,
                    property_id or fact.property_id,
                )
                if is_dup:
                    duplicates += 1
                    continue

                await self._upsert(fact)
                added += 1
            except Exception:
                logger.warning(
                    "Failed to store fact %s: %s",
                    fact.fact_id, fact.content[:60],
                    exc_info=True,
                )
                errors += 1

        logger.info(
            "FactStore: %d added, %d duplicates, %d errors (of %d total)",
            added, duplicates, errors, len(facts),
        )

        return StoreResult(
            added=added,
            duplicates=duplicates,
            errors=errors,
            total=len(facts),
        )

    async def search(
        self,
        query: str,
        property_id: str = "",
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[StoredFact]:
        """Search facts by semantic similarity.

        Args:
            query: Natural language query.
            property_id: Restrict results to this property.
            top_k: Maximum results to return.

        Returns:
            List of matching StoredFact, ordered by relevance.
        """
        try:
            vector = await self._get_embedding(query)
            if vector is None:
                return []

            client = self._get_client()
            if client is None:
                return []

            filters = {}
            if property_id:
                filters["property_id"] = property_id

            results = await self._vector_search(
                client, vector, top_k, filters,
            )
            return [self._payload_to_fact(r) for r in results]
        except Exception:
            logger.warning("FactStore search failed", exc_info=True)
            return []

    async def get_all(
        self,
        property_id: str,
        limit: int = 100,
    ) -> list[StoredFact]:
        """Retrieve all facts for a property (no vector search).

        Args:
            property_id: Property to fetch facts for.
            limit: Maximum facts to return.

        Returns:
            List of StoredFact for the property.
        """
        try:
            client = self._get_client()
            if client is None:
                return []

            results = await self._scroll(client, property_id, limit)
            return [self._payload_to_fact(r) for r in results]
        except Exception:
            logger.warning("FactStore get_all failed", exc_info=True)
            return []

    async def delete(self, fact_id: str) -> bool:
        """Remove a fact by ID.

        Args:
            fact_id: The fact to remove.

        Returns:
            True if deleted, False on error.
        """
        try:
            client = self._get_client()
            if client is None:
                return False

            await self._delete_point(client, fact_id)
            return True
        except Exception:
            logger.warning("FactStore delete failed for %s", fact_id, exc_info=True)
            return False

    @property
    def collection_name(self) -> str:
        """The Qdrant collection name."""
        return self._collection

    # ── Deduplication ────────────────────────────────────────── #

    async def _is_duplicate(
        self,
        content: str,
        property_id: str,
    ) -> bool:
        """Check if a fact already exists (cosine similarity > threshold).

        A match means the core information is already stored, even if
        phrased differently.  This prevents the KB from accumulating
        near-identical entries over multiple nightly consolidations.
        """
        vector = await self._get_embedding(content)
        if vector is None:
            return False

        client = self._get_client()
        if client is None:
            return False

        filters = {}
        if property_id:
            filters["property_id"] = property_id

        results = await self._vector_search(client, vector, top_k=1, filters=filters)
        if not results:
            return False

        score = results[0].get("score", 0.0)
        return score >= self._dedup_threshold

    # ── Qdrant wrappers (lazy init, async-safe) ──────────────── #

    def _get_client(self) -> Any:
        """Lazy-init Qdrant client.  Returns None if unavailable."""
        if self._client is not None:
            return self._client

        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self._qdrant_url)
            self._ensure_collection(self._client)
            return self._client
        except Exception:
            logger.warning("Failed to connect to Qdrant at %s", self._qdrant_url)
            return None

    def _ensure_collection(self, client: Any) -> None:
        """Create collection if it doesn't exist."""
        from qdrant_client.models import Distance, VectorParams

        collections = [c.name for c in client.get_collections().collections]
        if self._collection not in collections:
            client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=_EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection: %s", self._collection)

    async def _upsert(self, fact: StoredFact) -> None:
        """Upsert a single fact into Qdrant."""
        vector = await self._get_embedding(fact.content)
        if vector is None:
            return

        client = self._get_client()
        if client is None:
            return

        from qdrant_client.models import PointStruct
        import asyncio

        point = PointStruct(
            id=fact.fact_id,
            vector=vector,
            payload={
                "content": fact.content,
                "fact_type": fact.fact_type,
                "property_id": fact.property_id,
                "entity_id": fact.entity_id,
                "confidence": fact.confidence,
                "source": fact.source,
                "created_at": fact.created_at,
                **fact.metadata,
            },
        )

        await asyncio.to_thread(
            client.upsert,
            collection_name=self._collection,
            points=[point],
        )

    async def _vector_search(
        self,
        client: Any,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run a vector similarity search against Qdrant."""
        import asyncio
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant_filter = None
        if filters:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            qdrant_filter = Filter(must=conditions)

        results = await asyncio.to_thread(
            client.search,
            collection_name=self._collection,
            query_vector=vector,
            limit=top_k,
            query_filter=qdrant_filter,
        )

        return [
            {"score": r.score, "payload": r.payload, "id": r.id}
            for r in results
        ]

    async def _scroll(
        self,
        client: Any,
        property_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Scroll through all facts for a property (no vector needed)."""
        import asyncio
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant_filter = Filter(
            must=[FieldCondition(key="property_id", match=MatchValue(value=property_id))],
        )

        results = await asyncio.to_thread(
            client.scroll,
            collection_name=self._collection,
            scroll_filter=qdrant_filter,
            limit=limit,
        )

        points = results[0] if isinstance(results, tuple) else results
        return [{"payload": p.payload, "id": p.id} for p in points]

    async def _delete_point(self, client: Any, fact_id: str) -> None:
        """Delete a single point from Qdrant."""
        import asyncio
        from qdrant_client.models import PointIdsList

        await asyncio.to_thread(
            client.delete,
            collection_name=self._collection,
            points_selector=PointIdsList(points=[fact_id]),
        )

    async def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding vector for text.  Returns None on failure."""
        if self._embed is not None:
            try:
                return await self._embed(text)
            except Exception:
                logger.warning("Embedding function failed for: %s", text[:60])
                return None

        # Fallback: try litellm embedding
        try:
            import asyncio
            import litellm
            response = await litellm.aembedding(
                model="text-embedding-3-small",
                input=[text],
            )
            return response.data[0]["embedding"]
        except Exception:
            logger.warning("litellm embedding failed for: %s", text[:60])
            return None

    @staticmethod
    def _payload_to_fact(result: dict[str, Any]) -> StoredFact:
        """Convert a Qdrant search result to StoredFact."""
        payload = result.get("payload", {})
        return StoredFact(
            fact_id=str(result.get("id", "")),
            content=payload.get("content", ""),
            fact_type=payload.get("fact_type", "info"),
            property_id=payload.get("property_id", ""),
            entity_id=payload.get("entity_id", ""),
            confidence=payload.get("confidence", 1.0),
            source=payload.get("source", ""),
            created_at=payload.get("created_at", ""),
        )
