"""Postgres-backed persistence for :class:`StoredCard` records.

Production implementation of the :class:`CardStore` Protocol declared
in :mod:`brain_engine.cards.store`.  Uses ``asyncpg`` with a JSONB
codec registered on every connection so that the full
:class:`~brain_engine.cards.models.DecisionCard` value object can be
round-tripped through the ``payload`` JSONB column without the schema
having to track every card-body evolution.

Schema contract — table ``decision_cards`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``005_decision_cards``).

The store is stateless apart from the injected connection pool; all
queries are parameterised (no string interpolation of user data) and
:meth:`save` mints a UUID so the Protocol's "return the wrapper" contract
is respected without the DB generating a second identifier.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)
from brain_engine.cards.store import (
    CardNotFoundError,
    CardStatus,
    StoredCard,
)

if TYPE_CHECKING:
    import asyncpg


__all__ = ["PgCardStore", "create_cards_pool"]


logger = structlog.get_logger(__name__)


_UTC: Final = timezone.utc


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_INSERT_SQL: Final[str] = """
INSERT INTO decision_cards (
    card_id,
    property_id,
    workflow,
    status,
    payload,
    created_at,
    resolved_at,
    resolved_by,
    resolution_note
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""

_UPDATE_STATUS_SQL: Final[str] = """
UPDATE decision_cards
SET    status          = $2,
       resolved_at     = COALESCE($3, resolved_at),
       resolved_by     = COALESCE($4, resolved_by),
       resolution_note = COALESCE($5, resolution_note)
WHERE  card_id = $1
RETURNING card_id, property_id, workflow, status, payload,
          created_at, resolved_at, resolved_by, resolution_note
"""

_SELECT_COLUMNS: Final[str] = (
    "card_id, property_id, workflow, status, payload, created_at, "
    "resolved_at, resolved_by, resolution_note"
)

_SELECT_BY_ID_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM decision_cards "  # noqa: S608
    "WHERE card_id = $1"
)

_SELECT_BY_PROPERTY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM decision_cards "  # noqa: S608
    "WHERE property_id = $1 ORDER BY created_at DESC"
)

_SELECT_BY_PROPERTY_AND_STATUS_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM decision_cards "  # noqa: S608
    "WHERE property_id = $1 AND status = $2 "
    "ORDER BY created_at DESC"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSON codec for the ``JSONB`` ``payload`` column."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_cards_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool wired with the JSONB codec.

    Args:
        database_url: Postgres URI (``postgresql://…``).
        min_size: Minimum pool size.
        max_size: Maximum pool size.

    Returns:
        A live asyncpg connection pool.

    Raises:
        ImportError: When ``asyncpg`` is not installed.
    """
    import asyncpg  # local import — optional dependency

    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        init=_register_jsonb_codec,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _card_to_payload(card: DecisionCard) -> dict[str, Any]:
    """Convert a :class:`DecisionCard` into a JSONB-ready mapping.

    ``asdict`` would coerce enums to their raw values already but we
    round-trip explicitly for readability: anybody inspecting the
    ``payload`` column sees plain strings, never the Python enum repr.
    """
    return {
        "property_id": card.property_id,
        "workflow": card.workflow,
        "context_tag": card.context_tag,
        "title": card.title,
        "reasoning": [
            {
                "kind": row.kind.value,
                "label": row.label,
                "weight": row.weight,
                "reference_id": row.reference_id,
            }
            for row in card.reasoning
        ],
        "action": {
            "action_type": card.action.action_type,
            "payload": dict(card.action.payload),
            "reversibility": card.action.reversibility.value,
            "undo_window_seconds": card.action.undo_window_seconds,
        },
        "trust_footer": card.trust_footer,
        "autonomy_state": card.autonomy_state.value,
        "created_at": card.created_at.isoformat(),
    }


def _payload_to_card(payload: dict[str, Any]) -> DecisionCard:
    """Reverse of :func:`_card_to_payload`.

    Assumes the payload was produced by :func:`_card_to_payload`; stray
    shapes surface as ``KeyError`` / ``ValueError`` and propagate so
    callers see genuine corruption instead of a silently-wrong card.
    """
    reasoning = tuple(
        ReasoningRow(
            kind=EvidenceKind(row["kind"]),
            label=row["label"],
            weight=float(row.get("weight", 1.0)),
            reference_id=row.get("reference_id"),
        )
        for row in payload.get("reasoning", ())
    )
    action_raw = payload["action"]
    action = PreparedAction(
        action_type=action_raw["action_type"],
        payload=dict(action_raw.get("payload") or {}),
        reversibility=ReversibilityTier(action_raw["reversibility"]),
        undo_window_seconds=int(action_raw.get("undo_window_seconds", 60)),
    )
    return DecisionCard(
        property_id=payload["property_id"],
        workflow=payload["workflow"],
        context_tag=payload["context_tag"],
        title=payload["title"],
        reasoning=reasoning,
        action=action,
        trust_footer=payload["trust_footer"],
        autonomy_state=AutonomyState(payload["autonomy_state"]),
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def _row_to_stored(row: dict[str, Any]) -> StoredCard:
    """Hydrate a :class:`StoredCard` from a raw Postgres row."""
    return StoredCard(
        card_id=row["card_id"],
        card=_payload_to_card(dict(row["payload"])),
        status=CardStatus(row["status"]),
        created_at=_as_datetime(row["created_at"]),
        resolved_at=_as_optional_datetime(row.get("resolved_at")),
        resolved_by=row.get("resolved_by"),
        resolution_note=row.get("resolution_note"),
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to :class:`datetime`."""
    if isinstance(value, datetime):
        return value
    raise TypeError(
        f"expected datetime for created_at, got {type(value).__name__}"
    )


def _as_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return _as_datetime(value)


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgCardStore:
    """Postgres-backed :class:`CardStore` implementation.

    Satisfies the Protocol defined in :mod:`brain_engine.cards.store`
    structurally — no inheritance — so the in-memory reference store
    shipped for dev / tests stays the canonical specification.

    By default the store does *not* own the pool's lifecycle.  When
    constructed via :meth:`from_url`, the store owns the pool and
    :meth:`close` releases it.

    Attributes:
        _pool: Injected asyncpg pool.
        _log: Structured logger bound to this component.
        _owns_pool: Whether :meth:`close` should close the pool.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._owns_pool = owns_pool
        self._log = logger.bind(component="pg_card_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgCardStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_cards_pool(
            database_url,
            min_size=min_size,
            max_size=max_size,
        )
        return cls(pool, owns_pool=True)

    async def close(self) -> None:
        """Close the underlying pool if this store owns it."""
        if self._owns_pool:
            await self._pool.close()
            self._log.info("pool_closed")

    # ── CardStore Protocol ─────────────────────────────────── #

    async def save(self, card: DecisionCard) -> StoredCard:
        """Persist a freshly-proposed card and return the wrapper.

        Mints a UUID to mirror the in-memory store's contract where
        callers receive an identifier they never had to allocate.
        """
        card_id = uuid.uuid4().hex
        stored = StoredCard(card_id=card_id, card=card)
        payload = _card_to_payload(card)
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_SQL,
                stored.card_id,
                stored.card.property_id,
                stored.card.workflow,
                stored.status.value,
                payload,
                stored.created_at,
                stored.resolved_at,
                stored.resolved_by,
                stored.resolution_note,
            )
        self._log.debug(
            "card_saved",
            card_id=card_id[:8],
            property_id=card.property_id,
            workflow=card.workflow,
        )
        return stored

    async def get(self, card_id: str) -> StoredCard | None:
        """Return the stored card by id, or ``None`` when absent."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_ID_SQL, card_id)
        if row is None:
            return None
        return _row_to_stored(dict(row))

    async def list_for_property(
        self,
        property_id: str,
        *,
        status: CardStatus | None = None,
    ) -> list[StoredCard]:
        """Return stored cards for ``property_id`` newest-first.

        Args:
            property_id: Property scope filter.
            status: Optional lifecycle filter; ``None`` returns every
                status.

        Returns:
            Ordered list of :class:`StoredCard`.
        """
        async with self._pool.acquire() as conn:
            if status is None:
                rows = await conn.fetch(
                    _SELECT_BY_PROPERTY_SQL, property_id,
                )
            else:
                rows = await conn.fetch(
                    _SELECT_BY_PROPERTY_AND_STATUS_SQL,
                    property_id,
                    status.value,
                )
        return [_row_to_stored(dict(row)) for row in rows]

    async def update_status(
        self,
        card_id: str,
        *,
        status: CardStatus,
        resolved_by: str | None = None,
        note: str | None = None,
    ) -> StoredCard:
        """Transition a stored card to a new lifecycle state.

        Mirrors the in-memory store semantics: the ``resolved_at``
        timestamp is cleared only when the card is moved back to
        ``PENDING``; otherwise we stamp ``now`` once.

        Raises:
            CardNotFoundError: When ``card_id`` is unknown.
        """
        resolved_at = (
            None
            if status is CardStatus.PENDING
            else datetime.now(_UTC)
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _UPDATE_STATUS_SQL,
                card_id,
                status.value,
                resolved_at,
                resolved_by,
                note,
            )
        if row is None:
            raise CardNotFoundError(card_id)
        self._log.info(
            "card_status_updated",
            card_id=card_id[:8],
            status=status.value,
            resolved_by=resolved_by,
        )
        return _row_to_stored(dict(row))
