"""Tests for the bootstrap → registry upsert hook.

After a successful ``/bootstrap/property/{id}`` run, the route
calls :func:`record_bootstrap_tenant` so the operator-supplied
``customer_id`` / ``org_id`` / ``provider_type`` survive into the
registry and future requests against the same property
auto-resolve without those body fields.

The contract under test:

* :func:`record_bootstrap_tenant` is a no-op when no resolver is
  configured (Phase 3 disabled by env flag).
* A blank or missing ``customer_id`` skips the upsert — the
  resolver's env-default fallback already covers that case.
* A blank or missing ``provider_type`` skips the upsert — without
  a provider type the GraphQL gateway cannot route the next
  request anyway.
* A successful upsert writes ``source='request_body'`` so
  observability can distinguish operator-driven rows from
  nightly-sync or lazy rows.
* The resolver's cache is refreshed (not just invalidated) so the
  very next request observes the new mapping without a Postgres
  round-trip.
* Registry failures are swallowed — the bootstrap response stays
  authoritative even if the registry write fails.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain_engine.tenants import (
    InMemoryPropertyTenantRegistry,
    TenantContext,
    TenantResolver,
    configure_tenant_resolver,
)
from brain_engine.tenants.runtime import record_bootstrap_tenant


def _resolver(registry: InMemoryPropertyTenantRegistry) -> TenantResolver:
    return TenantResolver(
        registry=registry,
        env_default_factory=lambda pid: TenantContext(
            customer_id="env",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id=pid,
            source="env_default",
        ),
    )


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    """Detach the runtime resolver around every test."""
    configure_tenant_resolver(None)
    yield
    configure_tenant_resolver(None)


async def test_no_resolver_means_no_op() -> None:
    # No active resolver → must not raise.
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
    )


async def test_blank_customer_id_skips_upsert() -> None:
    registry = InMemoryPropertyTenantRegistry()
    configure_tenant_resolver(_resolver(registry))
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="   ",
        org_id="org",
        provider_type="HOSTAWAY",
    )
    assert await registry.get("prop1") is None


async def test_blank_provider_type_skips_upsert() -> None:
    registry = InMemoryPropertyTenantRegistry()
    configure_tenant_resolver(_resolver(registry))
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="cust",
        org_id="org",
        provider_type="   ",
    )
    assert await registry.get("prop1") is None


async def test_successful_upsert_writes_bootstrap_source() -> None:
    registry = InMemoryPropertyTenantRegistry()
    configure_tenant_resolver(_resolver(registry))
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
    )
    stored = await registry.get("prop1")
    assert stored is not None
    # SQL ``source`` column receives the origin label, not the
    # resolution-path label — see :data:`TENANT_SOURCE_BOOTSTRAP`.
    assert stored.source == "bootstrap"
    assert stored.customer_id == "cust"
    assert stored.org_id == "org"
    assert stored.provider_type == "HOSTAWAY"


async def test_upsert_refreshes_resolver_cache() -> None:
    registry = InMemoryPropertyTenantRegistry()
    resolver = _resolver(registry)
    configure_tenant_resolver(resolver)
    # Prime the cache with env default.
    await resolver.resolve("prop1")
    # Now record a row — both registry AND cache should reflect.
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
    )
    # Wipe the registry to prove the cache was refreshed.
    registry._rows.pop("prop1")  # type: ignore[attr-defined]
    ctx = await resolver.resolve("prop1")
    assert ctx.customer_id == "cust"


async def test_upsert_failure_does_not_raise() -> None:
    registry = InMemoryPropertyTenantRegistry()
    resolver = _resolver(registry)
    # Inject an upsert that always raises so the swallow contract
    # is exercised end to end.
    resolver._registry = AsyncMock()  # type: ignore[assignment]
    resolver._registry.upsert.side_effect = RuntimeError("db down")
    configure_tenant_resolver(resolver)
    # Must not propagate.
    await record_bootstrap_tenant(
        property_channel_id="prop1",
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
    )
