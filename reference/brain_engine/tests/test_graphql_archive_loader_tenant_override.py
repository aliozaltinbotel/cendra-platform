"""Tests for the per-call tenant override on the GraphQL archive loader.

Phase 1 multi-tenant bootstrap (2026-05-21) makes the loader accept
per-call ``customer_id_override`` / ``org_id_override`` /
``provider_type_override`` kwargs so the cross-tenant bootstrap
endpoint can ingest properties from any Cendra workspace without
bouncing the pod.

The contract under test:

* When no override is supplied, the GraphQL ``variables`` dict
  carries the constructor-baked tenant strings exactly like before
  — no behavioural drift for the existing single-tenant pod.
* When a non-empty override is supplied, it replaces the baked
  value in the GraphQL variables for both the reservations index
  pre-fetch and the conversations page query.
* An empty / whitespace-only ``customer_id`` override falls back
  to the baked value (the gateway rejects blank customer ids).
* An empty / whitespace-only ``org_id`` / ``provider_type``
  override clears the slot — the optional GraphQL filter is
  dropped from the variables dict for that call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from brain_engine.onboarding.graphql_archive_loader import (
    GraphQLConversationArchiveLoader,
)

# ── Fixtures ──────────────────────────────────────────────────────


def _build_client() -> AsyncMock:
    """Stub :class:`UnifiedDataGraphQLClient` that records calls.

    ``execute`` returns empty lists so the loader walks one page and
    exits — the focus here is the *variables* the loader sends, not
    the data it would emit on a populated tenant.
    """
    client = AsyncMock(name="UnifiedDataGraphQLClient")
    client.execute = AsyncMock(
        side_effect=[
            {"reservations": []},
            {"conversations": []},
        ],
    )
    return client


async def _drain(
    loader: GraphQLConversationArchiveLoader, **kwargs: Any
) -> None:
    """Drive the loader's async iterator to completion."""
    iterator = loader.load(
        property_id="prop1",
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 6, 1, tzinfo=UTC),
        limit=10,
        **kwargs,
    )
    async for _ in iterator:
        pass


def _calls(client: AsyncMock) -> list[dict[str, Any]]:
    """Return the per-call ``variables`` dicts for the GraphQL client."""
    return [call.args[1] for call in client.execute.await_args_list]


# ── Default behaviour (no override) ──────────────────────────────


async def test_no_override_uses_baked_tenant_strings() -> None:
    """The constructor-baked tenant strings reach the GraphQL gateway
    verbatim when the caller does not pass any override."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id="org-A",
        provider_type="HOSTAWAY",
    )

    await _drain(loader)

    for variables in _calls(client):
        assert variables["customerId"] == "cust-A"
        assert variables["orgId"] == "org-A"
        assert variables["providerType"] == "HOSTAWAY"


async def test_no_org_or_provider_baked_omits_optional_filters() -> None:
    """Optional GraphQL filters are dropped from variables when the
    constructor was called with ``None`` and no override is given."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id=None,
        provider_type=None,
    )

    await _drain(loader)

    for variables in _calls(client):
        assert "orgId" not in variables
        assert "providerType" not in variables
        assert variables["customerId"] == "cust-A"


# ── Override applied ──────────────────────────────────────────────


async def test_customer_id_override_replaces_baked_value() -> None:
    """``customer_id_override`` swaps the GraphQL ``customerId`` for
    this call only — used by the cross-tenant bootstrap endpoint."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id="org-A",
        provider_type="HOSTAWAY",
    )

    await _drain(loader, customer_id_override="cust-B")

    for variables in _calls(client):
        assert variables["customerId"] == "cust-B"
        # The other filters keep the baked values when only customer
        # is overridden — minimal-surprise behaviour.
        assert variables["orgId"] == "org-A"
        assert variables["providerType"] == "HOSTAWAY"


async def test_full_triple_override_applies() -> None:
    """All three overrides land together in the GraphQL variables."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id="org-A",
        provider_type="HOSTAWAY",
    )

    await _drain(
        loader,
        customer_id_override="cust-B",
        org_id_override="org-B",
        provider_type_override="LODGIFY",
    )

    for variables in _calls(client):
        assert variables["customerId"] == "cust-B"
        assert variables["orgId"] == "org-B"
        assert variables["providerType"] == "LODGIFY"


# ── Defensive fallback for blank values ──────────────────────────


async def test_blank_customer_override_falls_back_to_baked() -> None:
    """An empty / whitespace-only ``customer_id`` override is treated
    as "no override" — the gateway rejects blank customer ids and
    silently shrinking the matcher would be hostile."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id="org-A",
    )

    await _drain(loader, customer_id_override="   ")

    for variables in _calls(client):
        assert variables["customerId"] == "cust-A"


async def test_blank_org_override_clears_optional_filter() -> None:
    """An empty ``org_id`` override drops the ``orgId`` variable —
    the operator wants to broaden the query past the pod default."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        cendra_org_id="org-A",
    )

    await _drain(loader, org_id_override="")

    for variables in _calls(client):
        assert "orgId" not in variables


async def test_blank_provider_override_clears_optional_filter() -> None:
    """An empty ``provider_type`` override drops the ``providerType``
    variable so a Hostaway-default pod can hit a Lodgify property
    without carrying the wrong provider filter into the query."""
    client = _build_client()
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
        provider_type="HOSTAWAY",
    )

    await _drain(loader, provider_type_override="")

    for variables in _calls(client):
        assert "providerType" not in variables


# ── Per-call isolation ────────────────────────────────────────────


async def test_override_does_not_leak_into_next_call() -> None:
    """The override applies to ONE ``load()`` invocation only —
    subsequent calls without override see baked defaults again."""
    client = AsyncMock(name="UnifiedDataGraphQLClient")
    client.execute = AsyncMock(
        side_effect=[
            {"reservations": []},
            {"conversations": []},
            {"reservations": []},
            {"conversations": []},
        ],
    )
    loader = GraphQLConversationArchiveLoader(
        client,
        cendra_customer_id="cust-A",
    )

    await _drain(loader, customer_id_override="cust-B")
    await _drain(loader)

    calls = _calls(client)
    # First load: overridden
    assert calls[0]["customerId"] == "cust-B"
    assert calls[1]["customerId"] == "cust-B"
    # Second load: baked default restored
    assert calls[2]["customerId"] == "cust-A"
    assert calls[3]["customerId"] == "cust-A"
