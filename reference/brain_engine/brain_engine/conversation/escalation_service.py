"""Escalation Service — tracks knowledge base escalation state.

Manages escalation chunk IDs from the knowledge base stored in
Redis. When a RAG result matches an escalated chunk, the response
is flagged for PM review instead of auto-sending.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_REDIS_KEY_PREFIX = "KnowledgeBaseEscalations"


class EscalationService:
    """Manages knowledge base escalation tracking.

    Loads escalated chunk IDs from Redis, caches in memory,
    and checks if specific RAG results should be escalated.

    Args:
        redis_client: Async Redis client.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._cache: dict[str, tuple[set[str], float]] = {}

    async def get_escalations(
        self,
        customer_id: str,
        force_refresh: bool = False,
    ) -> set[str]:
        """Get escalated chunk IDs for a customer.

        Args:
            customer_id: Customer identifier.
            force_refresh: Bypass cache.

        Returns:
            Set of escalated chunk ID strings.
        """
        if not force_refresh:
            cached = self._get_from_cache(customer_id)
            if cached is not None:
                return cached

        escalations = await self._load_from_redis(customer_id)
        self._put_in_cache(customer_id, escalations)
        return escalations

    async def is_chunk_escalated(
        self,
        customer_id: str,
        chunk_id: str,
    ) -> bool:
        """Check if a specific knowledge base chunk is escalated.

        Args:
            customer_id: Customer identifier.
            chunk_id: Knowledge base chunk ID.

        Returns:
            True if this chunk requires escalation.
        """
        escalations = await self.get_escalations(customer_id)
        return chunk_id in escalations

    async def check_rag_results(
        self,
        customer_id: str,
        rag_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Check RAG results for escalated chunks.

        Marks each result with an 'escalated' flag.

        Args:
            customer_id: Customer identifier.
            rag_results: RAG search results with 'chunk_id' or 'document_id'.

        Returns:
            Results with 'is_escalated' field added.
        """
        escalations = await self.get_escalations(customer_id)

        for result in rag_results:
            chunk_id = result.get("chunk_id", result.get("document_id", ""))
            result["is_escalated"] = chunk_id in escalations

        return rag_results

    async def add_escalation(
        self,
        customer_id: str,
        chunk_id: str,
    ) -> None:
        """Add a chunk ID to the escalation list.

        Args:
            customer_id: Customer identifier.
            chunk_id: Chunk to escalate.
        """
        escalations = await self.get_escalations(customer_id)
        escalations.add(chunk_id)
        await self._save_to_redis(customer_id, escalations)
        self._put_in_cache(customer_id, escalations)

    async def remove_escalation(
        self,
        customer_id: str,
        chunk_id: str,
    ) -> None:
        """Remove a chunk ID from the escalation list.

        Args:
            customer_id: Customer identifier.
            chunk_id: Chunk to de-escalate.
        """
        escalations = await self.get_escalations(customer_id)
        escalations.discard(chunk_id)
        await self._save_to_redis(customer_id, escalations)
        self._put_in_cache(customer_id, escalations)

    def _get_from_cache(self, customer_id: str) -> set[str] | None:
        """Check memory cache for escalation data."""
        entry = self._cache.get(customer_id)
        if not entry:
            return None
        data, cached_at = entry
        if time.monotonic() - cached_at > _CACHE_TTL:
            del self._cache[customer_id]
            return None
        return data

    def _put_in_cache(self, customer_id: str, data: set[str]) -> None:
        """Store escalation data in memory cache."""
        self._cache[customer_id] = (data, time.monotonic())

    async def _load_from_redis(self, customer_id: str) -> set[str]:
        """Load escalations from Redis."""
        if not self._redis:
            return set()
        key = f"{_REDIS_KEY_PREFIX}:{customer_id}"
        try:
            raw = await self._redis.get(key)
            if not raw:
                return set()
            data = json.loads(raw)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            logger.warning("Failed to load escalations for %s", customer_id, exc_info=True)
            return set()

    async def _save_to_redis(self, customer_id: str, data: set[str]) -> None:
        """Save escalations to Redis."""
        if not self._redis:
            return
        key = f"{_REDIS_KEY_PREFIX}:{customer_id}"
        try:
            await self._redis.set(key, json.dumps(list(data)), ex=86400)
        except Exception:
            logger.warning("Failed to save escalations for %s", customer_id, exc_info=True)
