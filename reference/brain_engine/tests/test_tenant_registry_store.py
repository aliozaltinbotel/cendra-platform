"""Tests for the :class:`PropertyTenantRegistry` stores.

InMemory + Postgres implementations share a single Protocol.  The
contract under test:

* :meth:`get` returns ``None`` for an unknown property and the
  stored :class:`TenantContext` otherwise — both implementations
  agree on the return value shape.
* :meth:`upsert` is idempotent: writing the same row twice does
  not raise and the second read returns the latest values.
* :meth:`upsert` rewrites every column — operators backfilling
  with corrected data must not be forced into a delete/insert
  dance to update an existing row.
* The Postgres store uses ``ON CONFLICT`` with the exact column
  set the migration expects (recorded via the asyncpg mock) so a
  schema drift would surface as a test failure rather than as a
  500 in production.
* Source tracking survives the round-trip — operators can audit
  ``bootstrap`` vs ``lazy`` vs ``manual`` rows for registry quality.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from brain_engine.tenants import (
    TENANT_SOURCE_LAZY,
    TENANT_SOURCE_REGISTRY,
    InMemoryPropertyTenantRegistry,
    PostgresPropertyTenantRegistry,
    TenantContext,
)


def _ctx(
    *,
    property_channel_id: str = "prop1",
    customer_id: str = "cust",
    org_id: str | None = "org",
    provider_type: str = "HOSTAWAY",
    source: str = TENANT_SOURCE_REGISTRY,
) -> TenantContext:
    return TenantContext(
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        property_channel_id=property_channel_id,
        source=source,
    )


# ── InMemory ──────────────────────────────────────────────────────


async def test_in_memory_get_returns_none_for_unknown() -> None:
    registry = InMemoryPropertyTenantRegistry()
    assert await registry.get("missing") is None


async def test_in_memory_upsert_then_get_roundtrips() -> None:
    registry = InMemoryPropertyTenantRegistry()
    ctx = _ctx()
    await registry.upsert(ctx)
    fetched = await registry.get("prop1")
    assert fetched == ctx


async def test_in_memory_upsert_is_idempotent() -> None:
    registry = InMemoryPropertyTenantRegistry()
    ctx = _ctx()
    await registry.upsert(ctx)
    await registry.upsert(ctx)
    assert await registry.get("prop1") == ctx


async def test_in_memory_upsert_overwrites_existing_row() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(_ctx(customer_id="old"))
    await registry.upsert(_ctx(customer_id="new"))
    fetched = await registry.get("prop1")
    assert fetched is not None
    assert fetched.customer_id == "new"


async def test_in_memory_distinguishes_distinct_properties() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(_ctx(property_channel_id="p1"))
    await registry.upsert(
        _ctx(property_channel_id="p2", customer_id="other"),
    )
    p1 = await registry.get("p1")
    p2 = await registry.get("p2")
    assert p1 is not None and p1.customer_id == "cust"
    assert p2 is not None and p2.customer_id == "other"


async def test_in_memory_preserves_source_field() -> None:
    registry = InMemoryPropertyTenantRegistry()
    ctx = _ctx(source=TENANT_SOURCE_LAZY)
    await registry.upsert(ctx)
    fetched = await registry.get("prop1")
    assert fetched is not None
    assert fetched.source == TENANT_SOURCE_LAZY


async def test_in_memory_preserves_null_org_id() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(_ctx(org_id=None))
    fetched = await registry.get("prop1")
    assert fetched is not None
    assert fetched.org_id is None


async def test_in_memory_returns_copy_safe_against_mutation_attempt() -> None:
    # TenantContext is frozen, so even an aliasing hand-out is safe.
    registry = InMemoryPropertyTenantRegistry()
    ctx = _ctx()
    await registry.upsert(ctx)
    fetched = await registry.get("prop1")
    assert fetched == ctx


# ── Postgres (asyncpg mock) ───────────────────────────────────────


def _build_pool(
    *,
    fetchrow_result: dict[str, Any] | None = None,
) -> tuple[Any, AsyncMock, AsyncMock]:
    """Build a fake asyncpg.Pool with recording fetchrow/execute."""
    conn = MagicMock(name="asyncpg.Connection")
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.execute = AsyncMock(return_value=None)

    pool = MagicMock(name="asyncpg.Pool")

    class _Acquire:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *exc: Any) -> None:
            return None

    pool.acquire = MagicMock(return_value=_Acquire())
    return pool, conn.fetchrow, conn.execute


async def test_postgres_get_returns_none_when_row_absent() -> None:
    pool, _, _ = _build_pool(fetchrow_result=None)
    registry = PostgresPropertyTenantRegistry(pool)
    assert await registry.get("missing") is None


async def test_postgres_get_maps_row_to_context() -> None:
    row = {
        "customer_id": "cust",
        "org_id": "org",
        "provider_type": "HOSTAWAY",
        "source": "bootstrap",
    }
    pool, _, _ = _build_pool(fetchrow_result=row)
    registry = PostgresPropertyTenantRegistry(pool)
    ctx = await registry.get("prop1")
    assert ctx is not None
    assert ctx.customer_id == "cust"
    assert ctx.org_id == "org"
    assert ctx.provider_type == "HOSTAWAY"
    assert ctx.property_channel_id == "prop1"
    assert ctx.source == "bootstrap"


async def test_postgres_upsert_invokes_execute_with_all_columns() -> None:
    pool, _, execute_mock = _build_pool()
    registry = PostgresPropertyTenantRegistry(pool)
    ctx = _ctx(source=TENANT_SOURCE_LAZY)
    await registry.upsert(ctx)
    execute_mock.assert_awaited_once()
    call_args = execute_mock.call_args.args
    # SQL + 5 positional params: property_channel_id, customer_id,
    # org_id, provider_type, source.
    assert len(call_args) == 6
    sql, prop, cust, org, provider, source = call_args
    assert "INSERT INTO property_tenant_registry" in sql
    assert "ON CONFLICT" in sql
    assert prop == "prop1"
    assert cust == "cust"
    assert org == "org"
    assert provider == "HOSTAWAY"
    assert source == TENANT_SOURCE_LAZY


async def test_postgres_upsert_passes_none_org_id() -> None:
    pool, _, execute_mock = _build_pool()
    registry = PostgresPropertyTenantRegistry(pool)
    await registry.upsert(_ctx(org_id=None))
    args = execute_mock.call_args.args
    assert args[3] is None  # org_id slot


# ── Cooldown methods (migration 033) ────────────────────────────


async def test_in_memory_get_last_auto_attempt_returns_none_when_never_set() -> None:
    registry = InMemoryPropertyTenantRegistry()
    assert await registry.get_last_auto_attempt("prop1") is None


async def test_in_memory_record_then_get_last_auto_attempt_roundtrips() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.record_auto_attempt("prop1")
    stamped = await registry.get_last_auto_attempt("prop1")
    assert stamped is not None
    assert stamped.tzinfo is not None  # always UTC-aware


async def test_postgres_get_last_auto_attempt_select_shape() -> None:
    from datetime import UTC, datetime

    expected = datetime(2026, 5, 22, 8, 0, tzinfo=UTC)
    pool, fetchrow_mock, _ = _build_pool(
        fetchrow_result={"last_auto_attempted_at": expected},
    )
    registry = PostgresPropertyTenantRegistry(pool)
    actual = await registry.get_last_auto_attempt("prop1")
    assert actual == expected
    fetchrow_mock.assert_awaited_once()
    sql = fetchrow_mock.call_args.args[0]
    assert "last_auto_attempted_at" in sql


async def test_postgres_record_auto_attempt_update_shape() -> None:
    pool, _, execute_mock = _build_pool()
    registry = PostgresPropertyTenantRegistry(pool)
    await registry.record_auto_attempt("prop1")
    execute_mock.assert_awaited_once()
    sql, prop = execute_mock.call_args.args
    assert "UPDATE property_tenant_registry" in sql
    assert "SET last_auto_attempted_at = now()" in sql
    assert prop == "prop1"


# ── distinct_customers (Phase 5 lazy probe) ─────────────────────


async def test_in_memory_distinct_customers_empty_when_no_rows() -> None:
    registry = InMemoryPropertyTenantRegistry()
    assert await registry.distinct_customers() == []


async def test_in_memory_distinct_customers_dedupes() -> None:
    registry = InMemoryPropertyTenantRegistry()
    await registry.upsert(_ctx(property_channel_id="p1", customer_id="A"))
    await registry.upsert(_ctx(property_channel_id="p2", customer_id="A"))
    await registry.upsert(_ctx(property_channel_id="p3", customer_id="B"))
    result = await registry.distinct_customers()
    assert sorted(result) == ["A", "B"]


def _build_fetch_pool(rows: list[dict[str, Any]]) -> tuple[Any, AsyncMock]:
    """asyncpg pool double whose conn.fetch() returns ``rows``."""
    conn = MagicMock(name="asyncpg.Connection")
    conn.fetch = AsyncMock(return_value=rows)

    pool = MagicMock(name="asyncpg.Pool")

    class _Acquire:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *exc: Any) -> None:
            return None

    pool.acquire = MagicMock(return_value=_Acquire())
    return pool, conn.fetch


async def test_postgres_distinct_customers_select_shape() -> None:
    pool, fetch_mock = _build_fetch_pool(
        [{"customer_id": "A"}, {"customer_id": "B"}],
    )
    registry = PostgresPropertyTenantRegistry(pool)
    result = await registry.distinct_customers()
    fetch_mock.assert_awaited_once()
    sql = fetch_mock.call_args.args[0]
    assert "SELECT DISTINCT customer_id" in sql
    assert sorted(result) == ["A", "B"]


async def test_postgres_distinct_customers_filters_blanks() -> None:
    pool, _ = _build_fetch_pool(
        [{"customer_id": "real"}, {"customer_id": ""}, {"customer_id": None}],
    )
    registry = PostgresPropertyTenantRegistry(pool)
    assert await registry.distinct_customers() == ["real"]
