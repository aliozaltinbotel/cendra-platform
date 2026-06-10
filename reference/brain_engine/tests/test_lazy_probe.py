"""Tests for :class:`GraphQLLazyProbe` (Phase 5).

The probe iterates every known customer and asks the unified-data
GraphQL gateway whether the unknown property channel id has any
conversations under that customer.  The contract under test:

* Blank ``property_channel_id`` → ``None`` (no GraphQL traffic).
* Iterates ``extra_customers`` BEFORE ``customers_provider`` so the
  pod env_default tenant is checked first.
* De-duplicates the candidate list while preserving order.
* First customer whose conversations query returns a row → returns
  a :class:`TenantContext` with ``source='lazy'`` and the
  ``provider_type`` carried by the response.
* When every candidate misses, returns ``None`` and stamps a
  negative cache entry.
* Subsequent calls within the TTL hit the negative cache (no
  GraphQL traffic).  After the TTL the entry is evicted and the
  probe runs again.
* GraphQL exceptions on a candidate do not abort the probe — the
  iteration continues with the next customer.
* ``customers_provider`` exceptions are swallowed; only the
  ``extra_customers`` list is used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from brain_engine.tenants import GraphQLLazyProbe


def _client_returning(
    *,
    conversations_by_customer: dict[str, list[dict[str, Any]]],
) -> Any:
    """Async client mock whose execute() routes by customerId."""

    async def execute(
        query: str,
        variables: dict[str, Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        cid = variables.get("customerId", "")
        return {"conversations": conversations_by_customer.get(cid, [])}

    client = AsyncMock()
    client.execute = AsyncMock(side_effect=execute)
    return client


def _client_always_failing() -> Any:
    client = AsyncMock()
    client.execute = AsyncMock(side_effect=RuntimeError("graphql down"))
    return client


async def _customers(*ids: str) -> list[str]:
    return list(ids)


async def test_blank_property_id_returns_none() -> None:
    client = _client_returning(conversations_by_customer={})
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("c1"),
    )
    assert await probe.probe("") is None
    client.execute.assert_not_awaited()


async def test_first_customer_with_conversation_wins() -> None:
    client = _client_returning(
        conversations_by_customer={
            "winner": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "LODGIFY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        },
    )
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("winner", "other"),
    )
    ctx = await probe.probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "winner"
    assert ctx.provider_type == "LODGIFY"
    assert ctx.org_id is None
    assert ctx.property_channel_id == "prop1"
    assert ctx.source == "lazy"


async def test_iterates_until_hit_skipping_empty_customers() -> None:
    client = _client_returning(
        conversations_by_customer={
            "owner": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "HOSTAWAY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        },
    )
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("miss_a", "miss_b", "owner"),
    )
    ctx = await probe.probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "owner"
    assert client.execute.await_count == 3


async def test_all_misses_returns_none_and_caches_negative() -> None:
    client = _client_returning(conversations_by_customer={})
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("c1", "c2"),
    )
    assert await probe.probe("prop1") is None
    assert client.execute.await_count == 2
    # Second call inside TTL should hit the negative cache — no
    # additional GraphQL traffic.
    assert await probe.probe("prop1") is None
    assert client.execute.await_count == 2


async def test_negative_cache_ttl_expires() -> None:
    client = _client_returning(conversations_by_customer={})
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("c1"),
        negative_cache_ttl=timedelta(milliseconds=1),
    )
    assert await probe.probe("prop1") is None
    # Forcibly age the cache entry past the TTL.
    probe._negative["prop1"] = (  # type: ignore[attr-defined]
        datetime.now(UTC) - timedelta(seconds=10)
    )
    assert await probe.probe("prop1") is None
    # Two probe-iterations should have happened (1+1).
    assert client.execute.await_count == 2


async def test_extra_customers_iterated_before_provider_list() -> None:
    client = _client_returning(
        conversations_by_customer={
            "env_default": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "GUESTY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        },
    )
    calls: list[str] = []

    async def execute(
        query: str,
        variables: dict[str, Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        calls.append(variables["customerId"])
        if variables["customerId"] == "env_default":
            return {
                "conversations": [
                    {
                        "channelEntityId": "prop1",
                        "providerType": "GUESTY",
                        "data": {"propertyChannelId": "prop1"},
                    },
                ],
            }
        return {"conversations": []}

    client.execute = AsyncMock(side_effect=execute)
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("from_registry"),
        extra_customers=("env_default",),
    )
    ctx = await probe.probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "env_default"
    assert calls[0] == "env_default"


async def test_dedup_avoids_double_probe() -> None:
    client = _client_returning(conversations_by_customer={})
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("dup", "dup"),
        extra_customers=("dup",),
    )
    assert await probe.probe("prop1") is None
    assert client.execute.await_count == 1


async def test_blank_customers_filtered_out() -> None:
    client = _client_returning(conversations_by_customer={})
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("", "real"),
        extra_customers=("",),
    )
    assert await probe.probe("prop1") is None
    assert client.execute.await_count == 1


async def test_graphql_exception_skips_to_next_customer() -> None:
    """A failure on one customer must not abort the entire probe."""

    async def execute(
        query: str,
        variables: dict[str, Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        if variables["customerId"] == "broken":
            raise RuntimeError("upstream 500")
        return {
            "conversations": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "LODGIFY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        }

    client = AsyncMock()
    client.execute = AsyncMock(side_effect=execute)
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("broken", "ok"),
    )
    ctx = await probe.probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "ok"


async def test_customers_provider_exception_falls_back_to_extras() -> None:
    """Registry failure must not crash the probe — extras still tried."""

    async def boom() -> list[str]:
        raise RuntimeError("registry down")

    client = _client_returning(
        conversations_by_customer={
            "env": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "HOSTAWAY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        },
    )
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=boom,
        extra_customers=("env",),
    )
    ctx = await probe.probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "env"


async def test_missing_provider_type_in_response_treated_as_miss() -> None:
    """A row without providerType cannot resolve a complete tenant."""
    client = _client_returning(
        conversations_by_customer={
            "c1": [
                {"channelEntityId": "prop1", "data": {}},
            ],
        },
    )
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("c1"),
    )
    assert await probe.probe("prop1") is None


async def test_call_is_async_callable_alias() -> None:
    """``GraphQLLazyProbe`` is itself usable as a ``LazyTenantProbe``."""
    client = _client_returning(
        conversations_by_customer={
            "c1": [
                {
                    "channelEntityId": "prop1",
                    "providerType": "LODGIFY",
                    "data": {"propertyChannelId": "prop1"},
                },
            ],
        },
    )
    probe = GraphQLLazyProbe(
        client=client,
        customers_provider=lambda: _customers("c1"),
    )
    ctx = await probe("prop1")
    assert ctx is not None
    assert ctx.customer_id == "c1"
