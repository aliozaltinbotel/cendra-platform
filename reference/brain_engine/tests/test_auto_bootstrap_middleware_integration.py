"""Middleware-side integration tests for Phase 4 auto-bootstrap.

Walks the documented happy path against a real FastAPI app + the
real :class:`TenantResolverMiddleware` with a configured
:class:`AutoBootstrapTrigger`.  The contract under test:

* When the trigger is configured, the middleware invokes
  ``trigger.maybe_fire`` after every successful tenant resolution.
* A registry hit + missing profile → bootstrap fires in the
  background; the request response is returned immediately
  (fire-and-forget — the test does not await the inner task).
* An exception inside the trigger never propagates to the request
  path — the middleware swallows + logs.
* When the trigger singleton is ``None`` the middleware behaves
  exactly like Phase 3 alone (no extra hook).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from starlette.testclient import TestClient

from brain_engine.tenants import (
    AutoBootstrapTrigger,
    InMemoryPropertyTenantRegistry,
    TenantContext,
    TenantResolver,
    TenantResolverMiddleware,
    configure_auto_bootstrap_trigger,
    configure_tenant_resolver,
    current_tenant,
)


def _env_default(property_channel_id: str) -> TenantContext:
    return TenantContext(
        customer_id="env_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id=property_channel_id,
        source="env_default",
    )


def _registry_with(*contexts: TenantContext) -> InMemoryPropertyTenantRegistry:
    registry = InMemoryPropertyTenantRegistry()
    for ctx in contexts:
        registry._rows[ctx.property_channel_id] = ctx  # type: ignore[attr-defined]
    return registry


class _FakeProfileStore:
    def __init__(self) -> None:
        self._rows: dict[str, Any] = {}

    async def get(self, property_channel_id: str) -> Any:
        return self._rows.get(property_channel_id)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TenantResolverMiddleware)

    @app.post("/property/{property_id}/echo")
    async def echo(property_id: str) -> dict[str, Any]:
        ctx = current_tenant()
        return {
            "property_id": property_id,
            "customer_id": ctx.customer_id if ctx else None,
            "source": ctx.source if ctx else None,
        }

    return app


def _build_pipeline() -> Any:
    pipeline = MagicMock(name="OnboardingBootstrapPipeline")
    report = MagicMock(
        conversations_loaded=5, cases_extracted=2, rules_emitted=0,
    )
    pipeline.bootstrap_fast = AsyncMock(return_value=report)
    return pipeline


async def test_middleware_fires_trigger_on_registry_hit() -> None:
    stored = TenantContext(
        customer_id="ec9013b9",
        org_id="626ee566",
        provider_type="LODGIFY",
        property_channel_id="598808",
        source="bootstrap",
    )
    resolver = TenantResolver(
        registry=_registry_with(stored),
        env_default_factory=_env_default,
    )
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
    )
    configure_tenant_resolver(resolver)
    configure_auto_bootstrap_trigger(trigger)
    try:
        app = _app()
        with TestClient(app) as client:
            response = client.post("/property/598808/echo")
        assert response.status_code == 200
        # Give the background task a tick to dispatch.
        await asyncio.sleep(0.05)
        pipeline.bootstrap_fast.assert_awaited_once()
        call_kwargs = pipeline.bootstrap_fast.call_args.kwargs
        assert call_kwargs["property_id"] == "598808"
        assert call_kwargs["customer_id_override"] == "ec9013b9"
    finally:
        configure_tenant_resolver(None)
        configure_auto_bootstrap_trigger(None)


async def test_middleware_skips_trigger_for_env_default_fallback() -> None:
    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default,
    )
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
    )
    configure_tenant_resolver(resolver)
    configure_auto_bootstrap_trigger(trigger)
    try:
        app = _app()
        with TestClient(app) as client:
            response = client.post("/property/unknown/echo")
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        pipeline.bootstrap_fast.assert_not_awaited()
    finally:
        configure_tenant_resolver(None)
        configure_auto_bootstrap_trigger(None)


async def test_middleware_swallows_trigger_exceptions() -> None:
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    resolver = TenantResolver(
        registry=_registry_with(stored),
        env_default_factory=_env_default,
    )
    # Synthetic trigger whose ``maybe_fire`` raises.  The middleware
    # must NOT propagate.
    broken_trigger = MagicMock(spec=AutoBootstrapTrigger)
    broken_trigger.maybe_fire = AsyncMock(side_effect=RuntimeError("nope"))
    configure_tenant_resolver(resolver)
    configure_auto_bootstrap_trigger(broken_trigger)
    try:
        app = _app()
        with TestClient(app) as client:
            response = client.post("/property/prop1/echo")
        assert response.status_code == 200
        assert response.json()["customer_id"] == "cust"
    finally:
        configure_tenant_resolver(None)
        configure_auto_bootstrap_trigger(None)


async def test_middleware_no_trigger_behaves_like_phase3_alone() -> None:
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    resolver = TenantResolver(
        registry=_registry_with(stored),
        env_default_factory=_env_default,
    )
    configure_tenant_resolver(resolver)
    configure_auto_bootstrap_trigger(None)
    try:
        app = _app()
        with TestClient(app) as client:
            response = client.post("/property/prop1/echo")
        assert response.status_code == 200
        assert response.json()["customer_id"] == "cust"
    finally:
        configure_tenant_resolver(None)


async def test_middleware_skips_trigger_when_no_property_id() -> None:
    resolver = TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=_env_default,
    )
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
    )
    configure_tenant_resolver(resolver)
    configure_auto_bootstrap_trigger(trigger)
    try:
        app = _app()

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return {"ok": True}

        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        pipeline.bootstrap_fast.assert_not_awaited()
    finally:
        configure_tenant_resolver(None)
        configure_auto_bootstrap_trigger(None)
