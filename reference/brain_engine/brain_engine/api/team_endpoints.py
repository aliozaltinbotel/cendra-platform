"""Team mention + handoff HTTP endpoints.

Surfaces two cooperative primitives the V2 mobile UI relies on:

- ``POST /api/v1/team/mentions`` — emit a non-blocking @mention
  for another teammate; the receiving member's Inbox lists them
  via ``GET /api/v1/team/mentions/{member_id}``.
- ``POST /api/v1/team/handoffs`` — initiate an explicit transfer
  of a thread from one teammate to another; the receiver acts on
  it via accept / decline.  Cancellation is owner-side.

Dependencies are injected from ``server.py`` at lifespan start via
:func:`configure_team_deps`, mirroring the workflow / interview /
card / memory routers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from brain_engine.team import (
    Handoff,
    HandoffNotFoundError,
    HandoffStatus,
    HandoffStore,
    Mention,
    MentionStore,
    new_handoff_id,
    new_mention_id,
)


__all__ = [
    "configure_team_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/team", tags=["Team"])


_deps: dict[str, Any] = {}


def configure_team_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.  Must contain
            ``"mention_store"`` (a :class:`MentionStore`) and
            ``"handoff_store"`` (a :class:`HandoffStore`).
    """
    _deps.update(deps)


def _mention_store() -> MentionStore:
    """Return the configured :class:`MentionStore` or raise 503."""
    store = _deps.get("mention_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="MentionStore not configured",
        )
    return store


def _handoff_store() -> HandoffStore:
    """Return the configured :class:`HandoffStore` or raise 503."""
    store = _deps.get("handoff_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="HandoffStore not configured",
        )
    return store


# ── Wire models ───────────────────────────────────────────────── #


class CreateMentionRequest(BaseModel):
    """Payload for emitting a mention."""

    property_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    note: str = ""


class MentionResponse(BaseModel):
    """Wire form of :class:`Mention`."""

    mention_id: str
    property_id: str
    thread_id: str
    author_id: str
    target_id: str
    note: str
    created_at: datetime


class CreateHandoffRequest(BaseModel):
    """Payload for initiating a handoff."""

    property_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    from_member_id: str = Field(min_length=1)
    to_member_id: str = Field(min_length=1)
    reason: str = ""


class HandoffActionRequest(BaseModel):
    """Body for accept / decline / cancel endpoints."""

    actor_id: str = Field(min_length=1)
    note: str | None = None


class HandoffResponse(BaseModel):
    """Wire form of :class:`Handoff`."""

    handoff_id: str
    property_id: str
    thread_id: str
    from_member_id: str
    to_member_id: str
    reason: str
    status: HandoffStatus
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


def _serialise_mention(mention: Mention) -> MentionResponse:
    """Project a :class:`Mention` to its wire form."""
    return MentionResponse(
        mention_id=mention.mention_id,
        property_id=mention.property_id,
        thread_id=mention.thread_id,
        author_id=mention.author_id,
        target_id=mention.target_id,
        note=mention.note,
        created_at=mention.created_at,
    )


def _serialise_handoff(handoff: Handoff) -> HandoffResponse:
    """Project a :class:`Handoff` to its wire form."""
    return HandoffResponse(
        handoff_id=handoff.handoff_id,
        property_id=handoff.property_id,
        thread_id=handoff.thread_id,
        from_member_id=handoff.from_member_id,
        to_member_id=handoff.to_member_id,
        reason=handoff.reason,
        status=handoff.status,
        created_at=handoff.created_at,
        resolved_at=handoff.resolved_at,
        resolution_note=handoff.resolution_note,
    )


# ── Mention endpoints ─────────────────────────────────────────── #


@router.post(
    "/mentions",
    response_model=MentionResponse,
    status_code=201,
)
async def create_mention(
    payload: CreateMentionRequest,
) -> MentionResponse:
    """Emit a non-blocking @mention for a teammate."""
    store = _mention_store()
    mention = Mention(
        mention_id=new_mention_id(),
        property_id=payload.property_id,
        thread_id=payload.thread_id,
        author_id=payload.author_id,
        target_id=payload.target_id,
        note=payload.note,
    )
    saved = await store.save(mention)
    return _serialise_mention(saved)


@router.get(
    "/mentions/{member_id}",
    response_model=list[MentionResponse],
)
async def list_member_mentions(
    member_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MentionResponse]:
    """List mentions targeting ``member_id`` newest-first."""
    store = _mention_store()
    rows = await store.list_for_target(member_id, limit=limit)
    return [_serialise_mention(m) for m in rows]


# ── Handoff endpoints ─────────────────────────────────────────── #


@router.post(
    "/handoffs",
    response_model=HandoffResponse,
    status_code=201,
)
async def create_handoff(
    payload: CreateHandoffRequest,
) -> HandoffResponse:
    """Initiate a thread handoff from one teammate to another."""
    if payload.from_member_id == payload.to_member_id:
        raise HTTPException(
            status_code=400,
            detail="from_member_id and to_member_id must differ",
        )
    store = _handoff_store()
    handoff = Handoff(
        handoff_id=new_handoff_id(),
        property_id=payload.property_id,
        thread_id=payload.thread_id,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        reason=payload.reason,
    )
    saved = await store.save(handoff)
    return _serialise_handoff(saved)


@router.get(
    "/handoffs/property/{property_id}",
    response_model=list[HandoffResponse],
)
async def list_property_handoffs(
    property_id: str,
    status: HandoffStatus | None = Query(default=None),
) -> list[HandoffResponse]:
    """List handoffs for a property, optionally filtered by status."""
    store = _handoff_store()
    rows = await store.list_for_property(property_id, status=status)
    return [_serialise_handoff(h) for h in rows]


@router.get(
    "/handoffs/{handoff_id}",
    response_model=HandoffResponse,
)
async def get_handoff(handoff_id: str) -> HandoffResponse:
    """Return a single handoff or 404."""
    store = _handoff_store()
    handoff = await store.get(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="handoff not found")
    return _serialise_handoff(handoff)


async def _transition(
    handoff_id: str,
    *,
    target: HandoffStatus,
    actor_id: str,
    expected_actor: str,
    note: str | None,
) -> HandoffResponse:
    """Validate ownership and apply a status transition."""
    store = _handoff_store()
    existing = await store.get(handoff_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="handoff not found")
    if existing.status is not HandoffStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"handoff already {existing.status.value} — "
                "no further transitions accepted"
            ),
        )
    expected = getattr(existing, expected_actor)
    if actor_id != expected:
        raise HTTPException(
            status_code=403,
            detail=(
                f"actor {actor_id!r} is not the expected "
                f"{expected_actor.replace('_', ' ')}"
            ),
        )
    try:
        updated = await store.update_status(
            handoff_id,
            status=target,
            note=note,
        )
    except HandoffNotFoundError as exc:
        raise HTTPException(status_code=404, detail="handoff not found") from exc
    return _serialise_handoff(updated)


@router.post(
    "/handoffs/{handoff_id}/accept",
    response_model=HandoffResponse,
)
async def accept_handoff(
    handoff_id: str,
    payload: HandoffActionRequest,
) -> HandoffResponse:
    """Receiver accepts the handoff."""
    return await _transition(
        handoff_id,
        target=HandoffStatus.ACCEPTED,
        actor_id=payload.actor_id,
        expected_actor="to_member_id",
        note=payload.note,
    )


@router.post(
    "/handoffs/{handoff_id}/decline",
    response_model=HandoffResponse,
)
async def decline_handoff(
    handoff_id: str,
    payload: HandoffActionRequest,
) -> HandoffResponse:
    """Receiver declines the handoff."""
    return await _transition(
        handoff_id,
        target=HandoffStatus.DECLINED,
        actor_id=payload.actor_id,
        expected_actor="to_member_id",
        note=payload.note,
    )


@router.post(
    "/handoffs/{handoff_id}/cancel",
    response_model=HandoffResponse,
)
async def cancel_handoff(
    handoff_id: str,
    payload: HandoffActionRequest,
) -> HandoffResponse:
    """Initiator cancels a still-pending handoff."""
    return await _transition(
        handoff_id,
        target=HandoffStatus.CANCELLED,
        actor_id=payload.actor_id,
        expected_actor="from_member_id",
        note=payload.note,
    )
