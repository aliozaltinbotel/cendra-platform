"""Lifespan wiring for the Phase 3 tenant resolver.

Constructs a :class:`brain_engine.tenants.TenantResolver` backed by
the ``property_tenant_registry`` Postgres table (migration ``032``)
and publishes it into the process-wide runtime holder so the
already-mounted :class:`TenantResolverMiddleware` starts populating
the :func:`current_tenant` ContextVar for every request.

Phase 3 is fully opt-in: the resolver is built only when
``TENANT_RESOLVER_ENABLED`` is set to a truthy value
(``1`` / ``true`` / ``yes`` / ``on``).  Anywhere else in the
codebase the middleware stays a no-op pass-through, which keeps
existing single-tenant pods byte-for-byte identical to the
pre-Phase-3 behaviour.

Backends:

* ``memory`` (default) — in-process registry, ideal for unit
  tests, local dev shells, and the legacy single-tenant pod where
  the env defaults are correct for every request anyway.
* ``postgres`` — :class:`PostgresPropertyTenantRegistry` against
  the table from migration ``032``.  URL falls back to
  ``DATABASE_URL`` so the same conn-string the rest of the
  application uses applies here too.

Returns ``(resolver, registry, state_store, close)`` where
``close`` is an async callable that ``lifespan`` should await
during shutdown to release the asyncpg pool (or ``None`` for the
in-memory backend).

``state_store`` is the Stage 1 ``property_state`` SSoT
(:class:`PropertyStateStore`).  It is built only when
``PROPERTY_STATE_ENABLED`` is truthy, so this wiring stays inert
until the flag flips: with the flag off the store is ``None`` and
the Phase 4 trigger keeps its legacy ``asyncio.create_task`` path.
When enabled it shares the registry's asyncpg pool (Postgres
backend) or is an in-memory store (memory backend), so the single
``close`` contract still releases everything.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Final

import structlog

from brain_engine.tenants import (
    TENANT_SOURCE_ENV_DEFAULT,
    GraphQLLazyProbe,
    InMemoryPropertyStateStore,
    InMemoryPropertyTenantRegistry,
    PostgresPropertyStateStore,
    PostgresPropertyTenantRegistry,
    PropertyStateStore,
    PropertyTenantRegistry,
    TenantContext,
    TenantResolver,
    configure_tenant_resolver,
)

if False:  # TYPE_CHECKING guarded below to avoid import cycle at runtime
    pass

logger = structlog.get_logger(__name__)


_ENABLED_ENV: Final[str] = "TENANT_RESOLVER_ENABLED"
_PROPERTY_STATE_ENABLED_ENV: Final[str] = "PROPERTY_STATE_ENABLED"
_BACKEND_ENV: Final[str] = "TENANT_REGISTRY_BACKEND"
_DATABASE_URL_ENV: Final[str] = "TENANT_REGISTRY_DATABASE_URL"
_FALLBACK_URL_ENV: Final[str] = "DATABASE_URL"
_LAZY_PROBE_ENV: Final[str] = "TENANT_LAZY_PROBE_ENABLED"
_LAZY_PROBE_TTL_ENV: Final[str] = "TENANT_LAZY_PROBE_NEGATIVE_CACHE_MIN"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def tenant_resolver_enabled() -> bool:
    """Return ``True`` when Phase 3 auto-resolution is turned on."""
    raw = os.environ.get(_ENABLED_ENV, "").strip().lower()
    return raw in _TRUTHY


def property_state_enabled() -> bool:
    """Return ``True`` when the Stage 1 ``property_state`` SSoT is on.

    Default off.  While off, :func:`_build_registry` yields a
    ``None`` state store and every downstream consumer (Phase 4
    trigger, request-bootstrap endpoint) stays on its pre-Stage-1
    behaviour, so merging the wiring is a no-op until the flag
    flips.
    """
    raw = os.environ.get(_PROPERTY_STATE_ENABLED_ENV, "").strip().lower()
    return raw in _TRUTHY


async def wire_tenant_resolver(
    *,
    env_customer_id: str | None,
    env_org_id: str | None,
    env_provider_type: str | None,
    unified_data_client: object | None = None,
) -> tuple[
    TenantResolver | None,
    PropertyTenantRegistry | None,
    PropertyStateStore | None,
    Callable[[], Awaitable[None]] | None,
]:
    """Construct + publish the active :class:`TenantResolver`.

    Args:
        env_customer_id: Pod-default Cendra customer id (the
            same value the existing readers / loaders consume
            from ``UNIFIED_DATA_CUSTOMER_ID``).  When the resolver
            falls through to the env default this id is what the
            downstream pipeline sees.
        env_org_id: Pod-default org id (may be ``None``).
        env_provider_type: Pod-default provider type
            (``"HOSTAWAY"``, ``"LODGIFY"`` …).

    Returns:
        A ``(resolver, registry, state_store, close)`` tuple.  All
        four are ``None`` when Phase 3 is disabled; ``state_store``
        is additionally ``None`` when ``PROPERTY_STATE_ENABLED`` is
        off even though Phase 3 itself is on.
    """

    if not tenant_resolver_enabled():
        logger.info("tenant_resolver.disabled")
        return None, None, None, None

    registry, state_store, close = await _build_registry()
    env_default_factory = _make_env_default_factory(
        env_customer_id=env_customer_id or "",
        env_org_id=env_org_id,
        env_provider_type=env_provider_type or "",
    )
    lazy_probe = _build_lazy_probe(
        registry=registry,
        unified_data_client=unified_data_client,
        env_customer_id=env_customer_id or "",
    )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=env_default_factory,
        lazy_probe=lazy_probe,
    )
    configure_tenant_resolver(resolver)
    logger.info(
        "tenant_resolver.wired",
        backend=os.environ.get(_BACKEND_ENV, "memory").lower(),
        lazy_probe=lazy_probe is not None,
        property_state=state_store is not None,
    )
    return resolver, registry, state_store, close


def _build_lazy_probe(
    *,
    registry: PropertyTenantRegistry,
    unified_data_client: object | None,
    env_customer_id: str,
) -> GraphQLLazyProbe | None:
    enabled = (
        os.environ.get(_LAZY_PROBE_ENV, "").strip().lower() in _TRUTHY
    )
    if not enabled:
        return None
    if unified_data_client is None:
        logger.warning(
            "tenant_resolver.lazy_probe_skipped_no_graphql_client",
        )
        return None
    from datetime import timedelta

    ttl_minutes = _read_int_env(
        _LAZY_PROBE_TTL_ENV, default=10, minimum=1,
    )
    return GraphQLLazyProbe(
        client=unified_data_client,  # type: ignore[arg-type]
        customers_provider=registry.distinct_customers,
        extra_customers=(env_customer_id,) if env_customer_id else (),
        negative_cache_ttl=timedelta(minutes=ttl_minutes),
    )


def _read_int_env(name: str, *, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


async def _build_registry() -> tuple[
    PropertyTenantRegistry,
    PropertyStateStore | None,
    Callable[[], Awaitable[None]] | None,
]:
    state_enabled = property_state_enabled()
    backend = os.environ.get(_BACKEND_ENV, "memory").strip().lower()
    if backend != "postgres":
        state_store = InMemoryPropertyStateStore() if state_enabled else None
        return InMemoryPropertyTenantRegistry(), state_store, None

    url = (
        os.environ.get(_DATABASE_URL_ENV)
        or os.environ.get(_FALLBACK_URL_ENV)
    )
    if not url:
        logger.warning("tenant_resolver.postgres_no_url_fallback_in_memory")
        state_store = InMemoryPropertyStateStore() if state_enabled else None
        return InMemoryPropertyTenantRegistry(), state_store, None

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=url,
        min_size=1,
        max_size=5,
        command_timeout=10,
    )
    registry = PostgresPropertyTenantRegistry(pool)
    # The state store shares the registry's pool when enabled so a
    # single ``close()`` releases everything; ``None`` keeps the
    # Stage 1 SSoT dormant until ``PROPERTY_STATE_ENABLED`` flips.
    state_store = PostgresPropertyStateStore(pool) if state_enabled else None

    async def close() -> None:
        await pool.close()

    return registry, state_store, close


def _make_env_default_factory(
    *,
    env_customer_id: str,
    env_org_id: str | None,
    env_provider_type: str,
) -> Callable[[str], TenantContext]:
    def factory(property_channel_id: str) -> TenantContext:
        return TenantContext(
            customer_id=env_customer_id,
            org_id=env_org_id,
            provider_type=env_provider_type,
            property_channel_id=property_channel_id,
            source=TENANT_SOURCE_ENV_DEFAULT,
        )

    return factory
