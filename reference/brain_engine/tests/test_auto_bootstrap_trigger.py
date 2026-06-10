"""Tests for :class:`AutoBootstrapTrigger` (Phase 4).

The trigger sits behind :class:`TenantResolverMiddleware` and
fires a background ``bootstrap_fast`` for properties the brain
has never primed.  The contract under test:

* Skip when ``property_channel_id`` is empty / whitespace.
* Skip when ``tenant_context.source == env_default`` — we never
  bootstrap into the env-default tenant because it is almost
  certainly the wrong workspace.
* Skip when ``tenant_context.customer_id`` is blank.
* Skip when the property already has a :class:`PropertyProfile`
  in the store (already bootstrapped).
* Skip when the same ``property_channel_id`` is already in flight
  (in-process dedup with :class:`asyncio.Lock`).
* Skip when the pipeline getter returns ``None`` (pipeline not
  wired yet).
* When all checks pass, schedule a fire-and-forget task and
  return ``True``.
* The pending set is cleared after the background task finishes,
  even on exception, so a subsequent miss re-fires.
* Exceptions inside the background bootstrap are logged but
  never propagated.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.tenants import (
    TENANT_SOURCE_ENV_DEFAULT,
    TENANT_SOURCE_REGISTRY,
    AutoBootstrapTrigger,
    TenantContext,
)


@dataclass
class _FakeProfile:
    """Minimal stand-in for :class:`PropertyProfile`."""

    property_channel_id: str
    built_at: datetime


class _FakeProfileStore:
    """In-process profile store double — keyed by channel id."""

    def __init__(self) -> None:
        self._rows: dict[str, _FakeProfile] = {}

    async def get(self, property_channel_id: str) -> _FakeProfile | None:
        return self._rows.get(property_channel_id)

    def insert(self, property_channel_id: str) -> None:
        self._rows[property_channel_id] = _FakeProfile(
            property_channel_id=property_channel_id,
            built_at=datetime.now(UTC),
        )


def _ctx(
    *,
    customer_id: str = "cust",
    org_id: str | None = "org",
    provider_type: str = "LODGIFY",
    property_channel_id: str = "prop1",
    source: str = TENANT_SOURCE_REGISTRY,
) -> TenantContext:
    return TenantContext(
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        property_channel_id=property_channel_id,
        source=source,
    )


def _build_pipeline(
    *,
    raises: BaseException | None = None,
) -> Any:
    pipeline = MagicMock(name="OnboardingBootstrapPipeline")
    if raises is not None:
        pipeline.bootstrap_fast = AsyncMock(side_effect=raises)
    else:
        report = MagicMock(
            conversations_loaded=12,
            cases_extracted=4,
            rules_emitted=1,
        )
        pipeline.bootstrap_fast = AsyncMock(return_value=report)
    return pipeline


async def _drain() -> None:
    """Let the event loop run scheduled tasks to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


def _registry() -> Any:
    """Fresh InMemoryPropertyTenantRegistry for trigger tests."""
    from brain_engine.tenants import InMemoryPropertyTenantRegistry

    return InMemoryPropertyTenantRegistry()


async def test_blank_property_channel_id_skips() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    assert await trigger.maybe_fire("", _ctx()) is False
    pipeline.bootstrap_fast.assert_not_awaited()


async def test_env_default_source_skips() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    fired = await trigger.maybe_fire(
        "prop1",
        _ctx(source=TENANT_SOURCE_ENV_DEFAULT),
    )
    assert fired is False
    pipeline.bootstrap_fast.assert_not_awaited()


async def test_blank_customer_id_skips() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    fired = await trigger.maybe_fire(
        "prop1",
        _ctx(customer_id=""),
    )
    assert fired is False
    pipeline.bootstrap_fast.assert_not_awaited()


async def test_existing_profile_skips() -> None:
    store = _FakeProfileStore()
    store.insert("prop1")
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=store,
        registry=_registry(),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is False
    pipeline.bootstrap_fast.assert_not_awaited()


async def test_no_pipeline_skips() -> None:
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: None,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is False


async def test_happy_path_fires_and_completes() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    fired = await trigger.maybe_fire("prop1", _ctx())
    assert fired is True
    await _drain()
    pipeline.bootstrap_fast.assert_awaited_once()
    call_kwargs = pipeline.bootstrap_fast.call_args.kwargs
    assert call_kwargs["property_id"] == "prop1"
    assert call_kwargs["customer_id_override"] == "cust"
    assert call_kwargs["org_id_override"] == "org"
    assert call_kwargs["provider_type_override"] == "LODGIFY"
    assert call_kwargs["mine_patterns_inline"] is False
    assert call_kwargs["dry_run"] is False


async def test_pending_dedup_blocks_concurrent_fires() -> None:
    pipeline = _build_pipeline()

    # Slow bootstrap so the dedup window is observable.
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_bootstrap(**_kwargs: Any) -> Any:
        started.set()
        await release.wait()
        return MagicMock(
            conversations_loaded=1, cases_extracted=0, rules_emitted=0,
        )

    pipeline.bootstrap_fast = AsyncMock(side_effect=slow_bootstrap)
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    first = await trigger.maybe_fire("prop1", _ctx())
    await started.wait()
    second = await trigger.maybe_fire("prop1", _ctx())
    third = await trigger.maybe_fire("prop1", _ctx())
    release.set()
    await _drain()
    assert (first, second, third) == (True, False, False)
    assert pipeline.bootstrap_fast.await_count == 1


async def test_pending_cleared_after_success_so_refire_works() -> None:
    pipeline = _build_pipeline()
    store = _FakeProfileStore()
    # cooldown=0 isolates this test from the Postgres cooldown layer
    # so it can prove the pending-set clears on done.
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=store,
        registry=_registry(),
        cooldown=timedelta(0),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    # Profile still not inserted (FakeProfileStore is the test
    # double; the real pipeline would insert it).  Trigger should
    # still re-fire because the pending set was cleared on done.
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    assert pipeline.bootstrap_fast.await_count == 2


async def test_pending_cleared_after_exception_so_refire_works() -> None:
    pipeline = _build_pipeline(raises=RuntimeError("graphql down"))
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
        cooldown=timedelta(0),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    # Failure was swallowed → no exception leaked into the request
    # path; pending set was cleared → re-fire works.
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    assert pipeline.bootstrap_fast.await_count == 2


async def test_distinct_properties_dedup_independently() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    a = await trigger.maybe_fire(
        "propA", _ctx(property_channel_id="propA"),
    )
    b = await trigger.maybe_fire(
        "propB", _ctx(property_channel_id="propB"),
    )
    assert (a, b) == (True, True)
    await _drain()
    assert pipeline.bootstrap_fast.await_count == 2


async def test_org_id_none_is_propagated_as_none() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    fired = await trigger.maybe_fire("prop1", _ctx(org_id=None))
    assert fired is True
    await _drain()
    call_kwargs = pipeline.bootstrap_fast.call_args.kwargs
    assert call_kwargs["org_id_override"] is None


async def test_bootstrap_days_passed_to_pipeline() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
        bootstrap_days=7,
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    call_kwargs = pipeline.bootstrap_fast.call_args.kwargs
    assert call_kwargs["days"] == 7


async def test_bootstrap_days_lower_bound_is_one() -> None:
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: None,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
        bootstrap_days=0,
    )
    assert trigger._bootstrap_days == 1  # type: ignore[attr-defined]


async def test_pipeline_called_even_without_org_id() -> None:
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    fired = await trigger.maybe_fire(
        "prop1", _ctx(org_id=None, provider_type="HOSTAWAY"),
    )
    assert fired is True
    await _drain()
    call_kwargs = pipeline.bootstrap_fast.call_args.kwargs
    assert call_kwargs["provider_type_override"] == "HOSTAWAY"


async def test_profile_inserted_during_dispatch_blocks_refire() -> None:
    """Real pipeline writes a profile — refire honours the new state."""
    pipeline = _build_pipeline()
    store = _FakeProfileStore()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=store,
        registry=_registry(),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    store.insert("prop1")
    assert await trigger.maybe_fire("prop1", _ctx()) is False
    assert pipeline.bootstrap_fast.await_count == 1


@pytest.mark.asyncio
async def test_trigger_with_concurrent_first_calls_only_fires_once() -> None:
    """Two coroutines hitting maybe_fire at the same time → 1 dispatch."""
    pipeline = _build_pipeline()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=_registry(),
    )
    results = await asyncio.gather(
        trigger.maybe_fire("prop1", _ctx()),
        trigger.maybe_fire("prop1", _ctx()),
    )
    await _drain()
    assert sorted(results) == [False, True]
    assert pipeline.bootstrap_fast.await_count == 1


# ── Cooldown ──────────────────────────────────────────────────────


async def test_cooldown_blocks_immediate_refire() -> None:
    """First fire records attempt → second within cooldown skips."""
    pipeline = _build_pipeline()
    registry = _registry()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=registry,
        cooldown=timedelta(hours=1),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    # Cooldown was recorded by _run → second call must skip even
    # though the pending set was cleared.
    assert await trigger.maybe_fire("prop1", _ctx()) is False
    await _drain()
    assert pipeline.bootstrap_fast.await_count == 1


async def test_cooldown_skips_when_attempt_recent() -> None:
    """Pre-seed last_attempt now() → first call already inside cooldown."""
    pipeline = _build_pipeline()
    registry = _registry()
    await registry.record_auto_attempt("prop1")
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=registry,
        cooldown=timedelta(hours=1),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is False
    pipeline.bootstrap_fast.assert_not_awaited()


async def test_cooldown_expiry_allows_refire() -> None:
    """Attempt older than cooldown → re-fire is permitted."""
    pipeline = _build_pipeline()
    registry = _registry()
    # Inject an "old" attempt directly so we do not need to sleep.
    registry._last_attempts["prop1"] = datetime.now(UTC) - timedelta(  # type: ignore[attr-defined]
        hours=2,
    )
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=registry,
        cooldown=timedelta(hours=1),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    pipeline.bootstrap_fast.assert_awaited_once()


async def test_cooldown_recorded_on_failure_too() -> None:
    """A failed bootstrap still marks last_attempt so we don't hammer."""
    pipeline = _build_pipeline(raises=RuntimeError("graphql down"))
    registry = _registry()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=registry,
        cooldown=timedelta(hours=1),
    )
    assert await trigger.maybe_fire("prop1", _ctx()) is True
    await _drain()
    # Even though the bootstrap raised, the attempt timestamp was
    # written → second call inside cooldown skips.
    assert await registry.get_last_auto_attempt("prop1") is not None
    assert await trigger.maybe_fire("prop1", _ctx()) is False
