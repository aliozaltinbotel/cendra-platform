"""Azure Cognitive Search RAG adapter.

Provides vector + text hybrid search against Cendra's Azure Cognitive
Search index. Used as Strategy 8 in CognitiveController.remember().

Cendra uses text-embedding-3-large (3072 dim) with HNSW algorithm.
Brain Engine keeps its own Qdrant for internal memory; this adapter
reads Cendra's external knowledge base (property FAQs, SOPs, amenities).

Config via environment variables:
    AZURE_SEARCH_ENDPOINT — Azure Search service URL
    AZURE_SEARCH_API_KEY  — Query or admin API key
    AZURE_SEARCH_INDEX    — Index name (default: property-knowledge)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_API_VERSION = "2024-07-01"


class AzureSearchAdapter:
    """REST client for Azure Cognitive Search.

    Args:
        endpoint: Azure Search service endpoint.
        api_key: Azure Search API key.
        index_name: Search index name.
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        index_name: str = "property-knowledge",
    ) -> None:
        self._endpoint = (
            endpoint or os.environ.get("AZURE_SEARCH_ENDPOINT", "")
        ).rstrip("/")
        self._api_key = (
            api_key or os.environ.get("AZURE_SEARCH_API_KEY", "")
        )
        self._index = (
            index_name or os.environ.get("AZURE_SEARCH_INDEX", "property-knowledge")
        )
        self._configured = bool(self._endpoint and self._api_key)

    @property
    def is_configured(self) -> bool:
        """Whether Azure Search credentials are available."""
        return self._configured

    async def search(
        self,
        query: str,
        property_id: str = "",
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search Azure Cognitive Search with hybrid vector + text.

        Args:
            query: Search query text.
            property_id: Optional property filter.
            top_k: Maximum results.

        Returns:
            List of result dicts with chunk_id, source, source_type,
            text, and score.
        """
        if not self._configured:
            return []

        try:
            embedding = await self._get_embedding(query)
            return await self._execute_search(
                embedding, query, property_id, top_k,
            )
        except Exception:
            logger.error("Azure Search failed", exc_info=True)
            return []

    async def _get_embedding(self, text: str) -> list[float]:
        """Generate embedding using text-embedding-3-large.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector (3072 dimensions).
        """
        import litellm
        response = await litellm.aembedding(
            model="text-embedding-3-large",
            input=[text],
        )
        return response.data[0]["embedding"]

    async def _execute_search(
        self,
        embedding: list[float],
        query: str,
        property_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Execute hybrid search against Azure Search REST API.

        Args:
            embedding: Query embedding vector.
            query: Original query text.
            property_id: Property filter.
            top_k: Max results.

        Returns:
            Parsed search results.
        """
        import httpx

        url = (
            f"{self._endpoint}/indexes/{self._index}"
            f"/docs/search?api-version={_API_VERSION}"
        )

        body: dict[str, Any] = {
            "search": query,
            "vectorQueries": [
                {
                    "vector": embedding,
                    "k": top_k,
                    "fields": "contentVector",
                    "kind": "vector",
                },
            ],
            "top": top_k,
            "select": "chunk_id,content,source,source_type,property_id",
        }

        if property_id:
            body["filter"] = f"property_id eq '{property_id}'"

        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return self._parse_results(data)

    @staticmethod
    def _parse_results(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Azure Search response into standardized format.

        Args:
            data: Raw Azure Search response.

        Returns:
            List of result dicts.
        """
        results: list[dict[str, Any]] = []
        for hit in data.get("value", []):
            results.append({
                "chunk_id": hit.get("chunk_id", ""),
                "text": hit.get("content", ""),
                "source": hit.get("source", ""),
                "source_type": hit.get("source_type", "document"),
                "score": hit.get("@search.score", 0.0),
            })
        return results

    async def health_check(self) -> bool:
        """Test connectivity to Azure Search.

        Returns:
            True if reachable.
        """
        if not self._configured:
            return False
        try:
            import httpx
            url = (
                f"{self._endpoint}/indexes/{self._index}"
                f"?api-version={_API_VERSION}"
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    url, headers={"api-key": self._api_key},
                )
                return resp.status_code == 200
        except Exception:
            return False
