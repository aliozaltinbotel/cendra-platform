"""Tests for the cascading :class:`TenantResolver`.

The resolver layers four sources, picking the first hit:

  1. In-process LRU cache
  2. Registry table
  3. Lazy GraphQL probe
  4. Env default factory

The contract under test:

* Registry hits short-circuit lazy probe / env default.
* Lazy hits write back to the registry so subsequent requests skip
  the probe.
* Env-default results are NOT cached so a later bootstrap upsert
  immediately overrides them.
* Cache eviction respects ``cache_capacity`` (LRU).
* :meth:`invalidate` removes a single entry; :meth:`record`
  refreshes both the registry and the cache.
* The cache always emits :data:`TENANT_SOURCE_REGISTRY` for
  registry hits even when the stored row recorded a different
  source — observability needs a single label for the hot path.
"""

from __future__ import annotations

from brain_engine.tenants import (
    TENANT_SOURCE_ENV_DEFAULT,
    TENANT_SOURCE_LAZY,
    TENANT_SOURCE_REGISTRY,
    InMemoryPropertyTenantRegistry,
    TenantContext,
    TenantResolver,
)


def _env_default_factory(property_channel_id: str) -> TenantContext:
    return TenantContext(
        customer_id="env_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id=property_channel_id,
        source=TENANT_SOURCE_ENV_DEFAULT,
    )


def _registry_with(*contexts: TenantContext) -> InMemoryPropertyTenantRegistry:
    registry = InMemoryPropertyTenantRegistry()
    for ctx in contexts:
        registry._rows[ctx.property_channel_id] = ctx  # type: ignore[attr-defined]
    return registry


async def test_falls_back_to_env_when_registry_empty_and_no_probe() -> None:
    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default_factory,
    )
    ctx = await resolver.resolve("unknown")
    assert ctx.source == TENANT_SOURCE_ENV_DEFAULT
    assert ctx.customer_id == "env_cust"


async def test_registry_hit_short_circuits_env_default() -> None:
    stored = TenantContext(
        customer_id="cust",
        org_id="org",
        provider_type="LODGIFY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    resolver = TenantResolver(
        registry=_registry_with(stored),
        env_default_factory=_env_default_factory,
    )
    ctx = await resolver.resolve("prop1")
    assert ctx.customer_id == "cust"
    assert ctx.provider_type == "LODGIFY"
    # source is normalised to TENANT_SOURCE_REGISTRY regardless of
    # the row's own ``source`` value so observability has a single
    # label for the hot path.
    assert ctx.source == TENANT_SOURCE_REGISTRY


async def test_registry_hit_is_cached() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(
        TenantContext(
            customer_id="cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="bootstrap",
        ),
    )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
    )
    first = await resolver.resolve("prop1")
    # Wipe the registry — the cache should still serve the row.
    registry._rows.clear()  # type: ignore[attr-defined]
    second = await resolver.resolve("prop1")
    assert first == second
    assert second.customer_id == "cust"


async def test_lazy_probe_used_when_registry_misses() -> None:
    calls: list[str] = []

    async def probe(property_channel_id: str) -> TenantContext:
        calls.append(property_channel_id)
        return TenantContext(
            customer_id="lazy_cust",
            org_id="lazy_org",
            provider_type="GUESTY",
            property_channel_id=property_channel_id,
            source=TENANT_SOURCE_LAZY,
        )

    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
        lazy_probe=probe,
    )
    ctx = await resolver.resolve("prop1")
    assert calls == ["prop1"]
    assert ctx.customer_id == "lazy_cust"
    # Lazy hits write back to the registry so the next hit skips.
    stored = await registry.get("prop1")
    assert stored is not None
    assert stored.customer_id == "lazy_cust"


async def test_lazy_probe_skipped_when_registry_already_has_row() -> None:
    probe_called = False

    async def probe(_: str) -> TenantContext:
        nonlocal probe_called
        probe_called = True
        raise AssertionError("probe should not be called")

    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(
        TenantContext(
            customer_id="cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="bootstrap",
        ),
    )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
        lazy_probe=probe,
    )
    await resolver.resolve("prop1")
    assert probe_called is False


async def test_lazy_probe_returning_none_falls_through_to_env() -> None:
    async def probe(_: str) -> TenantContext | None:
        return None

    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default_factory,
        lazy_probe=probe,
    )
    ctx = await resolver.resolve("prop1")
    assert ctx.source == TENANT_SOURCE_ENV_DEFAULT


async def test_env_default_results_are_not_cached() -> None:
    """A later bootstrap row must override env-default immediately."""
    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
    )
    first = await resolver.resolve("prop1")
    assert first.source == TENANT_SOURCE_ENV_DEFAULT
    await registry.upsert(
        TenantContext(
            customer_id="real",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="bootstrap",
        ),
    )
    second = await resolver.resolve("prop1")
    assert second.customer_id == "real"
    assert second.source == TENANT_SOURCE_REGISTRY


async def test_invalidate_drops_cached_entry() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(
        TenantContext(
            customer_id="cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="bootstrap",
        ),
    )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
    )
    await resolver.resolve("prop1")
    # Mutate the registry behind the resolver.
    await registry.upsert(
        TenantContext(
            customer_id="fresh",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="manual",
        ),
    )
    # Cache still has the old value.
    cached = await resolver.resolve("prop1")
    assert cached.customer_id == "cust"
    # After invalidation the fresh row is re-read.
    resolver.invalidate("prop1")
    fresh = await resolver.resolve("prop1")
    assert fresh.customer_id == "fresh"


async def test_record_writes_registry_and_refreshes_cache() -> None:
    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
    )
    ctx = TenantContext(
        customer_id="new",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    await resolver.record(ctx)
    assert await registry.get("prop1") == ctx
    resolved = await resolver.resolve("prop1")
    assert resolved.customer_id == "new"


async def test_cache_capacity_evicts_oldest() -> None:
    registry = InMemoryPropertyTenantRegistry()
    for i in range(3):
        await registry.upsert(
            TenantContext(
                customer_id=f"c{i}",
                org_id=None,
                provider_type="HOSTAWAY",
                property_channel_id=f"p{i}",
                source="bootstrap",
            ),
        )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
        cache_capacity=2,
    )
    await resolver.resolve("p0")
    await resolver.resolve("p1")
    await resolver.resolve("p2")
    # Drain p0 from the registry — only the cache could still serve.
    registry._rows.pop("p0")  # type: ignore[attr-defined]
    # p0 was evicted (LRU oldest) → resolver must hit env default.
    ctx = await resolver.resolve("p0")
    assert ctx.source == TENANT_SOURCE_ENV_DEFAULT


async def test_cache_capacity_lower_bound_is_one() -> None:
    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default_factory,
        cache_capacity=0,
    )
    assert resolver._cache_capacity == 1  # type: ignore[attr-defined]


async def test_recently_used_entry_is_kept_under_pressure() -> None:
    registry = InMemoryPropertyTenantRegistry()
    for i in range(3):
        await registry.upsert(
            TenantContext(
                customer_id=f"c{i}",
                org_id=None,
                provider_type="HOSTAWAY",
                property_channel_id=f"p{i}",
                source="bootstrap",
            ),
        )
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default_factory,
        cache_capacity=2,
    )
    await resolver.resolve("p0")
    await resolver.resolve("p1")
    # Touch p0 to make it the MRU.
    await resolver.resolve("p0")
    # Inserting p2 should evict p1, not p0.
    await resolver.resolve("p2")
    registry._rows.pop("p1")  # type: ignore[attr-defined]
    registry._rows.pop("p0")  # type: ignore[attr-defined]
    # p0 still in cache.
    ctx = await resolver.resolve("p0")
    assert ctx.customer_id == "c0"


async def test_resolver_uses_env_default_property_id() -> None:
    """The env-default factory receives the live property id."""
    captured: list[str] = []

    def factory(property_channel_id: str) -> TenantContext:
        captured.append(property_channel_id)
        return _env_default_factory(property_channel_id)

    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=factory,
    )
    await resolver.resolve("real_prop_id")
    assert captured == ["real_prop_id"]


async def test_resolver_no_lazy_probe_skips_lazy_layer() -> None:
    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default_factory,
        lazy_probe=None,
    )
    ctx = await resolver.resolve("prop1")
    assert ctx.source == TENANT_SOURCE_ENV_DEFAULT
