"""Tests for the :class:`TenantContext` value object and ContextVar.

The :class:`TenantContext` is the per-request tenant identity the
Phase 3 resolver populates and downstream services consume.  The
contract under test:

* The dataclass is frozen — accidental mutation between middleware
  and downstream services is impossible.
* The ``source`` field is validated against the documented enum so
  callers cannot accidentally introduce typoed source labels that
  would defeat observability filters.
* :func:`current_tenant` returns ``None`` outside a bound block —
  the no-middleware code path stays opt-in.
* :func:`bind_tenant` sets and resets the ContextVar in a single
  ``with`` block, including when the body raises.
* Nested binds restore the outer context, never leak.
"""

from __future__ import annotations

import dataclasses

import pytest

from brain_engine.tenants import (
    TENANT_SOURCE_ENV_DEFAULT,
    TENANT_SOURCE_LAZY,
    TENANT_SOURCE_REGISTRY,
    TENANT_SOURCE_REQUEST_BODY,
    TenantContext,
    bind_tenant,
    current_tenant,
)


def _ctx(source: str = TENANT_SOURCE_REGISTRY) -> TenantContext:
    return TenantContext(
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
        property_channel_id="prop1",
        source=source,
    )


def test_tenant_context_is_frozen() -> None:
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.customer_id = "other"  # type: ignore[misc]


def test_tenant_context_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="source"):
        TenantContext(
            customer_id="cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source="cache_hit_v2",
        )


def test_tenant_context_accepts_every_documented_source() -> None:
    for source in (
        TENANT_SOURCE_REQUEST_BODY,
        TENANT_SOURCE_REGISTRY,
        TENANT_SOURCE_LAZY,
        TENANT_SOURCE_ENV_DEFAULT,
    ):
        ctx = TenantContext(
            customer_id="cust",
            org_id=None,
            provider_type="HOSTAWAY",
            property_channel_id="prop1",
            source=source,
        )
        assert ctx.source == source


def test_current_tenant_default_is_none() -> None:
    assert current_tenant() is None


def test_bind_tenant_sets_and_clears() -> None:
    ctx = _ctx()
    with bind_tenant(ctx) as bound:
        assert bound is ctx
        assert current_tenant() is ctx
    assert current_tenant() is None


def test_bind_tenant_clears_on_exception() -> None:
    ctx = _ctx()
    with pytest.raises(RuntimeError, match="boom"), bind_tenant(ctx):
        assert current_tenant() is ctx
        raise RuntimeError("boom")
    assert current_tenant() is None


def test_bind_tenant_nested_restores_outer() -> None:
    outer = _ctx(TENANT_SOURCE_REGISTRY)
    inner = TenantContext(
        customer_id="other",
        org_id=None,
        provider_type="LODGIFY",
        property_channel_id="prop1",
        source=TENANT_SOURCE_LAZY,
    )
    with bind_tenant(outer):
        assert current_tenant() is outer
        with bind_tenant(inner):
            assert current_tenant() is inner
        assert current_tenant() is outer
    assert current_tenant() is None
