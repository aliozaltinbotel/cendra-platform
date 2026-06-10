"""RAG document search tool — queries property knowledge base.

Wraps the existing Azure Search adapter and Qdrant retriever
into a conversation tool the ReAct agent can call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from brain_engine.streaming.emit_helpers import emit_rag_hit
from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


@tool(description=(
    "Search the property knowledge base for information about "
    "amenities, WiFi, rules, policies, check-in instructions, "
    "parking, appliances, and other property details. "
    "Use SHORT queries (2-5 words). Call separately per topic. "
    "Do NOT use for availability/dates/pricing — use availability_checker. "
    "Do NOT use for reservation details — use reservation_info_retriever. "
    "Do NOT use for nearby places — use location_search. "
    "Do NOT use when guest only says thank you."
))
async def rag_document_search(
    query: str,
    runtime: ToolRuntime | None = None,
) -> str:
    """Search property knowledge base via RAG.

    Args:
        query: Short search query (2-5 words).
        runtime: Injected runtime with config and store.

    Returns:
        Retrieved knowledge base content or 'no results'.
    """
    property_id = _get_property_id(runtime)
    results = await _search_knowledge_base(query, property_id, runtime)

    if not results:
        return f"No information found for: {query}"

    return _format_results(results)


def _get_property_id(runtime: ToolRuntime | None) -> str:
    """Extract property_id from runtime config.

    Args:
        runtime: Tool runtime context.

    Returns:
        Property ID string.
    """
    if not runtime:
        return ""
    return runtime.config.get("property_id", "")


async def _search_knowledge_base(
    query: str,
    property_id: str,
    runtime: ToolRuntime | None,
) -> list[dict[str, Any]]:
    """Execute RAG search against configured backends.

    Tries Azure Cognitive Search first, falls back to Qdrant
    semantic memory if Azure is not configured.

    Args:
        query: Search query.
        property_id: Filter by property.
        runtime: Tool runtime for accessing services.

    Returns:
        List of result dicts with 'content' and 'score'.
    """
    t0 = time.perf_counter()
    try:
        from brain_engine.integrations.azure_search_adapter import (
            AzureSearchAdapter,
        )
        adapter = AzureSearchAdapter()
        raw = await adapter.search(
            query=query,
            property_id=property_id,
            top_k=6,
        )
        results = [
            {"content": r.get("content", ""), "score": r.get("score", 0.0)}
            for r in raw
            if r.get("content")
        ]
        emit_rag_hit(
            query=query,
            source="azure_search",
            docs=[
                {
                    "id": str(r.get("id", "")),
                    "title": str(r.get("title", "")),
                    "score": float(r.get("score", 0.0)),
                    "excerpt": str(r.get("content", "")),
                }
                for r in raw
                if r.get("content")
            ],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return results
    except Exception:
        logger.debug("Azure Search unavailable, trying Qdrant", exc_info=True)

    try:
        from brain_engine.memory.semantic_memory import SemanticMemory
        memory = SemanticMemory()
        # Multi-tenancy gate: without this filter the bi-encoder
        # returns semantically-similar chunks from ANY property in
        # the collection — Azure Search above already enforces the
        # same filter, this brings the Qdrant fallback to parity.
        metadata_filter = (
            {"property_id": property_id} if property_id else None
        )
        raw = await memory.search(
            query,
            top_k=6,
            metadata_filter=metadata_filter,
        )
        results = [
            {"content": r.get("text", ""), "score": r.get("score", 0.0)}
            for r in raw
            if r.get("text")
        ]
        emit_rag_hit(
            query=query,
            source="qdrant_semantic",
            docs=[
                {
                    "id": str(r.get("id", "")),
                    "title": "",
                    "score": float(r.get("score", 0.0)),
                    "excerpt": str(r.get("text", "")),
                }
                for r in raw
                if r.get("text")
            ],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return results
    except Exception:
        logger.debug("Qdrant unavailable", exc_info=True)

    # PMS REST fallbacks (Knowledge Base + property descriptions) were
    # retired on 2026-04-28 along with the Botel PMS adapter.  Brain
    # Engine reads property data exclusively through the onboarding-api
    # unified GraphQL gateway; that path is consumed by other tools
    # (availability_checker, reservation_info_retriever) so RAG search
    # cleanly returns "no results" once Azure Search and Qdrant fall
    # through.

    logger.warning("All RAG backends unavailable")
    emit_rag_hit(
        query=query,
        source="rag_unavailable",
        docs=[],
        latency_ms=(time.perf_counter() - t0) * 1000.0,
    )
    return []


def _format_results(results: list[dict[str, Any]]) -> str:
    """Format RAG results into agent-readable text.

    Args:
        results: List of search results.

    Returns:
        Formatted string with numbered results.
    """
    lines: list[str] = []
    for i, r in enumerate(results[:4], 1):
        content = r["content"][:500]
        lines.append(f"[{i}] {content}")
    return "\n\n".join(lines)
