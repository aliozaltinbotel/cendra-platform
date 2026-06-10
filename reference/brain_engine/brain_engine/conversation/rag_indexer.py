"""RAG Conversation Indexer — indexes past conversations for retrieval.

Processes completed conversations and indexes them into the vector
database so future RAG queries can find relevant past interactions.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IndexConversationRequest(BaseModel):
    """Input to POST /api/v1/rag-index-conversations."""

    customer_id: str
    property_id: str = ""
    conversation_id: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexConversationResponse(BaseModel):
    """Output of conversation indexing."""

    status: bool = True
    chunks_indexed: int = 0
    conversation_id: str = ""
    error: str | None = None


class RagAnswerRequest(BaseModel):
    """Input to POST /api/v1/rag-answer."""

    customer_id: str
    property_id: str = ""
    query: str = ""
    top_k: int = 6
    search_conversations: bool = False
    search_documents: bool = True


class RagAnswerResponse(BaseModel):
    """Output of RAG answer generation."""

    status: bool = True
    answer: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


async def index_conversation(
    request: IndexConversationRequest,
) -> IndexConversationResponse:
    """Index a conversation into the RAG vector database.

    Splits conversation into chunks, generates embeddings,
    and stores in the vector database for future retrieval.

    Args:
        request: Conversation data to index.

    Returns:
        Number of chunks indexed.
    """
    if not request.messages:
        return IndexConversationResponse(
            status=False, error="No messages to index",
        )

    chunks = _split_conversation(request.messages)
    indexed = 0

    for chunk in chunks:
        try:
            await _store_chunk(
                chunk=chunk,
                customer_id=request.customer_id,
                property_id=request.property_id,
                conversation_id=request.conversation_id,
                metadata=request.metadata,
            )
            indexed += 1
        except Exception:
            logger.warning("Failed to index chunk", exc_info=True)

    logger.info(
        "Indexed %d/%d chunks for conversation %s",
        indexed, len(chunks), request.conversation_id,
    )
    return IndexConversationResponse(
        chunks_indexed=indexed,
        conversation_id=request.conversation_id,
    )


async def generate_rag_answer(
    request: RagAnswerRequest,
) -> RagAnswerResponse:
    """Generate an answer using RAG retrieval.

    Searches the knowledge base and optionally past conversations
    to find relevant context, then generates an answer.

    Args:
        request: RAG query parameters.

    Returns:
        Generated answer with sources.
    """
    results: list[dict[str, Any]] = []

    if request.search_documents:
        doc_results = await _search_documents(
            request.query, request.property_id, request.top_k,
        )
        results.extend(doc_results)

    if request.search_conversations:
        conv_results = await _search_conversations(
            request.query, request.customer_id, request.top_k,
        )
        results.extend(conv_results)

    if not results:
        return RagAnswerResponse(
            answer="No relevant information found.",
            sources=[],
        )

    answer = _format_rag_results(results)
    sources = [
        {
            "content": r.get("content", "")[:200],
            "score": r.get("score", 0.0),
            "source": r.get("source", "unknown"),
        }
        for r in results[:request.top_k]
    ]

    return RagAnswerResponse(answer=answer, sources=sources)


def _split_conversation(
    messages: list[dict[str, str]],
) -> list[str]:
    """Split a conversation into indexable chunks.

    Groups messages into exchanges (user question + AI response)
    for meaningful retrieval chunks.

    Args:
        messages: List of message dicts.

    Returns:
        List of chunk strings.
    """
    chunks: list[str] = []
    current_chunk: list[str] = []

    for msg in messages:
        role = msg.get("role", msg.get("senderType", ""))
        content = msg.get("content", msg.get("text", ""))

        current_chunk.append(f"[{role}]: {content}")

        if role in ("assistant", "bot", "property") and len(current_chunk) >= 2:
            chunks.append("\n".join(current_chunk))
            current_chunk = []

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


async def _store_chunk(
    chunk: str,
    customer_id: str,
    property_id: str,
    conversation_id: str,
    metadata: dict[str, Any],
) -> None:
    """Store a conversation chunk in the vector database.

    Args:
        chunk: Text chunk to store.
        customer_id: Customer identifier.
        property_id: Property identifier.
        conversation_id: Source conversation ID.
        metadata: Additional metadata.
    """
    chunk_id = hashlib.md5(chunk.encode()).hexdigest()

    try:
        from brain_engine.memory.semantic_memory import SemanticMemory
        memory = SemanticMemory()
        await memory.store(
            text=chunk,
            metadata={
                "chunk_id": chunk_id,
                "customer_id": customer_id,
                "property_id": property_id,
                "conversation_id": conversation_id,
                "source": "conversation",
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            },
        )
    except Exception:
        logger.debug("SemanticMemory unavailable, trying Azure Search", exc_info=True)
        try:
            from brain_engine.integrations.azure_search_adapter import AzureSearchAdapter
            adapter = AzureSearchAdapter()
            await adapter.index_document(
                document_id=chunk_id,
                content=chunk,
                property_id=property_id,
                metadata={
                    "customer_id": customer_id,
                    "conversation_id": conversation_id,
                    "source": "conversation",
                    **metadata,
                },
            )
        except Exception:
            logger.warning("No vector backend available for indexing")
            raise


async def _search_documents(
    query: str,
    property_id: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Search document knowledge base."""
    try:
        from brain_engine.integrations.azure_search_adapter import AzureSearchAdapter
        adapter = AzureSearchAdapter()
        return await adapter.search(
            query=query, property_id=property_id, top_k=top_k,
        )
    except Exception:
        logger.debug("Azure Search unavailable")
        return []


async def _search_conversations(
    query: str,
    customer_id: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Search indexed past conversations."""
    try:
        from brain_engine.memory.semantic_memory import SemanticMemory
        memory = SemanticMemory()
        results = await memory.search(query, top_k=top_k)
        return [
            {**r, "source": "conversation"}
            for r in results
            if r.get("metadata", {}).get("customer_id") == customer_id
        ]
    except Exception:
        logger.debug("Semantic memory unavailable for conversation search")
        return []


def _format_rag_results(results: list[dict[str, Any]]) -> str:
    """Format RAG results into answer text."""
    lines: list[str] = []
    for i, r in enumerate(results[:6], 1):
        content = r.get("content", r.get("text", ""))[:500]
        lines.append(f"[{i}] {content}")
    return "\n\n".join(lines)
