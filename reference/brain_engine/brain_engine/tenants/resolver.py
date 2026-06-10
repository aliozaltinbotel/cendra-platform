"""Resolve a :class:`TenantContext` from a ``property_channel_id``.

The resolver layers four cascading sources, picking the first hit:

1. **In-process LRU cache** — eliminates Postgres round-trips for
   hot properties (Sandbox UI typically holds one property
   selected for many seconds while the operator interacts).
2. **Registry table** (Postgres) — the canonical store populated
   by bootstrap, nightly sync, and manual backfill.
3. **Lazy GraphQL probe** — optional injectable that searches the
   unified-data gateway for a property the registry has never
   seen.  On hit, the result is written back to the registry so
   subsequent requests skip the probe.
4. **Env default** — final fallback to the pod-level
   ``UNIFIED_DATA_*`` environment variables.  Logged at WARN so
   operators can audit "fell through to env default" rates and
   alert when they spike (signals a registry gap).

The cache is an OrderedDict (insertion-order LRU) bounded at
10 000 entries — a defensive sanity cap; in practice the working
set is a few hundred concurrent properties per pod.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Final

import structlog

from brain_engine.tenants.models import (
    TENANT_SOURCE_LAZY,
    TENANT_SOURCE_REGISTRY,
    TenantContext,
)
from brain_engine.tenants.registry_store import PropertyTenantRegistry

__all__ = [
    "EnvDefaultFactory",
    "LazyTenantProbe",
    "TenantResolver",
]


logger = structlog.get_logger(__name__)


_DEFAULT_CACHE_CAPACITY: Final[int] = 10_000


#: Async callable that probes the unified-data GraphQL gateway for
#: a property and returns a fresh :class:`TenantContext`, or
#: ``None`` when the property is unknown.  The returned context's
#: ``source`` MUST be :data:`TENANT_SOURCE_LAZY`.
LazyTenantProbe = Callable[[str], Awaitable[TenantContext | None]]


#: Sync callable that produces the pod-level env default context
#: for an unknown ``property_channel_id``.  Kept injectable so
#: tests don't need to set environment variables.
EnvDefaultFactory = Callable[[str], TenantContext]


class TenantResolver:
    """Cascading resolver: cache → registry → lazy probe → env."""

    def __init__(
        self,
        registry: PropertyTenantRegistry,
        env_default_factory: EnvDefaultFactory,
        lazy_probe: LazyTenantProbe | None = None,
        cache_capacity: int = _DEFAULT_CACHE_CAPACITY,
    ) -> None:
        self._registry = registry
        self._env_default_factory = env_default_factory
        self._lazy_probe = lazy_probe
        self._cache_capacity = max(1, cache_capacity)
        self._cache: OrderedDict[str, TenantContext] = OrderedDict()

    async def resolve(
        self,
        property_channel_id: str,
    ) -> TenantContext:
        """Return the :class:`TenantContext` for the property."""

        cached = self._cache_get(property_channel_id)
        if cached is not None:
            return cached

        registry_hit = await self._registry.get(property_channel_id)
        if registry_hit is not None:
            normalised = self._with_source(
                registry_hit, TENANT_SOURCE_REGISTRY,
            )
            self._cache_put(normalised)
            return normalised

        if self._lazy_probe is not None:
            lazy_hit = await self._lazy_probe(property_channel_id)
            if lazy_hit is not None:
                normalised = self._with_source(
                    lazy_hit, TENANT_SOURCE_LAZY,
                )
                await self._registry.upsert(normalised)
                self._cache_put(normalised)
                return normalised

        env_default = self._env_default_factory(property_channel_id)
        logger.warning(
            "tenant_resolver.env_default_fallback",
            property_channel_id=property_channel_id,
            customer_id=env_default.customer_id,
            provider_type=env_default.provider_type,
        )
        # Env-default results are NOT cached: a later bootstrap
        # call must be able to override them on the very next
        # request without waiting for the LRU to evict the stale
        # entry.
        return env_default

    def invalidate(self, property_channel_id: str) -> None:
        """Drop ``property_channel_id`` from the in-process cache.

        Bootstrap calls invoke this after upserting the registry
        so the next request observes the fresh row.
        """

        self._cache.pop(property_channel_id, None)

    async def record(self, context: TenantContext) -> None:
        """Persist ``context`` to the registry and refresh the cache.

        Bootstrap endpoints call this after a successful run so the
        property → tenant mapping survives a pod restart.  The
        cache entry is refreshed (not just invalidated) so the very
        next request observes the new mapping without paying for a
        Postgres round-trip.

        The Postgres row records the *origin* (``bootstrap`` /
        ``sync`` / ``lazy`` / ``manual``) carried by ``context``;
        the cached :class:`TenantContext` is normalised to
        :data:`TENANT_SOURCE_REGISTRY` so it matches what a later
        registry-hit resolution would produce.  Without that
        normalisation observability would see the very first
        request return ``'bootstrap'`` while every subsequent one
        returns ``'registry'``, which is confusing.
        """

        await self._registry.upsert(context)
        cached = self._with_source(context, TENANT_SOURCE_REGISTRY)
        self._cache_put(cached)

    def _cache_get(
        self,
        property_channel_id: str,
    ) -> TenantContext | None:
        context = self._cache.get(property_channel_id)
        if context is not None:
            self._cache.move_to_end(property_channel_id)
        return context

    def _cache_put(self, context: TenantContext) -> None:
        key = context.property_channel_id
        self._cache[key] = context
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_capacity:
            self._cache.popitem(last=False)

    @staticmethod
    def _with_source(
        context: TenantContext,
        source: str,
    ) -> TenantContext:
        if context.source == source:
            return context
        return TenantContext(
            customer_id=context.customer_id,
            org_id=context.org_id,
            provider_type=context.provider_type,
            property_channel_id=context.property_channel_id,
            source=source,
        )
