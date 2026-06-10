"""Tests for :class:`PropertyStateStore` (InMemory + Postgres).

Three layers under test:

1. :class:`PropertyState` value object — status validation, the
   non-negative invariants on the counter fields, and frozen
   replace semantics (the only legal way to compose a
   transition).
2. :class:`InMemoryPropertyStateStore` — the Protocol contract
   on a single-event-loop store.  Idempotency of
   :meth:`create_if_absent` and the missing-row behaviour of
   :meth:`update` matter for the bootstrap-intent function
   (PR-B) — once those two land any deviation here would
   silently change the dedup contract.
3. :class:`PostgresPropertyStateStore` — asyncpg pool mocked
   exactly like ``test_tenant_registry_store.py`` so a schema
   drift (column rename, ``ON CONFLICT`` clause loss, missing
   ``RETURNING``) surfaces here rather than as a 500 in
   production.

The mocks deliberately assert the *shape* of the SQL rather
than its exact whitespace — a future ``ruff format`` pass on
the multiline statements must not break the suite.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_COLD,
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    InMemoryPropertyStateStore,
    PostgresPropertyStateStore,
    PropertyState,
    PropertyStateNotFoundError,
)


def _state(
    *,
    property_channel_id: str = "p1",
    customer_id: str = "cust",
    org_id: str | None = "org",
    provider_type: str = "HOSTAWAY",
    status: str = PROPERTY_STATUS_COLD,
) -> PropertyState:
    return PropertyState(
        property_channel_id=property_channel_id,
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        status=status,
    )


# ── PropertyState value-object validation ────────────────────


def test_default_status_is_cold() -> None:
    state = _state()
    assert state.status == PROPERTY_STATUS_COLD


def test_status_validation_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="status="):
        _state(status="bogus")


def test_status_validation_message_lists_allowed() -> None:
    with pytest.raises(ValueError) as exc:
        _state(status="bogus")
    assert "cold" in str(exc.value)
    assert "primed" in str(exc.value)


def test_negative_conversations_loaded_rejected() -> None:
    with pytest.raises(ValueError, match="conversations_loaded"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            conversations_loaded=-1,
        )


def test_negative_cases_extracted_rejected() -> None:
    with pytest.raises(ValueError, match="cases_extracted"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            cases_extracted=-1,
        )


def test_negative_rules_emitted_rejected() -> None:
    with pytest.raises(ValueError, match="rules_emitted"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            rules_emitted=-1,
        )


def test_negative_retry_count_rejected() -> None:
    with pytest.raises(ValueError, match="retry_count"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            retry_count=-1,
        )


def test_zero_window_days_rejected() -> None:
    with pytest.raises(ValueError, match="window_days"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            window_days=0,
        )


def test_negative_window_days_rejected() -> None:
    with pytest.raises(ValueError, match="window_days"):
        PropertyState(
            property_channel_id="p1",
            customer_id="c",
            provider_type="HOSTAWAY",
            window_days=-7,
        )


def test_none_window_days_allowed() -> None:
    state = _state()
    assert state.window_days is None


def test_state_is_frozen() -> None:
    state = _state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.status = PROPERTY_STATUS_WARMING  # type: ignore[misc]


def test_replace_produces_new_state_with_updated_status() -> None:
    cold = _state()
    warming = dataclasses.replace(
        cold,
        status=PROPERTY_STATUS_WARMING,
        current_job_id="job-1",
    )
    assert cold.status == PROPERTY_STATUS_COLD
    assert warming.status == PROPERTY_STATUS_WARMING
    assert warming.current_job_id == "job-1"
    assert warming.property_channel_id == cold.property_channel_id


def test_first_seen_at_is_tz_aware() -> None:
    state = _state()
    assert state.first_seen_at.tzinfo is not None


def test_updated_at_is_tz_aware() -> None:
    state = _state()
    assert state.updated_at.tzinfo is not None


# ── InMemoryPropertyStateStore ───────────────────────────────


async def test_in_memory_get_returns_none_for_unknown() -> None:
    store = InMemoryPropertyStateStore()
    assert await store.get("missing") is None


async def test_in_memory_create_if_absent_inserts_when_missing() -> None:
    store = InMemoryPropertyStateStore()
    state = _state()
    result = await store.create_if_absent(state)
    assert result == state
    assert await store.get("p1") == state


async def test_in_memory_create_if_absent_returns_existing() -> None:
    store = InMemoryPropertyStateStore()
    original = _state(customer_id="first")
    await store.create_if_absent(original)
    second = _state(customer_id="second")
    returned = await store.create_if_absent(second)
    # Idempotent: second call sees the row written by the first.
    assert returned == original
    assert returned.customer_id == "first"


async def test_in_memory_update_persists_new_state() -> None:
    store = InMemoryPropertyStateStore()
    initial = await store.create_if_absent(_state())
    updated = dataclasses.replace(
        initial,
        status=PROPERTY_STATUS_QUEUED,
        intent_dedup_key="dedup-1",
    )
    returned = await store.update(updated)
    assert returned == updated
    fetched = await store.get("p1")
    assert fetched == updated
    assert fetched is not None
    assert fetched.status == PROPERTY_STATUS_QUEUED
    assert fetched.intent_dedup_key == "dedup-1"


async def test_in_memory_update_raises_when_row_missing() -> None:
    store = InMemoryPropertyStateStore()
    with pytest.raises(PropertyStateNotFoundError):
        await store.update(_state(property_channel_id="never-seen"))


async def test_in_memory_update_full_replace_overwrites_counters() -> None:
    store = InMemoryPropertyStateStore()
    seed = _state()
    await store.create_if_absent(seed)
    primed = dataclasses.replace(
        seed,
        status=PROPERTY_STATUS_PRIMED,
        conversations_loaded=42,
        cases_extracted=10,
        rules_emitted=7,
        profile_built=True,
        window_days=30,
        last_bootstrap_at=datetime(2026, 5, 25, tzinfo=UTC),
    )
    await store.update(primed)
    fetched = await store.get("p1")
    assert fetched is not None
    assert fetched.conversations_loaded == 42
    assert fetched.cases_extracted == 10
    assert fetched.rules_emitted == 7
    assert fetched.profile_built is True
    assert fetched.window_days == 30


async def test_in_memory_distinguishes_distinct_properties() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(property_channel_id="a"))
    await store.create_if_absent(
        _state(property_channel_id="b", customer_id="other"),
    )
    a = await store.get("a")
    b = await store.get("b")
    assert a is not None and a.customer_id == "cust"
    assert b is not None and b.customer_id == "other"


async def test_in_memory_create_if_absent_returns_existing_object_identity() -> None:
    # The returned row must reflect what is persisted — callers
    # that compare ``returned is argument`` should NOT assume
    # the original argument round-trips when a row already
    # existed.  This guards the contract documented in the
    # Protocol docstring.
    store = InMemoryPropertyStateStore()
    first = _state(customer_id="first")
    await store.create_if_absent(first)
    second = _state(customer_id="second")
    returned = await store.create_if_absent(second)
    assert returned is not second
    assert returned == first


# ── PostgresPropertyStateStore (asyncpg mock) ────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    """Build a fake asyncpg Record dict matching SELECT shape."""
    base: dict[str, Any] = {
        "property_channel_id": "p1",
        "customer_id": "cust",
        "org_id": "org",
        "provider_type": "HOSTAWAY",
        "status": PROPERTY_STATUS_COLD,
        "current_job_id": None,
        "intent_dedup_key": None,
        "conversations_loaded": 0,
        "cases_extracted": 0,
        "rules_emitted": 0,
        "profile_built": False,
        "window_days": None,
        "first_seen_at": datetime(2026, 5, 25, tzinfo=UTC),
        "last_bootstrap_at": None,
        "last_data_event_at": None,
        "last_error": None,
        "retry_count": 0,
        "updated_at": datetime(2026, 5, 25, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _build_pool(
    *,
    fetchrow_results: list[dict[str, Any] | None] | None = None,
) -> tuple[Any, AsyncMock]:
    """Build a fake asyncpg.Pool whose fetchrow plays a script.

    ``fetchrow_results`` is consumed in order across successive
    ``fetchrow`` calls — the same Connection is reused across
    a single ``async with pool.acquire()`` block so the
    create-then-select fallback path can be exercised end to
    end.
    """
    if fetchrow_results is None:
        fetchrow_results = [None]
    conn = MagicMock(name="asyncpg.Connection")
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_results))

    pool = MagicMock(name="asyncpg.Pool")

    class _Acquire:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *exc: Any) -> None:
            return None

    pool.acquire = MagicMock(return_value=_Acquire())
    return pool, conn.fetchrow


async def test_postgres_get_returns_none_when_row_absent() -> None:
    pool, _ = _build_pool(fetchrow_results=[None])
    store = PostgresPropertyStateStore(pool)
    assert await store.get("missing") is None


async def test_postgres_get_maps_row_to_state() -> None:
    pool, fetchrow_mock = _build_pool(fetchrow_results=[_row()])
    store = PostgresPropertyStateStore(pool)
    state = await store.get("p1")
    fetchrow_mock.assert_awaited_once()
    sql = fetchrow_mock.call_args.args[0]
    assert "SELECT" in sql
    assert "FROM property_state" in sql
    assert "WHERE property_channel_id = $1" in sql
    assert state is not None
    assert state.property_channel_id == "p1"
    assert state.customer_id == "cust"
    assert state.status == PROPERTY_STATUS_COLD


async def test_postgres_create_if_absent_returns_inserted_row() -> None:
    pool, fetchrow_mock = _build_pool(fetchrow_results=[_row()])
    store = PostgresPropertyStateStore(pool)
    state = await store.create_if_absent(_state())
    fetchrow_mock.assert_awaited_once()
    sql = fetchrow_mock.call_args.args[0]
    assert "INSERT INTO property_state" in sql
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert "RETURNING" in sql
    assert state.property_channel_id == "p1"


async def test_postgres_create_if_absent_falls_back_to_select() -> None:
    # First fetchrow (INSERT … RETURNING) returns None — the
    # ON CONFLICT path fired.  Second fetchrow (SELECT) returns
    # the row written by the concurrent winner.
    winner_row = _row(customer_id="winner")
    pool, fetchrow_mock = _build_pool(
        fetchrow_results=[None, winner_row],
    )
    store = PostgresPropertyStateStore(pool)
    state = await store.create_if_absent(_state(customer_id="loser"))
    assert fetchrow_mock.await_count == 2
    insert_sql = fetchrow_mock.call_args_list[0].args[0]
    select_sql = fetchrow_mock.call_args_list[1].args[0]
    assert "INSERT INTO property_state" in insert_sql
    assert "SELECT" in select_sql
    assert "WHERE property_channel_id = $1" in select_sql
    assert state.customer_id == "winner"


async def test_postgres_create_if_absent_raises_when_select_also_misses() -> None:
    # Pathological case: INSERT lost the race AND the follow-up
    # SELECT returned nothing.  Surfaces as RuntimeError because
    # it is a serious invariant violation, not a normal flow.
    pool, _ = _build_pool(fetchrow_results=[None, None])
    store = PostgresPropertyStateStore(pool)
    with pytest.raises(RuntimeError, match="insert conflicted"):
        await store.create_if_absent(_state())


async def test_postgres_create_if_absent_passes_all_columns() -> None:
    pool, fetchrow_mock = _build_pool(fetchrow_results=[_row()])
    store = PostgresPropertyStateStore(pool)
    state = _state(status=PROPERTY_STATUS_QUEUED)
    await store.create_if_absent(state)
    args = fetchrow_mock.call_args.args
    # 1 SQL + 18 positional params (every column).
    assert len(args) == 19
    assert args[1] == "p1"  # property_channel_id
    assert args[2] == "cust"  # customer_id
    assert args[3] == "org"  # org_id
    assert args[4] == "HOSTAWAY"  # provider_type
    assert args[5] == PROPERTY_STATUS_QUEUED


async def test_postgres_update_returns_updated_row() -> None:
    updated_row = _row(
        status=PROPERTY_STATUS_PRIMED,
        conversations_loaded=42,
        rules_emitted=7,
    )
    pool, fetchrow_mock = _build_pool(fetchrow_results=[updated_row])
    store = PostgresPropertyStateStore(pool)
    state = await store.update(_state(status=PROPERTY_STATUS_PRIMED))
    sql = fetchrow_mock.call_args.args[0]
    assert "UPDATE property_state" in sql
    assert "WHERE property_channel_id = $1" in sql
    assert "RETURNING" in sql
    assert state.status == PROPERTY_STATUS_PRIMED
    assert state.conversations_loaded == 42
    assert state.rules_emitted == 7


async def test_postgres_update_raises_property_state_not_found() -> None:
    pool, _ = _build_pool(fetchrow_results=[None])
    store = PostgresPropertyStateStore(pool)
    with pytest.raises(PropertyStateNotFoundError):
        await store.update(_state(property_channel_id="never-seen"))


async def test_postgres_update_passes_all_columns() -> None:
    pool, fetchrow_mock = _build_pool(fetchrow_results=[_row()])
    store = PostgresPropertyStateStore(pool)
    state = _state(status=PROPERTY_STATUS_FAILED)
    state = dataclasses.replace(state, last_error="boom")
    await store.update(state)
    args = fetchrow_mock.call_args.args
    # 1 SQL + 18 positional params (PK + 17 SET targets).
    assert len(args) == 19
    assert args[1] == "p1"  # property_channel_id (PK in WHERE)
    assert args[5] == PROPERTY_STATUS_FAILED
    assert args[16] == "boom"  # last_error


async def test_postgres_get_preserves_null_org_id() -> None:
    pool, _ = _build_pool(fetchrow_results=[_row(org_id=None)])
    store = PostgresPropertyStateStore(pool)
    state = await store.get("p1")
    assert state is not None
    assert state.org_id is None


async def test_postgres_get_preserves_timestamps() -> None:
    bootstrap_at = datetime(2026, 5, 25, 12, 30, tzinfo=UTC)
    pool, _ = _build_pool(
        fetchrow_results=[_row(last_bootstrap_at=bootstrap_at)],
    )
    store = PostgresPropertyStateStore(pool)
    state = await store.get("p1")
    assert state is not None
    assert state.last_bootstrap_at == bootstrap_at
