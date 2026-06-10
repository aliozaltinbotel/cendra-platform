"""Postgres-backed persistence for :class:`WorkflowAutonomy` records.

Production implementation of the :class:`AutonomyStore` Protocol declared
in :mod:`brain_engine.autonomy.engine`.  Mirrors the ``PgBlockerStore``
pattern: an injected ``asyncpg`` pool, ``from_url`` /``owns_pool`` /
``close`` lifecycle helpers, and a single ``ON CONFLICT DO UPDATE`` that
serves both the first write and every subsequent transition without the
caller having to distinguish them.

Schema contract — table ``workflow_autonomy`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``003_workflow_autonomy.sql``).

The store is stateless apart from the injected pool; parameters are
passed positionally (no SQL string interpolation of user data) and the
``(property_id, workflow)`` primary key carries the upsert.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
)

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_UPSERT_SQL: Final[str] = """
INSERT INTO workflow_autonomy (
    property_id,
    workflow,
    state,
    sample_size,
    success_rate,
    override_rate,
    incidents,
    mean_latency_seconds,
    hold_seconds,
    changed_at,
    changed_by,
    reason
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
)
ON CONFLICT (property_id, workflow) DO UPDATE SET
    state                = EXCLUDED.state,
    sample_size          = EXCLUDED.sample_size,
    success_rate         = EXCLUDED.success_rate,
    override_rate        = EXCLUDED.override_rate,
    incidents            = EXCLUDED.incidents,
    mean_latency_seconds = EXCLUDED.mean_latency_seconds,
    hold_seconds         = EXCLUDED.hold_seconds,
    changed_at           = EXCLUDED.changed_at,
    changed_by           = EXCLUDED.changed_by,
    reason               = EXCLUDED.reason
"""

_SELECT_COLUMNS: Final[str] = (
    "property_id, workflow, state, sample_size, success_rate, "
    "override_rate, incidents, mean_latency_seconds, hold_seconds, "
    "changed_at, changed_by, reason"
)

_SELECT_BY_KEY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM workflow_autonomy "  # noqa: S608
    "WHERE property_id = $1 AND workflow = $2"
)

_SELECT_BY_PROPERTY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM workflow_autonomy "  # noqa: S608
    "WHERE property_id = $1 "
    "ORDER BY workflow ASC"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def create_autonomy_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool sized for autonomy reads/writes.

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


def _record_to_params(record: WorkflowAutonomy) -> tuple[Any, ...]:
    """Flatten a :class:`WorkflowAutonomy` into upsert parameters.

    Order and count mirror :data:`_UPSERT_SQL`.
    """
    metrics = record.metrics
    return (
        record.property_id,
        record.workflow,
        record.state.value,
        metrics.sample_size,
        float(metrics.success_rate),
        float(metrics.override_rate),
        metrics.incidents,
        float(metrics.mean_latency_seconds),
        record.hold_seconds,
        record.changed_at,
        record.changed_by,
        record.reason,
    )


def _row_to_record(row: dict[str, Any]) -> WorkflowAutonomy:
    """Hydrate a :class:`WorkflowAutonomy` from a raw Postgres row."""
    metrics = WorkflowMetrics(
        sample_size=int(row["sample_size"]),
        success_rate=float(row["success_rate"]),
        override_rate=float(row["override_rate"]),
        incidents=int(row["incidents"]),
        mean_latency_seconds=float(row["mean_latency_seconds"]),
    )
    return WorkflowAutonomy(
        property_id=row["property_id"],
        workflow=row["workflow"],
        state=AutonomyState(row["state"]),
        metrics=metrics,
        hold_seconds=int(row["hold_seconds"]),
        changed_at=_as_datetime(row["changed_at"]),
        changed_by=row["changed_by"],
        reason=row["reason"],
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to :class:`datetime`.

    asyncpg returns TIMESTAMPTZ as :class:`datetime` natively; tests may
    feed a pre-formatted dict, so the helper stays defensive.
    """
    if isinstance(value, datetime):
        return value
    raise TypeError(
        f"expected datetime for changed_at, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgAutonomyStore:
    """Postgres-backed :class:`AutonomyStore` implementation.

    Satisfies the Protocol defined in
    :mod:`brain_engine.autonomy.engine` without inheritance — structural
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
        self._log = logger.bind(component="pg_autonomy_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgAutonomyStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_autonomy_pool(
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

    # ── AutonomyStore Protocol ─────────────────────────────── #

    async def get(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy | None:
        """Return the stored record, or ``None`` when absent."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_KEY_SQL,
                property_id,
                workflow,
            )
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def put(self, record: WorkflowAutonomy) -> None:
        """Persist (upsert) a record keyed by property+workflow."""
        params = _record_to_params(record)
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *params)
        self._log.debug(
            "autonomy_saved",
            property_id=record.property_id,
            workflow=record.workflow,
            state=record.state.value,
            sample_size=record.metrics.sample_size,
        )

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        """Return every workflow record for a property."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _SELECT_BY_PROPERTY_SQL,
                property_id,
            )
        return [_row_to_record(dict(row)) for row in rows]
