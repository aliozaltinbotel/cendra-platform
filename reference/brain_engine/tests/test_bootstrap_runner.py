"""Tests for :class:`BootstrapRunner` state transitions.

The runner is the bridge between :func:`request_bootstrap` (which
records a ``queued`` intent) and the real bootstrap pipeline.  It
owns the rest of the property_state FSM:

* queued → warming (set when the workload starts)
* warming → primed (set on pipeline success with harvest counters)
* warming → failed (set on pipeline exception with last_error + retry++)

The pipeline is mocked with a minimal fake that satisfies the
``bootstrap_fast`` keyword signature the runner depends on.  We do
NOT import :class:`OnboardingBootstrapPipeline` here — it would
pull half the engine into the test rig and the runner only
cares about the keyword surface plus the report attributes,
which is precisely what a Protocol-shaped fake provides.

Each test also confirms the runner is **resilient** to the row
disappearing between transitions: if some out-of-band operator
deletes the property_state row mid-flight, the runner logs and
returns instead of raising — preventing one bad row from killing
the worker task.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    TENANT_SOURCE_REGISTRY,
    BootstrapRunner,
    InMemoryPropertyStateStore,
    PropertyState,
    TenantContext,
)


def _tenant() -> TenantContext:
    return TenantContext(
        customer_id="cust",
        org_id="org",
        provider_type="HOSTAWAY",
        property_channel_id="p1",
        source=TENANT_SOURCE_REGISTRY,
    )


def _queued_state(
    *,
    property_channel_id: str = "p1",
    retry_count: int = 0,
) -> PropertyState:
    return PropertyState(
        property_channel_id=property_channel_id,
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_QUEUED,
        current_job_id="job-1",
        retry_count=retry_count,
    )


@dataclass
class _FakeReport:
    """Tiny stand-in for :class:`BootstrapPropertyReport`."""

    conversations_loaded: int = 0
    cases_extracted: int = 0
    rules_emitted: int = 0
    profile_built: bool = False


class _RecordingPipeline:
    """Captures bootstrap_fast kwargs + returns a configured report."""

    def __init__(
        self,
        *,
        report: _FakeReport | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._report = report or _FakeReport()
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def bootstrap_fast(self, **kwargs: Any) -> _FakeReport:
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self._report


def _fixed_clock(ts: datetime) -> Any:
    def clock() -> datetime:
        return ts

    return clock


# ── workload_factory shape ──────────────────────────────────────


def test_workload_factory_returns_callable() -> None:
    store = InMemoryPropertyStateStore()
    pipeline = _RecordingPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    assert callable(workload)


# ── Success path: queued → warming → primed ─────────────────────


async def test_success_transitions_to_primed_with_counters() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline(
        report=_FakeReport(
            conversations_loaded=42,
            cases_extracted=10,
            rules_emitted=7,
            profile_built=True,
        ),
    )
    clock_ts = datetime(2026, 5, 25, 14, 0, tzinfo=UTC)
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
        clock=_fixed_clock(clock_ts),
    )
    factory = runner.workload_factory(_tenant(), window_days=730)
    workload = factory(_queued_state(), "job-1")
    await workload()
    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_PRIMED
    assert final.conversations_loaded == 42
    assert final.cases_extracted == 10
    assert final.rules_emitted == 7
    assert final.profile_built is True
    assert final.window_days == 730
    assert final.last_bootstrap_at == clock_ts
    assert final.updated_at == clock_ts
    assert final.current_job_id is None
    assert final.retry_count == 0
    assert final.last_error is None


async def test_success_passes_tenant_overrides_to_pipeline() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-77")
    await workload()
    assert len(pipeline.calls) == 1
    kwargs = pipeline.calls[0]
    assert kwargs["property_id"] == "p1"
    assert kwargs["days"] == 30
    assert kwargs["customer_id_override"] == "cust"
    assert kwargs["org_id_override"] == "org"
    assert kwargs["provider_type_override"] == "HOSTAWAY"
    assert kwargs["job_id"] == "job-77"


async def test_success_resets_retry_count_to_zero() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state(retry_count=3))
    pipeline = _RecordingPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(retry_count=3), "job-1")
    await workload()
    final = await store.get("p1")
    assert final is not None
    assert final.retry_count == 0


# ── Failure path: queued → warming → failed ─────────────────────


async def test_pipeline_exception_transitions_to_failed() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline(
        raise_exc=RuntimeError("network down"),
    )
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    await workload()
    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_FAILED
    assert final.last_error is not None
    assert "RuntimeError" in final.last_error
    assert "network down" in final.last_error
    assert final.current_job_id is None
    assert final.retry_count == 1


async def test_failure_increments_existing_retry_count() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state(retry_count=4))
    pipeline = _RecordingPipeline(raise_exc=ValueError("bad data"))
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(retry_count=4), "job-1")
    await workload()
    final = await store.get("p1")
    assert final is not None
    assert final.retry_count == 5


# ── Resilience: row vanishes mid-flight ─────────────────────────


async def test_warming_transition_swallows_vanished_row() -> None:
    # Row created so we have something to seed, then deleted
    # via private store handle so the warming transition trips
    # PropertyStateNotFoundError.
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    store._rows.clear()  # type: ignore[attr-defined]
    pipeline = _RecordingPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    # Must not raise — runner logs and bails out.
    await workload()
    # Pipeline must NOT have been called: the warming transition
    # never succeeded so the runner short-circuited.
    assert pipeline.calls == []


async def test_primed_transition_swallows_vanished_row() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline()

    # Custom store that deletes the row right after the warming
    # transition completes.  Need to do this inside the runner
    # flow, so monkeypatch the update method to drop the row
    # before returning.
    original_update = store.update
    update_count = {"n": 0}

    async def update_then_drop(state: PropertyState) -> PropertyState:
        result = await original_update(state)
        update_count["n"] += 1
        if state.status == PROPERTY_STATUS_WARMING:
            store._rows.clear()  # type: ignore[attr-defined]
        return result

    store.update = update_then_drop  # type: ignore[method-assign]

    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    await workload()
    # warming transition succeeded; pipeline ran; primed
    # transition raised PropertyStateNotFoundError but was
    # swallowed.  update was called once (warming).
    assert update_count["n"] == 1
    assert pipeline.calls != []


async def test_failed_transition_swallows_vanished_row() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline(raise_exc=RuntimeError("boom"))

    original_update = store.update

    async def update_then_drop(state: PropertyState) -> PropertyState:
        result = await original_update(state)
        if state.status == PROPERTY_STATUS_WARMING:
            store._rows.clear()  # type: ignore[attr-defined]
        return result

    store.update = update_then_drop  # type: ignore[method-assign]

    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    # Pipeline raises, runner tries failed transition, row is
    # gone — must swallow and return without raising.
    await workload()


# ── Counter coercion / report-shape resilience ─────────────────


async def test_runner_coerces_missing_report_fields_to_zero() -> None:
    # Pipeline returns a stripped report — runner must fall back
    # gracefully on getattr().
    @dataclass
    class _BareReport:
        pass  # no fields at all

    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _RecordingPipeline(report=cast(Any, _BareReport()))
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")
    await workload()
    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_PRIMED
    assert final.conversations_loaded == 0
    assert final.cases_extracted == 0
    assert final.rules_emitted == 0
    assert final.profile_built is False


# ── Frozen-state contract preserved across replace() ────────────


async def test_state_is_immutable_after_transitions() -> None:
    store = InMemoryPropertyStateStore()
    seed = _queued_state()
    await store.create_if_absent(seed)
    pipeline = _RecordingPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(seed, "job-1")
    await workload()
    # The seed object itself never mutates — replace() built new
    # objects throughout the runner.  This is a regression guard
    # against silently mutating frozen dataclasses (would raise
    # FrozenInstanceError if the runner ever tried).
    assert seed.status == PROPERTY_STATUS_QUEUED
    with pytest.raises(dataclasses.FrozenInstanceError):
        seed.status = PROPERTY_STATUS_PRIMED  # type: ignore[misc]


# ── Timeout: a slow run is cancelled and marked failed ──────────


class _SlowPipeline:
    """bootstrap_fast that never finishes within the runner's ceiling."""

    def __init__(self) -> None:
        self.calls = 0

    async def bootstrap_fast(self, **_kwargs: Any) -> _FakeReport:
        self.calls += 1
        await asyncio.sleep(10)  # far longer than the test timeout
        return _FakeReport()


async def test_timeout_cancels_run_and_marks_failed() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_state())
    pipeline = _SlowPipeline()
    runner = BootstrapRunner(
        pipeline=cast(Any, pipeline),
        state_store=store,
        timeout_seconds=0.05,
    )
    factory = runner.workload_factory(_tenant(), window_days=30)
    workload = factory(_queued_state(), "job-1")

    await workload()

    assert pipeline.calls == 1
    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_FAILED
    assert final.retry_count == 1
    assert final.current_job_id is None
    assert "TimeoutError" in (final.last_error or "")
