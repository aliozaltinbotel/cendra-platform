"""Postgres-backed persistence for :class:`InterviewAnswer` records.

Production implementation of the :class:`InterviewAnswerStore` Protocol
declared in :mod:`brain_engine.interview.store`.  Uses ``asyncpg`` and
``ON CONFLICT (property_id, qid) DO UPDATE`` so :meth:`put` is the
single primitive for both first-time capture and re-answer overwrites.

Schema contract — table ``interview_answers`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``004_interview_answers``).

The store is stateless apart from the injected connection pool; callers
own the pool's lifecycle unless the store was built via :meth:`from_url`,
in which case :meth:`close` releases it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.interview.models import AnswerSource, InterviewAnswer

if TYPE_CHECKING:
    import asyncpg


__all__ = ["PgInterviewAnswerStore", "create_interview_pool"]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_UPSERT_SQL: Final[str] = """
INSERT INTO interview_answers (
    property_id,
    qid,
    answer_text,
    source,
    answered_by,
    answered_at
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (property_id, qid) DO UPDATE SET
    answer_text = EXCLUDED.answer_text,
    source      = EXCLUDED.source,
    answered_by = EXCLUDED.answered_by,
    answered_at = EXCLUDED.answered_at
"""

_SELECT_COLUMNS: Final[str] = (
    "property_id, qid, answer_text, source, answered_by, answered_at"
)

_SELECT_BY_KEY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM interview_answers "  # noqa: S608
    "WHERE property_id = $1 AND qid = $2"
)

_SELECT_BY_PROPERTY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM interview_answers "  # noqa: S608
    "WHERE property_id = $1 ORDER BY answered_at DESC"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def create_interview_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool sized for the interview store.

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


def _answer_to_params(answer: InterviewAnswer) -> tuple[Any, ...]:
    """Flatten an :class:`InterviewAnswer` into upsert parameters.

    Order and count mirror :data:`_UPSERT_SQL`.
    """
    return (
        answer.property_id,
        answer.qid,
        answer.answer_text,
        answer.source.value,
        answer.answered_by,
        answer.answered_at,
    )


def _row_to_answer(row: dict[str, Any]) -> InterviewAnswer:
    """Hydrate an :class:`InterviewAnswer` from a raw Postgres row."""
    return InterviewAnswer(
        property_id=row["property_id"],
        qid=row["qid"],
        answer_text=row["answer_text"],
        source=AnswerSource(row["source"]),
        answered_at=_as_datetime(row["answered_at"]),
        answered_by=row.get("answered_by") or "pm",
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to :class:`datetime`."""
    if isinstance(value, datetime):
        return value
    raise TypeError(
        f"expected datetime for answered_at, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgInterviewAnswerStore:
    """Postgres-backed :class:`InterviewAnswerStore` implementation.

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
        self._log = logger.bind(component="pg_interview_answer_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgInterviewAnswerStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_interview_pool(
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

    # ── InterviewAnswerStore Protocol ─────────────────────── #

    async def get(
        self,
        *,
        property_id: str,
        qid: str,
    ) -> InterviewAnswer | None:
        """Return the stored answer, or ``None`` when absent."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_KEY_SQL, property_id, qid,
            )
        if row is None:
            return None
        return _row_to_answer(dict(row))

    async def put(self, answer: InterviewAnswer) -> None:
        """Upsert an answer keyed by ``(property_id, qid)``."""
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *_answer_to_params(answer))
        self._log.debug(
            "answer_saved",
            property_id=answer.property_id,
            qid=answer.qid,
            source=answer.source.value,
        )

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[InterviewAnswer]:
        """Return every stored answer for a property."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_PROPERTY_SQL, property_id)
        return [_row_to_answer(dict(row)) for row in rows]
