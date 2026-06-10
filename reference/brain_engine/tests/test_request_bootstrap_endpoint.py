"""HTTP tests for the request-bootstrap intent endpoint.

``POST /api/v1/onboarding/request-bootstrap/property/{id}`` is the
lightweight enqueue from ``CENDRA_BRAIN_ENGINE_ARCHITECTURE_2026.md``
§8.  It resolves the tenant, records an intent in the
``property_state`` SSoT, and dispatches the work through the shared
``request_bootstrap`` dedup path.  Contract under test:

* Cold property → ``200`` with ``enqueued=True``, ``status=queued``,
  a non-empty ``job_id``, and the dispatcher invoked once.
* A second call dedups against the in-flight row → ``enqueued=False``
  with ``reason=in_flight`` and no second dispatch.
* ``503`` when the SSoT is not wired (``PROPERTY_STATE_ENABLED`` off)
  or no resolver is published (``TENANT_RESOLVER_ENABLED`` off).
* ``409`` when the property resolves to a tenant with no customer id.
* The JSON body is optional (UI may post nothing).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api import bootstrap_intent_endpoints
from brain_engine.api.bootstrap_intent_endpoints import (
    configure_bootstrap_intent_deps,
    router,
)
from brain_engine.tenants import (
    TENANT_SOURCE_ENV_DEFAULT,
    BootstrapIntentMessage,
    BootstrapWorkload,
    InMemoryPropertyStateStore,
    InMemoryPropertyTenantRegistry,
    PropertyStateStore,
    TenantContext,
    TenantResolver,
    configure_tenant_resolver,
)

_URL = "/api/v1/onboarding/request-bootstrap/property/prop1"


class _SpyDispatcher:
    """Records dispatch calls without running the workload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        self.calls.append((property_channel_id, job_id))


def _resolver(*, customer_id: str = "cust") -> TenantResolver:
    def factory(property_channel_id: str) -> TenantContext:
        return TenantContext(
            customer_id=customer_id,
            org_id="org",
            provider_type="LODGIFY",
            property_channel_id=property_channel_id,
            source=TENANT_SOURCE_ENV_DEFAULT,
        )

    return TenantResolver(
        registry=InMemoryPropertyTenantRegistry(),
        env_default_factory=factory,
    )


def _wire(
    *,
    state_store: PropertyStateStore | None,
    dispatcher: object | None,
    pipeline: object | None,
    resolver: TenantResolver | None,
) -> TestClient:
    configure_tenant_resolver(resolver)
    configure_bootstrap_intent_deps(
        {
            "state_store": state_store,
            "dispatcher": dispatcher,
            "pipeline_getter": (lambda: pipeline),
        },
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def teardown_function() -> None:
    """Detach process globals so tests do not leak into each other."""
    configure_tenant_resolver(None)
    bootstrap_intent_endpoints._deps.clear()


def test_cold_property_enqueues() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    client = _wire(
        state_store=store,
        dispatcher=dispatcher,
        pipeline=object(),
        resolver=_resolver(),
    )

    resp = client.post(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["enqueued"] is True
    assert body["status"] == "queued"
    assert body["reason"] == "new"
    assert body["property_channel_id"] == "prop1"
    assert isinstance(body["job_id"], str) and body["job_id"]
    assert len(dispatcher.calls) == 1


def test_second_call_dedups_in_flight() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    client = _wire(
        state_store=store,
        dispatcher=dispatcher,
        pipeline=object(),
        resolver=_resolver(),
    )

    first = client.post(_URL).json()
    second = client.post(_URL).json()

    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["status"] == "queued"
    assert second["reason"] == "in_flight"
    assert len(dispatcher.calls) == 1


def test_optional_body_and_window_override() -> None:
    client = _wire(
        state_store=InMemoryPropertyStateStore(),
        dispatcher=_SpyDispatcher(),
        pipeline=object(),
        resolver=_resolver(),
    )

    # No body at all (UI may post nothing).
    assert client.post(_URL).status_code == 200
    # Explicit reason + window override on a fresh property.
    resp = client.post(
        "/api/v1/onboarding/request-bootstrap/property/prop2",
        json={"reason": "stale_refresh", "window_days": 30},
    )
    assert resp.status_code == 200
    assert resp.json()["enqueued"] is True


def test_503_when_state_store_not_wired() -> None:
    client = _wire(
        state_store=None,
        dispatcher=_SpyDispatcher(),
        pipeline=object(),
        resolver=_resolver(),
    )

    assert client.post(_URL).status_code == 503


def test_503_when_resolver_not_published() -> None:
    client = _wire(
        state_store=InMemoryPropertyStateStore(),
        dispatcher=_SpyDispatcher(),
        pipeline=object(),
        resolver=None,
    )

    assert client.post(_URL).status_code == 503


def test_409_when_tenant_has_no_customer_id() -> None:
    client = _wire(
        state_store=InMemoryPropertyStateStore(),
        dispatcher=_SpyDispatcher(),
        pipeline=object(),
        resolver=_resolver(customer_id=""),
    )

    assert client.post(_URL).status_code == 409
