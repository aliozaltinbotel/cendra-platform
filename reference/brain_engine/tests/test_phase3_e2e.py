"""End-to-end integration tests for Phase 3 auto-resolution.

Mounts the real :class:`TenantResolverMiddleware` against a small
FastAPI app, wires an in-memory registry + resolver via the
runtime singleton, and walks the documented happy path:

* Bootstrap-style upsert seeds the registry.
* A subsequent request that carries only ``property_id`` resolves
  to the bootstrapped tenant — no manual ``customer_id`` in body.
* Lazy probe path discovers an unknown property at request time
  and writes it back to the registry.
* Tearing down the resolver returns the app to no-op behaviour.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from brain_engine.tenants import (
    InMemoryPropertyTenantRegistry,
    TenantContext,
    TenantResolver,
    TenantResolverMiddleware,
    configure_tenant_resolver,
    current_tenant,
)
from brain_engine.tenants.runtime import record_bootstrap_tenant


def _env_default(property_channel_id: str) -> TenantContext:
    return TenantContext(
        customer_id="env_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id=property_channel_id,
        source="env_default",
    )


def _app(resolver: TenantResolver) -> FastAPI:
    configure_tenant_resolver(resolver)
    app = FastAPI()
    app.add_middleware(TenantResolverMiddleware)

    @app.post("/conversation")
    async def conversation(payload: dict[str, object]) -> dict[str, object]:
        ctx = current_tenant()
        return {
            "property_id": payload.get("property_id"),
            "customer_id": ctx.customer_id if ctx else None,
            "source": ctx.source if ctx else None,
        }

    return app


async def test_bootstrap_then_conversation_resolves_tenant() -> None:
    """Happy path: bootstrap upsert → subsequent Sandbox UI request."""
    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default,
    )
    try:
        # Step 1 — operator bootstraps property 598808 with overrides.
        configure_tenant_resolver(resolver)
        await record_bootstrap_tenant(
            property_channel_id="598808",
            customer_id="ec9013b9",
            org_id="626ee566",
            provider_type="LODGIFY",
        )
        # Step 2 — Sandbox UI fires a conversation with only property_id.
        app = _app(resolver)
        with TestClient(app) as client:
            response = client.post(
                "/conversation",
                json={"property_id": "598808", "message": "hi"},
                headers={"X-Property-Channel-Id": "598808"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["customer_id"] == "ec9013b9"
        assert data["source"] == "registry"
    finally:
        configure_tenant_resolver(None)


async def test_lazy_probe_path_discovers_and_persists() -> None:
    """Unknown property → lazy probe runs once, then served from registry."""
    probe_calls: list[str] = []

    async def probe(property_channel_id: str) -> TenantContext:
        probe_calls.append(property_channel_id)
        return TenantContext(
            customer_id="discovered_cust",
            org_id=None,
            provider_type="GUESTY",
            property_channel_id=property_channel_id,
            source="lazy",
        )

    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default,
        lazy_probe=probe,
    )
    try:
        app = _app(resolver)
        with TestClient(app) as client:
            first = client.post(
                "/conversation",
                json={"property_id": "lazy_prop", "message": "hi"},
                headers={"X-Property-Channel-Id": "lazy_prop"},
            )
            second = client.post(
                "/conversation",
                json={"property_id": "lazy_prop", "message": "hi again"},
                headers={"X-Property-Channel-Id": "lazy_prop"},
            )
        assert first.json()["customer_id"] == "discovered_cust"
        assert second.json()["customer_id"] == "discovered_cust"
        # Probe runs only on the first request — second hit comes
        # from the cache populated by the resolver.
        assert probe_calls == ["lazy_prop"]
        # Registry now holds the discovered mapping.
        stored = await registry.get("lazy_prop")
        assert stored is not None
        assert stored.customer_id == "discovered_cust"
    finally:
        configure_tenant_resolver(None)


async def test_env_default_fallback_when_no_probe_and_unknown_property() -> None:
    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default,
    )
    try:
        app = _app(resolver)
        with TestClient(app) as client:
            response = client.post(
                "/conversation",
                json={"property_id": "unknown", "message": "hi"},
                headers={"X-Property-Channel-Id": "unknown"},
            )
        data = response.json()
        assert data["customer_id"] == "env_cust"
        assert data["source"] == "env_default"
    finally:
        configure_tenant_resolver(None)


async def test_tearing_down_resolver_returns_to_no_op() -> None:
    registry = InMemoryPropertyTenantRegistry()
    resolver = TenantResolver(
        registry=registry,
        env_default_factory=_env_default,
    )
    configure_tenant_resolver(resolver)
    app = FastAPI()
    app.add_middleware(TenantResolverMiddleware)

    @app.post("/c")
    async def echo(payload: dict[str, object]) -> dict[str, object]:
        ctx = current_tenant()
        return {"customer_id": ctx.customer_id if ctx else None}

    # Now detach the resolver before any request runs.
    configure_tenant_resolver(None)
    with TestClient(app) as client:
        response = client.post("/c", json={"property_id": "x"})
    assert response.json() == {"customer_id": None}
