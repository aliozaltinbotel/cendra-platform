"""Tests that downstream loaders honour the active :class:`TenantContext`.

Phase 3 promises every existing reader/loader picks up the tenant
the middleware bound to the request, while keeping the legacy
single-tenant pod path byte-for-byte identical when no middleware
is installed.  The contract under test:

* :class:`GraphQLConversationArchiveLoader` reads
  :func:`current_tenant` when no override kwargs were passed; the
  explicit-override path (Phase 1) still wins.
* :class:`UnifiedPropertyReader` / :class:`UnifiedRatePlanReader` /
  :class:`UnifiedReviewReader` consult ``current_tenant`` via the
  shared ``_effective_tenant`` helper before reaching for the
  constructor-baked defaults.
* The legacy path (no ContextVar bound) is unchanged.
* ``get_detail`` accepts a ContextVar-only tenant (no constructor
  org_id / provider_type) — the new code path that lazy-resolved
  properties exercise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from brain_engine.integrations.unified_data.readers import (
    UnifiedPropertyReader,
    UnifiedRatePlanReader,
    UnifiedReviewReader,
)
from brain_engine.onboarding.graphql_archive_loader import (
    GraphQLConversationArchiveLoader,
)
from brain_engine.tenants import TenantContext, bind_tenant


def _archive_client() -> AsyncMock:
    client = AsyncMock(name="UnifiedDataGraphQLClient")
    client.execute = AsyncMock(
        side_effect=[
            {"reservations": []},
            {"conversations": []},
        ],
    )
    return client


async def _drain(loader: GraphQLConversationArchiveLoader) -> None:
    iterator = loader.load(
        property_id="prop1",
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 1, 2, tzinfo=UTC),
    )
    async for _ in iterator:
        pass


def _ctx(
    *,
    customer_id: str = "ctxvar_cust",
    org_id: str | None = "ctxvar_org",
    provider_type: str = "LODGIFY",
) -> TenantContext:
    return TenantContext(
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        property_channel_id="prop1",
        source="registry",
    )


# ── GraphQL archive loader ────────────────────────────────────────


async def test_loader_uses_baked_default_when_no_context() -> None:
    client = _archive_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    await _drain(loader)
    last_call = client.execute.call_args_list[-1]
    variables = last_call.kwargs.get("variables") or last_call.args[1]
    assert variables["customerId"] == "baked_cust"


async def test_loader_uses_context_var_when_set() -> None:
    client = _archive_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(customer_id="ctxvar_cust")):
        await _drain(loader)
    last_call = client.execute.call_args_list[-1]
    variables = last_call.kwargs.get("variables") or last_call.args[1]
    assert variables["customerId"] == "ctxvar_cust"


async def test_loader_explicit_override_beats_context_var() -> None:
    client = _archive_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(customer_id="ctxvar_cust")):
        iterator = loader.load(
            property_id="prop1",
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=datetime(2026, 1, 2, tzinfo=UTC),
            customer_id_override="explicit_cust",
        )
        async for _ in iterator:
            pass
    last_call = client.execute.call_args_list[-1]
    variables = last_call.kwargs.get("variables") or last_call.args[1]
    assert variables["customerId"] == "explicit_cust"


async def test_loader_context_var_org_id_propagates() -> None:
    client = _archive_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(org_id="ctxvar_org_id")):
        await _drain(loader)
    last_call = client.execute.call_args_list[-1]
    variables = last_call.kwargs.get("variables") or last_call.args[1]
    assert variables["orgId"] == "ctxvar_org_id"


# ── Unified data readers ──────────────────────────────────────────


def _readers_client() -> AsyncMock:
    client = AsyncMock(name="UnifiedDataGraphQLClient")
    client.execute = AsyncMock(return_value={"properties": []})
    return client


async def test_property_reader_uses_context_var() -> None:
    client = _readers_client()
    reader = UnifiedPropertyReader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(customer_id="ctxvar_cust", provider_type="LODGIFY")):
        await reader.list_summaries()
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert variables["customerId"] == "ctxvar_cust"
    assert variables["providerType"] == "LODGIFY"


async def test_property_reader_falls_back_to_baked() -> None:
    client = _readers_client()
    reader = UnifiedPropertyReader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    await reader.list_summaries()
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert variables["customerId"] == "baked_cust"
    assert variables["providerType"] == "HOSTAWAY"


async def test_rate_plan_reader_uses_context_var() -> None:
    client = _readers_client()
    client.execute = AsyncMock(return_value={"ratePlans": []})
    reader = UnifiedRatePlanReader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(customer_id="ctxvar_cust")):
        await reader.list_for_property(property_channel_id="prop1")
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert variables["customerId"] == "ctxvar_cust"


async def test_review_reader_uses_context_var() -> None:
    client = _readers_client()
    client.execute = AsyncMock(return_value={"reviews": []})
    reader = UnifiedReviewReader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(customer_id="ctxvar_cust")):
        await reader.list_for_property(property_channel_id="prop1")
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert variables["customerId"] == "ctxvar_cust"


async def test_get_detail_uses_context_var_org_and_provider() -> None:
    """get_detail accepts a ContextVar-only tenant — Phase 3 path."""
    client = AsyncMock(name="UnifiedDataGraphQLClient")
    client.execute = AsyncMock(
        return_value={"property": {"id": "p", "name": "x"}},
    )
    reader = UnifiedPropertyReader(
        client,
        cendra_customer_id="baked_cust",
        # No baked org_id / provider_type — exercises Phase 3 fallback.
    )
    with bind_tenant(_ctx(org_id="ctxvar_org", provider_type="LODGIFY")):
        await reader.get_detail(channel_entity_id="prop1")
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert variables["orgId"] == "ctxvar_org"
    assert variables["providerType"] == "LODGIFY"


async def test_property_reader_clears_org_id_when_context_says_none() -> None:
    """A ContextVar with org_id=None drops the optional filter."""
    client = _readers_client()
    reader = UnifiedPropertyReader(
        client,
        cendra_customer_id="baked_cust",
        cendra_org_id="baked_org",
        provider_type="HOSTAWAY",
    )
    with bind_tenant(_ctx(org_id=None, provider_type="HOSTAWAY")):
        await reader.list_summaries()
    call = client.execute.call_args
    variables = call.kwargs.get("variables") or call.args[1]
    assert "orgId" not in variables
