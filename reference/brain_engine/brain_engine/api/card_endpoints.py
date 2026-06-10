"""Decision-card HTTP endpoints — V2 UI surface.

Exposes the proposed-card lifecycle over HTTP so the V2 mobile UI can:

1. ``POST /api/v1/cards/propose`` — engine pushes a freshly-built
   :class:`DecisionCard` into the store.  Returns the wrapped record
   with the minted ``card_id``.
2. ``GET /api/v1/cards/property/{property_id}`` — list cards for a
   property, optionally filtered by ``status``.
3. ``GET /api/v1/cards/{card_id}`` — read a single card.
4. ``POST /api/v1/cards/{card_id}/confirm`` — PM (or autopilot)
   confirms.  Idempotent: repeated calls keep the card confirmed.
5. ``POST /api/v1/cards/{card_id}/dismiss`` — PM declines.

The router uses the same ``configure_*_deps`` injection pattern as
the workflow and interview routers; ``server.py`` wires a single
:class:`CardStore` instance into ``_deps`` at lifespan start.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards import (
    CardNotFoundError,
    CardStatus,
    CardStore,
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
    StoredCard,
)


__all__ = [
    "configure_card_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/cards", tags=["Decision Cards"])


# Shared deps — populated from server.py at lifespan start.
_deps: dict[str, Any] = {}


def configure_card_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.  Must contain the
            key ``"card_store"`` mapped to a live :class:`CardStore`.
    """
    _deps.update(deps)


def _store() -> CardStore:
    """Return the configured :class:`CardStore` or raise 503."""
    store = _deps.get("card_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="CardStore not configured",
        )
    return store


# ── Wire models ───────────────────────────────────────────────── #


class ReasoningRowPayload(BaseModel):
    """Wire form of :class:`ReasoningRow`."""

    kind: EvidenceKind
    label: str
    weight: float = 1.0
    reference_id: str | None = None

    def to_value(self) -> ReasoningRow:
        """Convert to the internal value object."""
        return ReasoningRow(
            kind=self.kind,
            label=self.label,
            weight=self.weight,
            reference_id=self.reference_id,
        )


class PreparedActionPayload(BaseModel):
    """Wire form of :class:`PreparedAction`."""

    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reversibility: ReversibilityTier = ReversibilityTier.AMBER
    undo_window_seconds: int = 60

    def to_value(self) -> PreparedAction:
        """Convert to the internal value object."""
        return PreparedAction(
            action_type=self.action_type,
            payload=dict(self.payload),
            reversibility=self.reversibility,
            undo_window_seconds=self.undo_window_seconds,
        )


class ProposeCardRequest(BaseModel):
    """Payload for the ``propose`` endpoint.

    Mirrors :class:`DecisionCard` field-for-field so callers can
    re-use the exact engine output.
    """

    property_id: str
    workflow: str
    context_tag: str
    title: str
    reasoning: list[ReasoningRowPayload]
    action: PreparedActionPayload
    trust_footer: str
    autonomy_state: AutonomyState

    def to_card(self) -> DecisionCard:
        """Build the immutable :class:`DecisionCard`."""
        return DecisionCard(
            property_id=self.property_id,
            workflow=self.workflow,
            context_tag=self.context_tag,
            title=self.title,
            reasoning=tuple(row.to_value() for row in self.reasoning),
            action=self.action.to_value(),
            trust_footer=self.trust_footer,
            autonomy_state=self.autonomy_state,
        )


class CardActionRequest(BaseModel):
    """Body for confirm/dismiss endpoints."""

    actor: str = Field(min_length=1)
    note: str | None = None


class ReasoningRowResponse(BaseModel):
    """Wire form of stored :class:`ReasoningRow`."""

    kind: EvidenceKind
    label: str
    weight: float
    reference_id: str | None


class PreparedActionResponse(BaseModel):
    """Wire form of stored :class:`PreparedAction`."""

    action_type: str
    payload: dict[str, Any]
    reversibility: ReversibilityTier
    undo_window_seconds: int


class DecisionCardResponse(BaseModel):
    """Wire form of :class:`DecisionCard`."""

    property_id: str
    workflow: str
    context_tag: str
    title: str
    reasoning: list[ReasoningRowResponse]
    action: PreparedActionResponse
    trust_footer: str
    autonomy_state: AutonomyState
    created_at: datetime
    is_actionable: bool
    has_blockers: bool


class StoredCardResponse(BaseModel):
    """Wire form of :class:`StoredCard`."""

    card_id: str
    status: CardStatus
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    card: DecisionCardResponse


def _serialise(stored: StoredCard) -> StoredCardResponse:
    """Project a :class:`StoredCard` to its wire form."""
    card = stored.card
    return StoredCardResponse(
        card_id=stored.card_id,
        status=stored.status,
        created_at=stored.created_at,
        resolved_at=stored.resolved_at,
        resolved_by=stored.resolved_by,
        resolution_note=stored.resolution_note,
        card=DecisionCardResponse(
            property_id=card.property_id,
            workflow=card.workflow,
            context_tag=card.context_tag,
            title=card.title,
            reasoning=[
                ReasoningRowResponse(
                    kind=row.kind,
                    label=row.label,
                    weight=row.weight,
                    reference_id=row.reference_id,
                )
                for row in card.reasoning
            ],
            action=PreparedActionResponse(
                action_type=card.action.action_type,
                payload=dict(card.action.payload),
                reversibility=card.action.reversibility,
                undo_window_seconds=card.action.undo_window_seconds,
            ),
            trust_footer=card.trust_footer,
            autonomy_state=card.autonomy_state,
            created_at=card.created_at,
            is_actionable=card.is_actionable,
            has_blockers=card.has_blockers,
        ),
    )


# ── Endpoints ─────────────────────────────────────────────────── #


@router.post(
    "/propose",
    response_model=StoredCardResponse,
    status_code=201,
)
async def propose_card(payload: ProposeCardRequest) -> StoredCardResponse:
    """Persist a freshly-proposed card and return the wrapper."""
    store = _store()
    stored = await store.save(payload.to_card())
    return _serialise(stored)


@router.get(
    "/property/{property_id}",
    response_model=list[StoredCardResponse],
)
async def list_property_cards(
    property_id: str,
    status: CardStatus | None = Query(default=None),
) -> list[StoredCardResponse]:
    """List stored cards for a property, optionally filtered."""
    store = _store()
    rows = await store.list_for_property(property_id, status=status)
    return [_serialise(stored) for stored in rows]


@router.get("/{card_id}", response_model=StoredCardResponse)
async def get_card(card_id: str) -> StoredCardResponse:
    """Return a single stored card or 404."""
    store = _store()
    stored = await store.get(card_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="card not found")
    return _serialise(stored)


@router.post("/{card_id}/confirm", response_model=StoredCardResponse)
async def confirm_card(
    card_id: str,
    payload: CardActionRequest,
) -> StoredCardResponse:
    """Mark a card ``CONFIRMED``."""
    store = _store()
    try:
        stored = await store.update_status(
            card_id,
            status=CardStatus.CONFIRMED,
            resolved_by=payload.actor,
            note=payload.note,
        )
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail="card not found") from exc
    return _serialise(stored)


@router.post("/{card_id}/dismiss", response_model=StoredCardResponse)
async def dismiss_card(
    card_id: str,
    payload: CardActionRequest,
) -> StoredCardResponse:
    """Mark a card ``DISMISSED``."""
    store = _store()
    try:
        stored = await store.update_status(
            card_id,
            status=CardStatus.DISMISSED,
            resolved_by=payload.actor,
            note=payload.note,
        )
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail="card not found") from exc
    return _serialise(stored)
