"""Tests for the Stage 1 intent path of :class:`AutoBootstrapTrigger`.

When a :class:`PropertyStateStore` is wired
(``PROPERTY_STATE_ENABLED`` on) the Phase 4 trigger stops using its
legacy in-proc ``asyncio.create_task`` dedup and instead delegates
to :func:`submit_bootstrap_intent` → ``request_bootstrap``.  The
contract under test:

* ``maybe_fire`` returns ``True`` and records a ``queued`` row on a
  cold property; the dispatcher is invoked exactly once.
* A second ``maybe_fire`` for the same property dedups against the
  in-flight ``property_state`` row — returns ``False``, no second
  dispatch.
* The ``env_default`` source and blank-customer guards still skip
  before the intent path is reached.
* With the default dispatcher (synthesised when none is supplied)
  the workload runs the pipeline and drives the row to ``primed``
  with the harvest counters — proving the runner wiring end to end.
* When no ``state_store`` is wired the legacy path is byte-for-byte
  unchanged (profile probe consulted, ``True`` on a fresh miss).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from brain_engine.tenants import (
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    TENANT_SOURCE_ENV_DEFAULT,
    TENANT_SOURCE_REGISTRY,
    AutoBootstrapTrigger,
    BootstrapIntentMessage,
    BootstrapWorkload,
    InMemoryPropertyStateStore,
    InMemoryPropertyTenantRegistry,
    TenantContext,
)


@dataclass
class _FakeProfile:
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


class _SpyDispatcher:
    """Records dispatch calls without running the workload.

    Lets the intent-layer assertions stay deterministic: the
    ``property_state`` row is left in ``queued`` because the
    background runner never starts.
    """

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


def _pipeline(*, report: Any | None = None) -> Any:
    pipeline = MagicMock(name="OnboardingBootstrapPipeline")
    pipeline.bootstrap_fast = AsyncMock(
        return_value=report
        or SimpleNamespace(
            conversations_loaded=12,
            cases_extracted=4,
            rules_emitted=1,
            profile_built=True,
        ),
    )
    return pipeline


async def _drain() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_intent_path_enqueues_cold_property() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: _pipeline(),
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
        state_store=store,
        dispatcher=dispatcher,
    )

    fired = await trigger.maybe_fire("prop1", _ctx())

    assert fired is True
    assert len(dispatcher.calls) == 1
    row = await store.get("prop1")
    assert row is not None
    assert row.status == PROPERTY_STATUS_QUEUED
    assert row.customer_id == "cust"


async def test_intent_path_dedups_in_flight() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: _pipeline(),
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
        state_store=store,
        dispatcher=dispatcher,
    )

    first = await trigger.maybe_fire("prop1", _ctx())
    second = await trigger.maybe_fire("prop1", _ctx())

    assert first is True
    assert second is False
    assert len(dispatcher.calls) == 1


async def test_intent_path_env_default_source_skips() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: _pipeline(),
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
        state_store=store,
        dispatcher=dispatcher,
    )

    fired = await trigger.maybe_fire(
        "prop1", _ctx(source=TENANT_SOURCE_ENV_DEFAULT),
    )

    assert fired is False
    assert dispatcher.calls == []
    assert await store.get("prop1") is None


async def test_intent_path_blank_customer_skips() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _SpyDispatcher()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: _pipeline(),
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
        state_store=store,
        dispatcher=dispatcher,
    )

    fired = await trigger.maybe_fire("prop1", _ctx(customer_id=""))

    assert fired is False
    assert dispatcher.calls == []
    assert await store.get("prop1") is None


async def test_default_dispatcher_runs_pipeline_to_primed() -> None:
    store = InMemoryPropertyStateStore()
    pipeline = _pipeline()
    # No dispatcher → the constructor synthesises a real
    # AsyncioBootstrapDispatcher, so the workload actually runs.
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=_FakeProfileStore(),
        registry=InMemoryPropertyTenantRegistry(),
        state_store=store,
    )

    fired = await trigger.maybe_fire("prop1", _ctx())
    await _drain()

    assert fired is True
    pipeline.bootstrap_fast.assert_awaited_once()
    row = await store.get("prop1")
    assert row is not None
    assert row.status == PROPERTY_STATUS_PRIMED
    assert row.conversations_loaded == 12
    assert row.cases_extracted == 4
    assert row.rules_emitted == 1
    assert row.last_bootstrap_at is not None


async def test_legacy_path_unchanged_without_state_store() -> None:
    pipeline = _pipeline()
    profile_store = _FakeProfileStore()
    trigger = AutoBootstrapTrigger(
        pipeline_getter=lambda: pipeline,
        profile_store=profile_store,
        registry=InMemoryPropertyTenantRegistry(),
    )

    fired = await trigger.maybe_fire("prop1", _ctx())
    await _drain()

    # Legacy path: scheduled its own create_task and ran the
    # pipeline directly (no property_state involved).
    assert fired is True
    pipeline.bootstrap_fast.assert_awaited_once()
