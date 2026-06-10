"""Postgres-backed persistence for :class:`Blocker` records.

Production implementation of the :class:`BlockerStore` Protocol declared
in :mod:`brain_engine.blockers.engine`.  Uses ``asyncpg`` with a JSONB
codec registered on every connection so that ``metadata`` dictionaries
round-trip natively into the ``JSONB`` column.

Schema contract — table ``blockers`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``002_blockers.sql``).

The store is stateless apart from the injected connection pool; all
queries are parameterised (no string interpolation of user data) and use
``ON CONFLICT (blocker_id) DO UPDATE`` so :meth:`save` doubles as a
"create-or-replace" primitive — useful when the engine re-hydrates a
:class:`Blocker` with :func:`dataclasses.replace` during resolution.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.approval.models import ActionType
from brain_engine.blockers.models import (
    Blocker,
    BlockerSeverity,
    BlockerType,
)

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_UPSERT_SQL: Final[str] = """
INSERT INTO blockers (
    blocker_id,
    blocker_type,
    severity,
    property_id,
    reservation_id,
    description,
    blocks_actions,
    metadata,
    created_at,
    resolved_at,
    resolved_by
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
)
ON CONFLICT (blocker_id) DO UPDATE SET
    blocker_type    = EXCLUDED.blocker_type,
    severity        = EXCLUDED.severity,
    property_id     = EXCLUDED.property_id,
    reservation_id  = EXCLUDED.reservation_id,
    description     = EXCLUDED.description,
    blocks_actions  = EXCLUDED.blocks_actions,
    metadata        = EXCLUDED.metadata,
    resolved_at     = EXCLUDED.resolved_at,
    resolved_by     = EXCLUDED.resolved_by
"""

_SELECT_COLUMNS: Final[str] = (
    "blocker_id, blocker_type, severity, property_id, reservation_id, "
    "description, blocks_actions, metadata, created_at, resolved_at, "
    "resolved_by"
)

_SELECT_BY_ID_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM blockers WHERE blocker_id = $1"  # noqa: S608,E501
)

_SELECT_ACTIVE_BY_PROPERTY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM blockers "  # noqa: S608
    "WHERE property_id = $1 AND resolved_at IS NULL "
    "ORDER BY created_at DESC"
)

_SELECT_ACTIVE_BY_PROPERTY_AND_RESERVATION_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM blockers "  # noqa: S608
    "WHERE property_id = $1 AND reservation_id = $2 "
    "AND resolved_at IS NULL "
    "ORDER BY created_at DESC"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSON codec for the ``JSONB`` ``metadata`` column."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_blockers_pool(
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


def _blocker_to_params(blocker: Blocker) -> tuple[Any, ...]:
    """Flatten a :class:`Blocker` into positional upsert parameters.

    Order and count mirror :data:`_UPSERT_SQL`.
    """
    return (
        blocker.blocker_id,
        blocker.blocker_type.value,
        blocker.severity.value,
        blocker.property_id,
        blocker.reservation_id,
        blocker.description,
        [a.value for a in blocker.blocks_actions],
        dict(blocker.metadata),
        blocker.created_at,
        blocker.resolved_at,
        blocker.resolved_by,
    )


def _row_to_blocker(row: dict[str, Any]) -> Blocker:
    """Hydrate a :class:`Blocker` from a raw Postgres row."""
    return Blocker(
        blocker_id=row["blocker_id"],
        blocker_type=BlockerType(row["blocker_type"]),
        severity=BlockerSeverity(row["severity"]),
        property_id=row["property_id"],
        reservation_id=row.get("reservation_id"),
        description=row.get("description") or "",
        blocks_actions=tuple(
            ActionType(a) for a in (row.get("blocks_actions") or ())
        ),
        metadata=dict(row.get("metadata") or {}),
        created_at=_as_datetime(row["created_at"]),
        resolved_at=_as_optional_datetime(row.get("resolved_at")),
        resolved_by=row.get("resolved_by"),
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to :class:`datetime`.

    asyncpg already returns TIMESTAMPTZ as :class:`datetime`, but tests
    may feed a pre-formatted dict, so the helper stays defensive.
    """
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


class PgBlockerStore:
    """Postgres-backed :class:`BlockerStore` implementation.

    Satisfies the Protocol defined in
    :mod:`brain_engine.blockers.engine` without inheritance — structural
    typing keeps the production store decoupled from the in-memory
    reference implementation shipped for dev / tests.

    By default the store does *not* own the pool's lifecycle.  When
    constructed via :meth:`from_url`, the store does own the pool and
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
        self._log = logger.bind(component="pg_blocker_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgBlockerStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_blockers_pool(
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

    # ── BlockerStore Protocol ──────────────────────────────── #

    async def save(self, blocker: Blocker) -> str:
        """Persist a :class:`Blocker`.

        Uses ``ON CONFLICT DO UPDATE`` so the same call works for the
        initial insert and for subsequent resolution writes without the
        caller having to distinguish them.

        Args:
            blocker: The blocker to persist.

        Returns:
            The ``blocker_id`` of the stored blocker.
        """
        params = _blocker_to_params(blocker)
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *params)
        self._log.debug(
            "blocker_saved",
            blocker_id=blocker.blocker_id[:8],
            blocker_type=blocker.blocker_type.value,
            resolved=blocker.is_resolved,
        )
        return blocker.blocker_id

    async def get(self, blocker_id: str) -> Blocker | None:
        """Retrieve a blocker by ``blocker_id``."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_ID_SQL, blocker_id)
        if row is None:
            return None
        return _row_to_blocker(dict(row))

    async def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return every active blocker for a property / reservation."""
        async with self._pool.acquire() as conn:
            if reservation_id is None:
                rows = await conn.fetch(
                    _SELECT_ACTIVE_BY_PROPERTY_SQL,
                    property_id,
                )
            else:
                rows = await conn.fetch(
                    _SELECT_ACTIVE_BY_PROPERTY_AND_RESERVATION_SQL,
                    property_id,
                    reservation_id,
                )
        return [_row_to_blocker(dict(row)) for row in rows]

    async def update(self, blocker: Blocker) -> None:
        """Persist an updated blocker (e.g. after resolution).

        Implemented as a plain :meth:`save` — the upsert semantics keep
        the code path identical to the initial insert, which is exactly
        what the Protocol contract asks for.
        """
        await self.save(blocker)
