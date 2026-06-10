"""Postgres-backed implementation of :class:`PropertyStateStore`.

Lives in its own module so the in-memory contract file
(:mod:`property_state_store`) stays free of an ``asyncpg``
import.  Tests that exercise only the Protocol pull from the
in-memory module; integration tests that need a real pool
import from this file.

SQL is intentionally hand-written rather than ORM-driven: the
write path is hot, the column set is closed (migration ``034``
is the only schema authority), and asyncpg's positional-args
contract surfaces parameter-count mistakes at call time rather
than as silent NULLs in the database.

Concurrency model:
    * :meth:`create_if_absent` uses ``ON CONFLICT
      (property_channel_id) DO NOTHING RETURNING`` followed by a
      SELECT fallback so two concurrent inserts both observe the
      winning row, never raise.
    * :meth:`update` is a full-row write keyed on the PK and
      ``RETURNING`` the post-image — callers receive the
      authoritative state in one round trip.

Stage 2 worker concerns (advisory locks, optimistic
``WHERE status = $expected`` guards) are deliberately out of
scope for PR-A: the Stage 1 callers all run inside a single
event loop and serialise their own intent decisions through
:func:`request_bootstrap` (PR-B).  Adding lock machinery now
would be YAGNI and would change the public contract before its
first real consumer arrives.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants.property_state import PropertyState
from brain_engine.tenants.property_state_store import (
    PropertyStateNotFoundError,
)

if TYPE_CHECKING:
    import asyncpg

__all__ = ["PostgresPropertyStateStore"]


logger = structlog.get_logger(__name__)


# Column order is preserved across SELECT / INSERT / UPDATE so
# the row-builder helper :func:`_row_to_state` reads from any
# of them by name (asyncpg ``Record`` is mapping-like).  The
# order also matches the migration column declarations top to
# bottom for diff-ability against ``034_property_state.sql``.
_COLUMNS: Final[tuple[str, ...]] = (
    "property_channel_id",
    "customer_id",
    "org_id",
    "provider_type",
    "status",
    "current_job_id",
    "intent_dedup_key",
    "conversations_loaded",
    "cases_extracted",
    "rules_emitted",
    "profile_built",
    "window_days",
    "first_seen_at",
    "last_bootstrap_at",
    "last_data_event_at",
    "last_error",
    "retry_count",
    "updated_at",
)

_SELECT_COLUMNS: Final[str] = ", ".join(_COLUMNS)


_SELECT_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} "
    "FROM property_state "
    "WHERE property_channel_id = $1"
)


# ``ON CONFLICT DO NOTHING RETURNING`` returns the inserted
# row when the insert wins and zero rows when an existing row
# blocked it.  The Python caller falls back to a plain SELECT
# in the second case so two concurrent callers both observe
# the winner — neither sees ``None``.
_INSERT_SQL: Final[str] = f"""
INSERT INTO property_state (
    property_channel_id,
    customer_id,
    org_id,
    provider_type,
    status,
    current_job_id,
    intent_dedup_key,
    conversations_loaded,
    cases_extracted,
    rules_emitted,
    profile_built,
    window_days,
    first_seen_at,
    last_bootstrap_at,
    last_data_event_at,
    last_error,
    retry_count,
    updated_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16, $17, $18
)
ON CONFLICT (property_channel_id) DO NOTHING
RETURNING {_SELECT_COLUMNS}
"""


# Full-row UPDATE: every column except the PK is overwritten.
# ``RETURNING`` gives the caller the post-image in one round
# trip; the absence of a returned row is how
# :class:`PropertyStateNotFoundError` is raised.
_UPDATE_SQL: Final[str] = f"""
UPDATE property_state SET
    customer_id          = $2,
    org_id               = $3,
    provider_type        = $4,
    status               = $5,
    current_job_id       = $6,
    intent_dedup_key     = $7,
    conversations_loaded = $8,
    cases_extracted      = $9,
    rules_emitted        = $10,
    profile_built        = $11,
    window_days          = $12,
    first_seen_at        = $13,
    last_bootstrap_at    = $14,
    last_data_event_at   = $15,
    last_error           = $16,
    retry_count          = $17,
    updated_at           = $18
WHERE property_channel_id = $1
RETURNING {_SELECT_COLUMNS}
"""


# Orphan reaper: one set-based UPDATE flips every stale ``queued`` /
# ``warming`` row to ``failed`` in a single round trip.  ``status`` on
# the RHS of the SET evaluates the pre-update value, so ``last_error``
# records which stage the row was orphaned in.  ``$1`` is the cutoff,
# ``$2`` the failed-transition timestamp.
_REAP_SQL: Final[str] = """
UPDATE property_state SET
    status         = 'failed',
    current_job_id = NULL,
    last_error     = 'reaped: orphaned ' || status,
    retry_count    = retry_count + 1,
    updated_at     = $2
WHERE status IN ('queued', 'warming')
  AND updated_at < $1
RETURNING property_channel_id
"""


# Stage 3 nightly stale-sweep candidate read (Track B): the ``primed``
# rows whose last successful warm predates the freshness cutoff.
# ``COALESCE(last_bootstrap_at, updated_at)`` is the "last warmed"
# anchor — a row kept fresh by the reactive consumer (which re-stamps
# ``last_bootstrap_at`` on every re-prime) never matches, and a legacy
# primed row with a NULL ``last_bootstrap_at`` still ages out on
# ``updated_at`` rather than being stranded by ``NULL < cutoff``
# (which evaluates to NULL/false).  Oldest-first so the most stale
# properties refresh first under the cap.  ``$1`` is the cutoff, ``$2``
# the row cap.  The ``status`` filter rides ``idx_ps_status``.
_LIST_STALE_CANDIDATES_SQL: Final[str] = f"""
SELECT {_SELECT_COLUMNS}
FROM property_state
WHERE status = 'primed'
  AND COALESCE(last_bootstrap_at, updated_at) < $1
ORDER BY COALESCE(last_bootstrap_at, updated_at) ASC
LIMIT $2
"""


def _row_to_state(row: asyncpg.Record) -> PropertyState:
    """Map an asyncpg ``Record`` to :class:`PropertyState`.

    Lives at module scope so both :meth:`get` and the insert /
    update return paths share one mapping rule.  Centralising
    the mapping also means adding a column requires touching
    exactly three places: the migration, the
    :class:`PropertyState` dataclass, and this helper — easy
    to spot in review.
    """

    return PropertyState(
        property_channel_id=row["property_channel_id"],
        customer_id=row["customer_id"],
        org_id=row["org_id"],
        provider_type=row["provider_type"],
        status=row["status"],
        current_job_id=row["current_job_id"],
        intent_dedup_key=row["intent_dedup_key"],
        conversations_loaded=row["conversations_loaded"],
        cases_extracted=row["cases_extracted"],
        rules_emitted=row["rules_emitted"],
        profile_built=row["profile_built"],
        window_days=row["window_days"],
        first_seen_at=row["first_seen_at"],
        last_bootstrap_at=row["last_bootstrap_at"],
        last_data_event_at=row["last_data_event_at"],
        last_error=row["last_error"],
        retry_count=row["retry_count"],
        updated_at=row["updated_at"],
    )


def _state_to_params(state: PropertyState) -> tuple[object, ...]:
    """Flatten a :class:`PropertyState` into asyncpg positional args.

    Order matches the placeholders in :data:`_INSERT_SQL` and
    :data:`_UPDATE_SQL`.  Returned as ``tuple[object, ...]``
    so the caller can splat into ``conn.execute(sql, *params)``
    without an additional list copy.
    """

    return (
        state.property_channel_id,
        state.customer_id,
        state.org_id,
        state.provider_type,
        state.status,
        state.current_job_id,
        state.intent_dedup_key,
        state.conversations_loaded,
        state.cases_extracted,
        state.rules_emitted,
        state.profile_built,
        state.window_days,
        state.first_seen_at,
        state.last_bootstrap_at,
        state.last_data_event_at,
        state.last_error,
        state.retry_count,
        state.updated_at,
    )


class PostgresPropertyStateStore:
    """Postgres-backed store against migration ``034``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(
        self,
        property_channel_id: str,
    ) -> PropertyState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_SQL, property_channel_id,
            )
        if row is None:
            return None
        return _row_to_state(row)

    async def create_if_absent(
        self,
        state: PropertyState,
    ) -> PropertyState:
        params = _state_to_params(state)
        async with self._pool.acquire() as conn:
            inserted = await conn.fetchrow(_INSERT_SQL, *params)
            if inserted is not None:
                logger.debug(
                    "property_state.create_if_absent.inserted",
                    property_channel_id=state.property_channel_id,
                    customer_id=state.customer_id,
                    status=state.status,
                )
                return _row_to_state(inserted)
            # Concurrent caller won the insert race — read what
            # they wrote.  The SELECT must succeed because the
            # row demonstrably exists (we just collided with
            # it); a missing row here is a serious invariant
            # violation worth raising rather than papering over.
            existing = await conn.fetchrow(
                _SELECT_SQL, state.property_channel_id,
            )
        if existing is None:
            raise RuntimeError(
                "property_state.create_if_absent: insert "
                "conflicted but follow-up SELECT returned no "
                f"row for {state.property_channel_id!r}",
            )
        return _row_to_state(existing)

    async def update(
        self,
        state: PropertyState,
    ) -> PropertyState:
        params = _state_to_params(state)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_UPDATE_SQL, *params)
        if row is None:
            raise PropertyStateNotFoundError(
                state.property_channel_id,
            )
        logger.debug(
            "property_state.update",
            property_channel_id=state.property_channel_id,
            status=state.status,
        )
        return _row_to_state(row)

    async def reap_orphaned(
        self,
        *,
        cutoff: datetime,
        now: datetime | None = None,
    ) -> list[str]:
        now_ts = now or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_REAP_SQL, cutoff, now_ts)
        reaped = [row["property_channel_id"] for row in rows]
        if reaped:
            logger.info(
                "property_state.reaped_orphaned",
                count=len(reaped),
                cutoff=cutoff.isoformat(),
            )
        return reaped

    async def list_stale_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[PropertyState]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_LIST_STALE_CANDIDATES_SQL, cutoff, limit)
        candidates = [_row_to_state(row) for row in rows]
        if candidates:
            logger.info(
                "property_state.stale_candidates",
                count=len(candidates),
                cutoff=cutoff.isoformat(),
            )
        return candidates
