"""Postgres-backed persistence for :class:`PmFact` rows.

Production implementation of the :class:`PmFactStore` Protocol
declared in :mod:`brain_engine.conversation.pm_facts.store`.  Uses
``asyncpg`` directly — no ORM — and relies on a partial unique
index defined in migration ``013_property_pm_facts`` to make
:meth:`add_fact` idempotent under replays.

Schema contract — table ``property_pm_facts``:

* ``id``                BIGSERIAL primary key.
* ``customer_id``       TEXT, NOT NULL.
* ``org_id``            TEXT, NOT NULL DEFAULT ``''``.
* ``property_channel_id`` TEXT, NOT NULL DEFAULT ``''`` — empty
  string means "customer-wide".  Modelled as TEXT (not nullable)
  so the natural-key unique index is straightforward.
* ``fact_text``         TEXT, NOT NULL.
* ``source_message_id`` TEXT, NOT NULL DEFAULT ``''``.
* ``created_at``        TIMESTAMPTZ, NOT NULL DEFAULT now().

The store owns no state apart from the asyncpg pool; lifespan
ownership is signalled by ``owns_pool`` mirroring
:class:`PgPropertyProfileStore` and :class:`PgUnansweredThreadStore`
so the bootstrap glue stays uniform.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.conversation.pm_facts.models import PmFact

if TYPE_CHECKING:
    import asyncpg


__all__ = ["PgPmFactStore", "create_pm_facts_pool"]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_INSERT_SQL: Final[str] = """
INSERT INTO property_pm_facts (
    customer_id,
    org_id,
    property_channel_id,
    fact_text,
    source_message_id,
    created_at
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT ON CONSTRAINT uq_property_pm_facts_natural_key
DO NOTHING
"""

_SELECT_FOR_PROPERTY_SQL: Final[str] = """
SELECT
    customer_id,
    org_id,
    property_channel_id,
    fact_text,
    source_message_id,
    created_at
FROM property_pm_facts
WHERE customer_id = $1
  AND property_channel_id IN ($2, '')
ORDER BY created_at DESC
"""

# Temporal-recall variant — adds a ``created_at <= $3`` cut-off so
# the live-chat path remains untouched while the
# ``GET /api/v1/properties/{id}/memory?as_of=...`` endpoint can
# answer "what was the wifi password on April 28?" deterministically.
_SELECT_FOR_PROPERTY_AS_OF_SQL: Final[str] = """
SELECT
    customer_id,
    org_id,
    property_channel_id,
    fact_text,
    source_message_id,
    created_at
FROM property_pm_facts
WHERE customer_id = $1
  AND property_channel_id IN ($2, '')
  AND created_at <= $3
ORDER BY created_at DESC
"""

_DELETE_FOR_PROPERTY_SQL: Final[str] = (
    "DELETE FROM property_pm_facts "
    "WHERE customer_id = $1 AND property_channel_id = $2"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def create_pm_facts_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool sized for the PM-fact store.

    Manager corrections arrive at human cadence (one per missed
    answer) so a narrow pool is more than enough and keeps the
    cendra-pg-app secret's connection budget intact.
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


def _row_to_fact(row: dict[str, Any]) -> PmFact:
    """Hydrate a :class:`PmFact` from a raw Postgres row."""
    return PmFact(
        customer_id=str(row["customer_id"] or ""),
        org_id=str(row["org_id"] or ""),
        property_channel_id=str(row["property_channel_id"] or ""),
        fact_text=str(row["fact_text"] or ""),
        source_message_id=str(row["source_message_id"] or ""),
        created_at=_as_datetime(row["created_at"]),
    )


def _as_datetime(value: Any) -> datetime:
    """Coerce a raw column value to an aware :class:`datetime`."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    raise TypeError(
        f"expected datetime, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgPmFactStore:
    """Postgres-backed :class:`PmFactStore` implementation.

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
        self._log = logger.bind(component="pg_pm_fact_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> PgPmFactStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_pm_facts_pool(
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

    # ── PmFactStore Protocol ──────────────────────────────────── #

    async def add_fact(self, fact: PmFact) -> None:
        """Insert one fact, deduplicating against the natural key."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_SQL,
                fact.customer_id,
                fact.org_id,
                fact.property_channel_id,
                fact.fact_text,
                fact.source_message_id,
                fact.created_at,
            )
        self._log.debug(
            "pm_fact_saved",
            customer_id=fact.customer_id,
            property_channel_id=fact.property_channel_id,
            fact_chars=len(fact.fact_text),
        )

    async def list_facts(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
        as_of: datetime | None = None,
    ) -> list[PmFact]:
        """Return facts for one (customer, property), newest first.

        Newest-first ordering is the supersede semantics the live-chat
        prompt relies on: when PM corrects an earlier answer the freshest
        fact appears at the top of the injected knowledge block, so the
        LLM weighs it ahead of stale entries on the same topic.

        Args:
            customer_id: Owning customer identifier.
            property_channel_id: Property scope; ``""`` returns
                customer-wide rows alongside listing-specific ones.
            as_of: Optional cut-off — when supplied, only rows with
                ``created_at <= as_of`` participate.  ``None``
                (default) preserves the live-chat contract; the
                temporal-recall endpoint forwards a parsed
                :class:`datetime` here to answer historical
                "what was the wifi password on April 28?" queries.
        """
        async with self._pool.acquire() as conn:
            if as_of is None:
                rows = await conn.fetch(
                    _SELECT_FOR_PROPERTY_SQL,
                    customer_id,
                    property_channel_id,
                )
            else:
                rows = await conn.fetch(
                    _SELECT_FOR_PROPERTY_AS_OF_SQL,
                    customer_id,
                    property_channel_id,
                    as_of,
                )
        return [_row_to_fact(dict(row)) for row in rows]

    async def clear_property(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
    ) -> None:
        """Drop every fact for one property scope."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                _DELETE_FOR_PROPERTY_SQL,
                customer_id,
                property_channel_id,
            )
        self._log.debug(
            "pm_facts_cleared",
            customer_id=customer_id,
            property_channel_id=property_channel_id,
        )
