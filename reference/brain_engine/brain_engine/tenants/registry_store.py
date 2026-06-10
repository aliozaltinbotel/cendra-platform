"""Persistence layer for the property → tenant registry.

The Protocol describes the minimal surface a resolver needs:

* :meth:`get` — point lookup by ``property_channel_id`` for the
  hot path (every Sandbox UI message ends up here).
* :meth:`upsert` — write a new mapping or refresh an existing one,
  recording where the row came from via the ``source`` field of
  :class:`brain_engine.tenants.models.TenantContext`.

Two implementations ship in this module:

* :class:`InMemoryPropertyTenantRegistry` — used by unit tests
  and by the in-process default before a Postgres pool is wired.
* :class:`PostgresPropertyTenantRegistry` — backed by the
  ``property_tenant_registry`` table from migration ``032``.
  Uses ``ON CONFLICT (property_channel_id) DO UPDATE`` so the
  upsert is genuinely idempotent under concurrent writers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol

import structlog

from brain_engine.tenants.models import TenantContext

if TYPE_CHECKING:
    import asyncpg

__all__ = [
    "InMemoryPropertyTenantRegistry",
    "PostgresPropertyTenantRegistry",
    "PropertyTenantRegistry",
]


logger = structlog.get_logger(__name__)


_SELECT_SQL: Final[str] = (
    "SELECT customer_id, org_id, provider_type, source "
    "FROM property_tenant_registry "
    "WHERE property_channel_id = $1"
)

_UPSERT_SQL: Final[str] = """
INSERT INTO property_tenant_registry (
    property_channel_id,
    customer_id,
    org_id,
    provider_type,
    source,
    first_seen_at,
    updated_at
)
VALUES ($1, $2, $3, $4, $5, now(), now())
ON CONFLICT (property_channel_id) DO UPDATE SET
    customer_id   = EXCLUDED.customer_id,
    org_id        = EXCLUDED.org_id,
    provider_type = EXCLUDED.provider_type,
    source        = EXCLUDED.source,
    updated_at    = now()
"""

_SELECT_LAST_ATTEMPT_SQL: Final[str] = (
    "SELECT last_auto_attempted_at FROM property_tenant_registry "
    "WHERE property_channel_id = $1"
)

_TOUCH_LAST_ATTEMPT_SQL: Final[str] = (
    "UPDATE property_tenant_registry "
    "SET last_auto_attempted_at = now() "
    "WHERE property_channel_id = $1"
)

_DISTINCT_CUSTOMERS_SQL: Final[str] = (
    "SELECT DISTINCT customer_id FROM property_tenant_registry"
)


class PropertyTenantRegistry(Protocol):
    """Read/write contract for the property → tenant table."""

    async def get(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        """Return the stored tenant for ``property_channel_id``."""
        ...

    async def upsert(self, context: TenantContext) -> None:
        """Create or refresh the row for ``context``."""
        ...

    async def get_last_auto_attempt(
        self,
        property_channel_id: str,
    ) -> datetime | None:
        """Return when Phase 4 last fired for this property.

        ``None`` means the trigger has never attempted to prime
        this property (or the row does not yet exist).
        """
        ...

    async def record_auto_attempt(self, property_channel_id: str) -> None:
        """Stamp ``last_auto_attempted_at = now()`` on the row.

        The trigger calls this after every fire (success OR
        failure) so a property that consistently bootstraps with
        no data does not retrigger on every request.
        """
        ...

    async def distinct_customers(self) -> list[str]:
        """Return every ``customer_id`` known to the registry.

        Used by the Phase 5 :class:`GraphQLLazyProbe` to enumerate
        candidate tenants when probing an unknown property.  The
        order is implementation-defined and may include the same
        customer twice across calls (callers should not rely on
        ordering or uniqueness beyond a best-effort de-dupe).
        """
        ...


class InMemoryPropertyTenantRegistry:
    """Process-local registry used by tests and the default pod."""

    def __init__(self) -> None:
        self._rows: dict[str, TenantContext] = {}
        self._last_attempts: dict[str, datetime] = {}

    async def get(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        return self._rows.get(property_channel_id)

    async def upsert(self, context: TenantContext) -> None:
        self._rows[context.property_channel_id] = context

    async def get_last_auto_attempt(
        self,
        property_channel_id: str,
    ) -> datetime | None:
        return self._last_attempts.get(property_channel_id)

    async def record_auto_attempt(self, property_channel_id: str) -> None:
        # Imported lazily so the registry module stays free of the
        # ``timezone`` import on hot paths that never touch this
        # method (most tests + the legacy single-tenant pod).
        from datetime import datetime as _dt

        self._last_attempts[property_channel_id] = _dt.now(UTC)

    async def distinct_customers(self) -> list[str]:
        return sorted({ctx.customer_id for ctx in self._rows.values()})


class PostgresPropertyTenantRegistry:
    """Postgres-backed registry against migration ``032``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, property_channel_id)
        if row is None:
            return None
        return TenantContext(
            customer_id=row["customer_id"],
            org_id=row["org_id"],
            provider_type=row["provider_type"],
            property_channel_id=property_channel_id,
            source=row["source"],
        )

    async def upsert(self, context: TenantContext) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                context.property_channel_id,
                context.customer_id,
                context.org_id,
                context.provider_type,
                context.source,
            )
        logger.debug(
            "tenant_registry.upsert",
            property_channel_id=context.property_channel_id,
            customer_id=context.customer_id,
            provider_type=context.provider_type,
            source=context.source,
        )

    async def get_last_auto_attempt(
        self,
        property_channel_id: str,
    ) -> datetime | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_LAST_ATTEMPT_SQL, property_channel_id,
            )
        if row is None:
            return None
        return row["last_auto_attempted_at"]

    async def record_auto_attempt(self, property_channel_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _TOUCH_LAST_ATTEMPT_SQL, property_channel_id,
            )

    async def distinct_customers(self) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_DISTINCT_CUSTOMERS_SQL)
        return [row["customer_id"] for row in rows if row["customer_id"]]
