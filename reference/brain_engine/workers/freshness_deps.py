"""Dependency assembly for the out-of-process freshness consumer.

Far lighter than :mod:`workers.bootstrap_deps`: the freshness consumer is
a pure *producer* — it marks properties stale and enqueues refresh
intents onto the same ``bootstrap-intents`` queue — so it needs only a
:class:`PropertyStateStore` and a :class:`ServiceBusBootstrapDispatcher`.
No :class:`BootstrapRunner`, pipeline, memory, or foundation deps: the
Stage 2 worker still owns the heavy bootstrap execution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

import asyncpg
import structlog

from brain_engine.integrations.service_bus import (
    BOOTSTRAP_QUEUE,
    ServiceBusQueueSender,
)
from brain_engine.tenants import PostgresPropertyStateStore, StaleSweeper
from brain_engine.tenants.service_bus_dispatcher import (
    ServiceBusBootstrapDispatcher,
)
from workers.freshness_message_handler import FreshnessMessageHandler

logger = structlog.get_logger(__name__)

_DEFAULT_WINDOW_DAYS = 7
_DEFAULT_STALE_TTL_DAYS = 14
_DEFAULT_STALE_SWEEP_LIMIT = 100


def _database_url() -> str:
    """Resolve the Postgres URL from the consumer environment."""
    url = os.getenv("TENANT_REGISTRY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "TENANT_REGISTRY_DATABASE_URL or DATABASE_URL must be set"
        )
    return url


def _service_bus_connection() -> str:
    """Resolve the Service Bus connection string from the environment."""
    conn = os.getenv("AZURE_SERVICEBUS_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("AZURE_SERVICEBUS_CONNECTION_STRING must be set")
    return conn


def _int_env(name: str, default: int) -> int:
    """Read a positive int env var, falling back to ``default``."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _bool_env(name: str, default: bool) -> bool:
    """Read a truthy env var (``1``/``true``/``yes``/``on``)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class FreshnessHandle:
    """The handler plus the resources to close on shutdown."""

    handler: FreshnessMessageHandler
    pool: asyncpg.Pool
    sender: ServiceBusQueueSender

    async def aclose(self) -> None:
        """Close pooled resources in reverse dependency order."""
        await self.sender.aclose()
        await self.pool.close()


async def build_freshness_handler() -> FreshnessHandle:
    """Assemble the freshness handler from consumer env vars."""
    pool = await asyncpg.create_pool(_database_url(), min_size=1, max_size=4)
    state_store = PostgresPropertyStateStore(pool)
    sender = ServiceBusQueueSender(
        connection_string=_service_bus_connection(),
        queue_name=BOOTSTRAP_QUEUE,
    )
    dispatcher = ServiceBusBootstrapDispatcher(sender)
    window_days = int(
        os.getenv("FRESHNESS_REFRESH_WINDOW_DAYS", str(_DEFAULT_WINDOW_DAYS))
    )
    handler = FreshnessMessageHandler(
        state_store=state_store,
        dispatcher=dispatcher,
        window_days=window_days,
    )
    logger.info("freshness_consumer.deps_built", window_days=window_days)
    return FreshnessHandle(handler=handler, pool=pool, sender=sender)


@dataclass(slots=True)
class SweeperHandle:
    """The stale-sweeper plus the resources to close on shutdown."""

    sweeper: StaleSweeper
    pool: asyncpg.Pool
    sender: ServiceBusQueueSender

    async def aclose(self) -> None:
        """Close pooled resources in reverse dependency order."""
        await self.sender.aclose()
        await self.pool.close()


async def build_stale_sweeper() -> SweeperHandle:
    """Assemble the Stage 3 stale-sweeper from CronJob env vars.

    Shares the reactive consumer's lightweight deps — a Postgres
    ``property_state`` pool and a Service Bus dispatcher onto the
    ``bootstrap-intents`` queue — because the sweep is the same kind of
    pure producer.  ``FRESHNESS_SWEEP_DRY_RUN`` defaults ``true`` so the
    first deploy logs candidate counts without enqueuing.
    """
    pool = await asyncpg.create_pool(_database_url(), min_size=1, max_size=4)
    state_store = PostgresPropertyStateStore(pool)
    sender = ServiceBusQueueSender(
        connection_string=_service_bus_connection(),
        queue_name=BOOTSTRAP_QUEUE,
    )
    dispatcher = ServiceBusBootstrapDispatcher(sender)
    ttl_days = _int_env("FRESHNESS_STALE_TTL_DAYS", _DEFAULT_STALE_TTL_DAYS)
    limit = _int_env("FRESHNESS_STALE_SWEEP_LIMIT", _DEFAULT_STALE_SWEEP_LIMIT)
    window_days = _int_env("FRESHNESS_STALE_REFRESH_WINDOW_DAYS", ttl_days)
    dry_run = _bool_env("FRESHNESS_SWEEP_DRY_RUN", True)
    sweeper = StaleSweeper(
        state_store,
        dispatcher,
        ttl=timedelta(days=ttl_days),
        limit=limit,
        window_days=window_days,
        dry_run=dry_run,
    )
    logger.info(
        "freshness_sweep.deps_built",
        ttl_days=ttl_days,
        limit=limit,
        window_days=window_days,
        dry_run=dry_run,
    )
    return SweeperHandle(sweeper=sweeper, pool=pool, sender=sender)
