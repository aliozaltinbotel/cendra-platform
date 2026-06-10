"""Postgres-backed persistence for :class:`UnansweredThread` rows.

Production implementation of the :class:`UnansweredThreadStore` Protocol
declared in :mod:`brain_engine.sandbox.store`.  Uses ``asyncpg`` and
``ON CONFLICT (conversation_id) DO UPDATE`` so :meth:`put` is the
single primitive for both first-time capture and re-generation after
the PM has reviewed (and rejected) a previous candidate.

Schema contract — table ``unanswered_threads`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``011_unanswered_threads``).

The store is stateless apart from the injected connection pool; callers
own the pool's lifecycle unless the store was built via :meth:`from_url`,
in which case :meth:`close` releases it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.sandbox.models import UnansweredThread

if TYPE_CHECKING:
    import asyncpg


__all__ = ["PgUnansweredThreadStore", "create_sandbox_pool"]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_UPSERT_SQL: Final[str] = """
INSERT INTO unanswered_threads (
    conversation_id,
    property_id,
    last_guest_message,
    last_guest_sent_at,
    example_reply,
    generated_by,
    language,
    generated_at,
    needs_review_reason
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (conversation_id) DO UPDATE SET
    property_id          = EXCLUDED.property_id,
    last_guest_message   = EXCLUDED.last_guest_message,
    last_guest_sent_at   = EXCLUDED.last_guest_sent_at,
    example_reply        = EXCLUDED.example_reply,
    generated_by         = EXCLUDED.generated_by,
    language             = EXCLUDED.language,
    generated_at         = EXCLUDED.generated_at,
    needs_review_reason  = EXCLUDED.needs_review_reason
"""

_SELECT_COLUMNS: Final[str] = (
    "conversation_id, property_id, last_guest_message, "
    "last_guest_sent_at, example_reply, generated_by, language, "
    "generated_at, needs_review_reason"
)

_SELECT_BY_KEY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM unanswered_threads "  # noqa: S608
    "WHERE conversation_id = $1"
)

_SELECT_BY_PROPERTY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM unanswered_threads "  # noqa: S608
    "WHERE property_id = $1 ORDER BY last_guest_sent_at DESC"
)

_DELETE_BY_PROPERTY_SQL: Final[str] = (
    "DELETE FROM unanswered_threads WHERE property_id = $1"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def create_sandbox_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool sized for the sandbox store.

    The sandbox SQL footprint is small (one upsert per unanswered
    thread, list reads for the UI) so a narrow pool keeps the
    connection count under the cendra-pg-app secret's quota.

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
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _thread_to_params(thread: UnansweredThread) -> tuple[Any, ...]:
    """Flatten an :class:`UnansweredThread` into upsert parameters.

    Order and count mirror :data:`_UPSERT_SQL`.
    """
    return (
        thread.conversation_id,
        thread.property_id,
        thread.last_guest_message,
        thread.last_guest_sent_at,
        thread.example_reply,
        thread.generated_by,
        thread.language,
        thread.generated_at,
        thread.needs_review_reason,
    )


def _row_to_thread(row: dict[str, Any]) -> UnansweredThread:
    """Hydrate an :class:`UnansweredThread` from a raw Postgres row."""
    return UnansweredThread(
        conversation_id=row["conversation_id"],
        property_id=row["property_id"],
        last_guest_message=row["last_guest_message"],
        last_guest_sent_at=_as_datetime(row["last_guest_sent_at"]),
        example_reply=row["example_reply"],
        generated_by=row["generated_by"],
        language=row.get("language") or "",
        generated_at=_as_datetime(row["generated_at"]),
        needs_review_reason=row.get("needs_review_reason") or "",
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to :class:`datetime`."""
    if isinstance(value, datetime):
        return value
    raise TypeError(
        f"expected datetime, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgUnansweredThreadStore:
    """Postgres-backed :class:`UnansweredThreadStore` implementation.

    Satisfies the Protocol structurally — no inheritance — so the
    in-memory reference implementation stays the canonical contract.

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
        self._log = logger.bind(component="pg_unanswered_thread_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> PgUnansweredThreadStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_sandbox_pool(
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

    # ── UnansweredThreadStore Protocol ────────────────────── #

    async def put(self, thread: UnansweredThread) -> None:
        """Upsert one sandbox row keyed by ``conversation_id``."""
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *_thread_to_params(thread))
        self._log.debug(
            "thread_saved",
            conversation_id=thread.conversation_id,
            property_id=thread.property_id,
            generated_by=thread.generated_by,
        )

    async def get(self, conversation_id: str) -> UnansweredThread | None:
        """Return the row for one conversation, or ``None``."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_KEY_SQL, conversation_id)
        if row is None:
            return None
        return _row_to_thread(dict(row))

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[UnansweredThread]:
        """Return every sandbox row for ``property_id``.

        Ordered by ``last_guest_sent_at`` descending — the most
        recent unanswered thread is the one the UI surfaces first.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_PROPERTY_SQL, property_id)
        return [_row_to_thread(dict(row)) for row in rows]

    async def clear_property(self, property_id: str) -> None:
        """Delete every sandbox row for ``property_id``.

        Called before a fresh harvest so stale rows do not linger
        when the PM has answered inside the PMS and the thread
        drops off the unanswered list.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_BY_PROPERTY_SQL, property_id)
        self._log.debug(
            "threads_cleared",
            property_id=property_id,
        )
