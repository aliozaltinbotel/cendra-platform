"""Tests for :class:`TenantResolverMiddleware`.

The middleware sits behind auth + rate-limiting and binds a
:class:`TenantContext` to the request before the route handler
runs.  The contract under test:

* The middleware extracts ``property_id`` from path / header /
  query in that priority order.  JSON body extraction was removed
  after dev pod 500s — Starlette ``BaseHTTPMiddleware``'s internal
  ``wrapped_receive`` state machine cannot tolerate a custom
  ``request._receive`` replay when the inner endpoint streams a
  response (AG-UI SSE).
* Downstream handlers see the bound :class:`TenantContext` via
  :func:`current_tenant` for the duration of the request.
* Requests without a property identifier pass through untouched.
* When no resolver is configured the middleware is a no-op even
  for requests that DO carry a property id.
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


def _build_resolver(stored: TenantContext | None = None) -> TenantResolver:
    registry = InMemoryPropertyTenantRegistry()
    if stored is not None:
        registry._rows[stored.property_channel_id] = stored  # type: ignore[attr-defined]

    def env_default(property_channel_id: str) -> TenantContext:
        return TenantContext(
            customer_id="env_cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id=property_channel_id,
            source="env_default",
        )

    return TenantResolver(
        registry=registry,
        env_default_factory=env_default,
    )


def _build_app(
    resolver: TenantResolver | None,
    *,
    use_runtime_singleton: bool = False,
) -> FastAPI:
    app = FastAPI()
    if use_runtime_singleton:
        configure_tenant_resolver(resolver)
        app.add_middleware(TenantResolverMiddleware)
    else:
        app.add_middleware(
            TenantResolverMiddleware,
            resolver=resolver,
        )

    @app.post("/property/{property_id}/echo")
    async def echo_path(property_id: str) -> dict[str, object]:
        ctx = current_tenant()
        return {
            "property_id": property_id,
            "customer_id": ctx.customer_id if ctx else None,
            "source": ctx.source if ctx else None,
        }

    @app.post("/foo")
    async def echo_foo() -> dict[str, object]:
        ctx = current_tenant()
        return {
            "customer_id": ctx.customer_id if ctx else None,
            "source": ctx.source if ctx else None,
        }

    @app.get("/health")
    async def health() -> dict[str, object]:
        ctx = current_tenant()
        return {"tenant": ctx.customer_id if ctx else None}

    return app


def test_middleware_extracts_from_path() -> None:
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    app = _build_app(_build_resolver(stored))
    with TestClient(app) as client:
        response = client.post("/property/prop1/echo")
    assert response.status_code == 200
    data = response.json()
    assert data["customer_id"] == "cust"
    assert data["source"] == "registry"


def test_middleware_extracts_from_query() -> None:
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    app = _build_app(_build_resolver(stored))
    with TestClient(app) as client:
        response = client.post("/foo?property_id=prop1", json={"x": 1})
    assert response.status_code == 200
    assert response.json()["customer_id"] == "cust"


def test_middleware_extracts_from_header_x_property_channel_id() -> None:
    """Primary header documented for Sandbox UI integration."""
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    app = _build_app(_build_resolver(stored))
    with TestClient(app) as client:
        response = client.post(
            "/foo",
            json={"any": "payload"},
            headers={"X-Property-Channel-Id": "prop1"},
        )
    assert response.status_code == 200
    assert response.json()["customer_id"] == "cust"


def test_middleware_extracts_from_header_alias() -> None:
    """``X-Property-Id`` and ``X-PropertyChannelId`` are accepted aliases."""
    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    app = _build_app(_build_resolver(stored))
    with TestClient(app) as client:
        response = client.post(
            "/foo",
            json={"any": "payload"},
            headers={"X-Property-Id": "prop1"},
        )
    assert response.status_code == 200
    assert response.json()["customer_id"] == "cust"


def test_middleware_does_not_break_streaming_endpoints() -> None:
    """SSE-style endpoints must not 500 when middleware is mounted.

    This is the regression that prompted dropping the JSON body
    peek: ``BaseHTTPMiddleware.wrapped_receive`` raised
    ``RuntimeError: Unexpected message received: http.request``
    on AG-UI SSE handshakes when the middleware replaced
    ``request._receive`` with a body-replay callable.
    """
    from starlette.responses import StreamingResponse

    stored = TenantContext(
        customer_id="cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    app = _build_app(_build_resolver(stored))

    async def event_stream() -> object:
        yield b"data: hello\n\n"

    @app.post("/stream")
    async def stream_endpoint() -> StreamingResponse:
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    with TestClient(app) as client:
        response = client.post(
            "/stream",
            json={"any": "payload"},
            headers={"X-Property-Channel-Id": "prop1"},
        )
    assert response.status_code == 200
    assert b"hello" in response.content


def test_middleware_passes_through_when_no_property_id() -> None:
    app = _build_app(_build_resolver())
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"tenant": None}


def test_middleware_no_op_when_resolver_not_configured() -> None:
    app = _build_app(resolver=None)
    with TestClient(app) as client:
        response = client.post("/property/prop1/echo")
    assert response.status_code == 200
    data = response.json()
    assert data["customer_id"] is None
    assert data["source"] is None


def test_middleware_resolves_via_runtime_singleton() -> None:
    stored = TenantContext(
        customer_id="singleton_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source="bootstrap",
    )
    resolver = _build_resolver(stored)
    app = _build_app(resolver=resolver, use_runtime_singleton=True)
    try:
        with TestClient(app) as client:
            response = client.post("/property/prop1/echo")
        assert response.status_code == 200
        assert response.json()["customer_id"] == "singleton_cust"
    finally:
        configure_tenant_resolver(None)


def test_middleware_priority_path_over_header() -> None:
    """Path wins over header — path is cheaper to extract."""
    stored = TenantContext(
        customer_id="path_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="path_prop",
        source="bootstrap",
    )
    resolver = _build_resolver(stored)
    app = _build_app(resolver)
    with TestClient(app) as client:
        response = client.post(
            "/property/path_prop/echo",
            headers={"X-Property-Channel-Id": "header_prop"},
        )
    assert response.status_code == 200
    assert response.json()["customer_id"] == "path_cust"


def test_middleware_priority_header_over_query() -> None:
    """Header wins over query — explicit header beats opaque query."""
    stored = TenantContext(
        customer_id="header_cust",
        org_id=None,
        provider_type="HOSTAWAY",
        property_channel_id="header_prop",
        source="bootstrap",
    )
    resolver = _build_resolver(stored)
    app = _build_app(resolver)
    with TestClient(app) as client:
        response = client.post(
            "/foo?property_id=query_prop",
            headers={"X-Property-Channel-Id": "header_prop"},
        )
    assert response.status_code == 200
    assert response.json()["customer_id"] == "header_cust"


def test_middleware_falls_back_to_env_default_when_unknown() -> None:
    app = _build_app(_build_resolver())
    with TestClient(app) as client:
        response = client.post("/property/unknown/echo")
    assert response.status_code == 200
    data = response.json()
    assert data["customer_id"] == "env_cust"
    assert data["source"] == "env_default"
