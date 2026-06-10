"""Memory-edit HTTP endpoints — PM-facing CRUD over the fact store.

The V2 UI lets a PM inspect and curate what Brain Engine has
"remembered" about their properties and guests.  The authoritative
long-lived memory layer for this surface is
:class:`brain_engine.memory.fact_store.FactStore` — a Qdrant-backed
collection of deduplicated facts that feed the ``ESTABLISHED FACTS``
section of the context assembler.

Endpoints:

1. ``GET  /api/v1/memory/facts/{property_id}`` — list stored facts.
2. ``POST /api/v1/memory/facts`` — create a new PM-authored fact.
3. ``PATCH /api/v1/memory/facts/{fact_id}`` — edit content / type /
   confidence in place (delete + re-store preserving the id).
4. ``DELETE /api/v1/memory/facts/{fact_id}`` — remove a fact.

Dependencies are injected from ``server.py`` at lifespan start via
:func:`configure_memory_deps`, mirroring the workflow / interview /
card routers.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from brain_engine.memory.fact_store import FactStore, StoredFact


__all__ = [
    "configure_memory_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])


_deps: dict[str, Any] = {}


def configure_memory_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.  Must contain
            ``"fact_store"`` mapped to a live :class:`FactStore`.
    """
    _deps.update(deps)


def _fact_store() -> FactStore:
    """Return the configured :class:`FactStore` or raise 503."""
    store = _deps.get("fact_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="FactStore not configured",
        )
    return store


# ── Wire models ───────────────────────────────────────────────── #


class FactResponse(BaseModel):
    """Wire form of :class:`StoredFact`."""

    fact_id: str
    content: str
    fact_type: str
    property_id: str
    entity_id: str
    confidence: float
    source: str
    created_at: str
    metadata: dict[str, Any]


class CreateFactRequest(BaseModel):
    """Payload for creating a PM-authored fact."""

    property_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    fact_type: str = "info"
    entity_id: str = ""
    confidence: float = 1.0
    source: str = "pm_edit"
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateFactRequest(BaseModel):
    """Partial update payload for a stored fact.

    All fields are optional — only provided fields are overwritten
    on the server side.  ``property_id`` is required because the
    fact store is partitioned by property.
    """

    property_id: str = Field(min_length=1)
    content: str | None = None
    fact_type: str | None = None
    entity_id: str | None = None
    confidence: float | None = None
    source: str | None = None
    metadata: dict[str, Any] | None = None


def _serialise(fact: StoredFact) -> FactResponse:
    """Project a :class:`StoredFact` to its wire form."""
    return FactResponse(
        fact_id=fact.fact_id,
        content=fact.content,
        fact_type=fact.fact_type,
        property_id=fact.property_id,
        entity_id=fact.entity_id,
        confidence=fact.confidence,
        source=fact.source,
        created_at=fact.created_at,
        metadata=dict(fact.metadata),
    )


async def _find_by_id(
    store: FactStore,
    *,
    property_id: str,
    fact_id: str,
    limit: int,
) -> StoredFact | None:
    """Locate a fact by id within ``property_id``.

    :class:`FactStore` does not expose a direct by-id accessor, so
    the implementation scrolls the property slice and filters —
    acceptable for the PM-edit surface where the page size is
    bounded by the UI.
    """
    facts = await store.get_all(property_id, limit=limit)
    for fact in facts:
        if fact.fact_id == fact_id:
            return fact
    return None


def _now_iso() -> str:
    """ISO-8601 UTC timestamp used for ``created_at`` defaults."""
    return datetime.now(timezone.utc).isoformat()


# ── Endpoints ─────────────────────────────────────────────────── #


@router.get(
    "/facts/{property_id}",
    response_model=list[FactResponse],
)
async def list_facts(
    property_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FactResponse]:
    """List stored facts for a property."""
    store = _fact_store()
    facts = await store.get_all(property_id, limit=limit)
    return [_serialise(f) for f in facts]


@router.post(
    "/facts",
    response_model=FactResponse,
    status_code=201,
)
async def create_fact(payload: CreateFactRequest) -> FactResponse:
    """Create a new PM-authored fact.

    The fact id is server-minted so the client does not need to
    coordinate uniqueness.  Dedup still runs — a near-duplicate is
    rejected with 409 so the PM sees the conflict explicitly.
    """
    store = _fact_store()
    fact = StoredFact(
        fact_id=uuid.uuid4().hex,
        content=payload.content,
        fact_type=payload.fact_type,
        property_id=payload.property_id,
        entity_id=payload.entity_id,
        confidence=payload.confidence,
        source=payload.source,
        created_at=_now_iso(),
        metadata=dict(payload.metadata),
    )
    result = await store.store_facts([fact], property_id=payload.property_id)
    if result.duplicates:
        raise HTTPException(
            status_code=409,
            detail="fact is a near-duplicate of an existing entry",
        )
    if result.errors or not result.added:
        raise HTTPException(
            status_code=502,
            detail="fact store rejected the write",
        )
    return _serialise(fact)


@router.patch(
    "/facts/{fact_id}",
    response_model=FactResponse,
)
async def update_fact(
    fact_id: str,
    payload: UpdateFactRequest,
) -> FactResponse:
    """Edit a stored fact in place.

    Implemented as delete + insert under the same id.  Because the
    old entry is removed first, the dedup check during re-insert
    operates against *other* facts only — so renaming the same
    fact does not trigger a false-positive duplicate.
    """
    store = _fact_store()
    existing = await _find_by_id(
        store,
        property_id=payload.property_id,
        fact_id=fact_id,
        limit=500,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="fact not found")
    merged = StoredFact(
        fact_id=existing.fact_id,
        content=(
            payload.content if payload.content is not None
            else existing.content
        ),
        fact_type=(
            payload.fact_type if payload.fact_type is not None
            else existing.fact_type
        ),
        property_id=existing.property_id,
        entity_id=(
            payload.entity_id if payload.entity_id is not None
            else existing.entity_id
        ),
        confidence=(
            payload.confidence if payload.confidence is not None
            else existing.confidence
        ),
        source=(
            payload.source if payload.source is not None
            else existing.source
        ),
        created_at=existing.created_at,
        metadata=(
            dict(payload.metadata) if payload.metadata is not None
            else dict(existing.metadata)
        ),
    )
    await store.delete(existing.fact_id)
    result = await store.store_facts(
        [merged], property_id=existing.property_id,
    )
    if result.duplicates:
        # Roll the dedup conflict up to the client; the old entry
        # has already been removed, so the caller should retry with
        # a different content to avoid data loss.
        raise HTTPException(
            status_code=409,
            detail=(
                "updated fact collides with another entry — "
                "original has been deleted"
            ),
        )
    if result.errors or not result.added:
        raise HTTPException(
            status_code=502,
            detail="fact store rejected the update",
        )
    return _serialise(merged)


@router.delete(
    "/facts/{fact_id}",
    status_code=204,
)
async def delete_fact(fact_id: str) -> None:
    """Remove a fact by id.

    The FactStore ``delete`` returns ``False`` on error; we map
    that to 502 so the UI can distinguish "gone" from "could not
    reach the backing store".  There is no cheap way to tell
    "already deleted" from "never existed" in Qdrant without a
    follow-up query, so 204 is returned on either success path.
    """
    store = _fact_store()
    ok = await store.delete(fact_id)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="fact store rejected the delete",
        )
    return None
