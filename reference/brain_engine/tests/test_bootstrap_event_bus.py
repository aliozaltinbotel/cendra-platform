"""Unit tests for the bootstrap audit-log event bus.

Pins the contract the HTTP layer relies on:

* :class:`InMemoryBootstrapEventBus` records every emit, supports
  paginated history, kind filters, and a live :meth:`stream`
  generator that wakes up when fresh events land.
* :class:`RedisBootstrapEventBus` uses Redis Streams + a summary
  hash; we exercise it against ``fakeredis.aioredis`` so the test
  suite never touches a real Redis.
* :class:`JobSummary` folds every supported :class:`EventKind`,
  including reason breakdowns and the terminal ``finished_at``
  transition.
* :class:`NullBootstrapEventBus` is a true no-op — every method
  short-circuits with the expected empty / ``None`` return.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from brain_engine.onboarding.event_bus import (
    BOOTSTRAP_EVENT_STREAM_TTL_SECONDS,
    BootstrapEvent,
    EventKind,
    InMemoryBootstrapEventBus,
    JobSummary,
    NullBootstrapEventBus,
    RedisBootstrapEventBus,
    SkipReason,
    _apply_event,
    _empty_summary,
    make_event,
)


def _event(
    *,
    job_id: str = "job-1",
    property_id: str = "323133",
    kind: EventKind = EventKind.CONVERSATION_LOADED,
    payload: dict | None = None,
    ts: datetime | None = None,
) -> BootstrapEvent:
    return BootstrapEvent(
        ts=ts or datetime.now(timezone.utc),
        job_id=job_id,
        property_id=property_id,
        kind=kind,
        payload=payload or {},
    )


# ── BootstrapEvent invariants ───────────────────────────────────


def test_bootstrap_event_requires_tz_aware_ts() -> None:
    with pytest.raises(ValueError):
        BootstrapEvent(
            ts=datetime(2026, 1, 1),
            job_id="job",
            property_id="323133",
            kind=EventKind.JOB_STARTED,
        )


def test_bootstrap_event_requires_job_and_property_ids() -> None:
    with pytest.raises(ValueError):
        BootstrapEvent(
            ts=datetime.now(timezone.utc),
            job_id="",
            property_id="323133",
            kind=EventKind.JOB_STARTED,
        )
    with pytest.raises(ValueError):
        BootstrapEvent(
            ts=datetime.now(timezone.utc),
            job_id="job",
            property_id="",
            kind=EventKind.JOB_STARTED,
        )


def test_bootstrap_event_to_dict_round_trips_payload() -> None:
    event = _event(payload={"conversation_id": "c-1", "reason": "x"})
    data = event.to_dict()
    assert data["job_id"] == "job-1"
    assert data["property_id"] == "323133"
    assert data["kind"] == EventKind.CONVERSATION_LOADED.value
    assert data["payload"] == {"conversation_id": "c-1", "reason": "x"}


# ── make_event helper ────────────────────────────────────────────


def test_make_event_stamps_current_utc_instant() -> None:
    before = datetime.now(timezone.utc)
    event = make_event(
        job_id="job", property_id="323133", kind=EventKind.JOB_STARTED,
    )
    after = datetime.now(timezone.utc)
    assert before <= event.ts <= after
    assert event.payload == {}


# ── _apply_event reducer ─────────────────────────────────────────


def test_apply_event_folds_every_supported_kind() -> None:
    started = datetime.now(timezone.utc)
    summary = _empty_summary(
        job_id="job",
        property_id="323133",
        started_at=started,
    )
    summary = _apply_event(summary, _event(kind=EventKind.JOB_STARTED))
    assert summary.status == "running"

    summary = _apply_event(
        summary, _event(kind=EventKind.CONVERSATION_LOADED),
    )
    summary = _apply_event(
        summary,
        _event(
            kind=EventKind.CONVERSATION_SKIPPED,
            payload={"reason": SkipReason.EMPTY_THREAD.value},
        ),
    )
    summary = _apply_event(
        summary,
        _event(
            kind=EventKind.CASE_SKIPPED,
            payload={"reason": SkipReason.MISSING_OUTCOME.value},
        ),
    )
    summary = _apply_event(summary, _event(kind=EventKind.CASE_EXTRACTED))
    summary = _apply_event(summary, _event(kind=EventKind.RULE_EMITTED))
    summary = _apply_event(
        summary,
        _event(
            kind=EventKind.RULE_BLOCKED,
            payload={"reason": SkipReason.LOW_CONFIDENCE.value},
        ),
    )
    summary = _apply_event(summary, _event(kind=EventKind.PROFILE_BUILT))

    assert summary.counts == {
        "conversations_loaded": 1,
        "conversations_skipped": 1,
        "cases_extracted": 1,
        "cases_skipped": 1,
        "rules_emitted": 1,
        "rules_blocked": 1,
        "profiles_built": 1,
    }
    assert summary.skip_breakdown == {
        SkipReason.EMPTY_THREAD.value: 1,
        SkipReason.MISSING_OUTCOME.value: 1,
    }
    assert summary.rule_block_breakdown == {
        SkipReason.LOW_CONFIDENCE.value: 1,
    }

    done = _event(kind=EventKind.JOB_DONE)
    summary = _apply_event(summary, done)
    assert summary.status == "done"
    assert summary.finished_at == done.ts


def test_apply_event_marks_failed_with_error() -> None:
    summary = _empty_summary(
        job_id="job", property_id="323133",
        started_at=datetime.now(timezone.utc),
    )
    summary = _apply_event(
        summary,
        _event(kind=EventKind.JOB_FAILED, payload={"error": "boom"}),
    )
    assert summary.status == "failed"
    assert summary.last_error == "boom"
    assert summary.finished_at is not None


# ── InMemoryBootstrapEventBus ────────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_bus_history_and_summary() -> None:
    bus = InMemoryBootstrapEventBus()
    for _ in range(3):
        await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(
        _event(
            kind=EventKind.CONVERSATION_SKIPPED,
            payload={"reason": SkipReason.MISSING_DATES.value},
        ),
    )
    await bus.emit(_event(kind=EventKind.JOB_DONE))

    history = await bus.history("job-1", since=0, limit=10)
    assert len(history) == 5
    assert [e.kind for e in history][:3] == [
        EventKind.CONVERSATION_LOADED
    ] * 3

    sliced = await bus.history("job-1", since=3, limit=10)
    assert len(sliced) == 2
    assert sliced[0].kind is EventKind.CONVERSATION_SKIPPED

    filtered = await bus.history(
        "job-1", since=0, limit=10,
        kinds=(EventKind.CONVERSATION_SKIPPED,),
    )
    assert len(filtered) == 1

    summary = await bus.summary("job-1")
    assert summary is not None
    assert summary.status == "done"
    assert summary.counts["conversations_loaded"] == 3
    assert summary.skip_breakdown == {
        SkipReason.MISSING_DATES.value: 1,
    }


@pytest.mark.asyncio
async def test_in_memory_bus_stream_yields_until_terminal_event() -> None:
    bus = InMemoryBootstrapEventBus()
    collected: list[BootstrapEvent] = []

    async def _consumer() -> None:
        async for event in bus.stream("job-1"):
            collected.append(event)

    consumer = asyncio.create_task(_consumer())
    await asyncio.sleep(0)
    await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(_event(kind=EventKind.JOB_DONE))
    await asyncio.wait_for(consumer, timeout=2.0)

    assert [e.kind for e in collected] == [
        EventKind.CONVERSATION_LOADED,
        EventKind.JOB_DONE,
    ]


@pytest.mark.asyncio
async def test_in_memory_bus_summary_unknown_job_is_none() -> None:
    bus = InMemoryBootstrapEventBus()
    assert await bus.summary("missing") is None


# ── NullBootstrapEventBus ────────────────────────────────────────


@pytest.mark.asyncio
async def test_null_bus_is_total_noop() -> None:
    bus = NullBootstrapEventBus()
    await bus.emit(_event())
    assert await bus.history("job-1", since=0, limit=10) == ()
    assert await bus.summary("job-1") is None
    yielded = [e async for e in bus.stream("job-1")]
    assert yielded == []


# ── RedisBootstrapEventBus over fakeredis ────────────────────────


@pytest.mark.asyncio
async def test_redis_bus_emit_and_history_round_trip() -> None:
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    client = fakeredis.FakeRedis()
    bus = RedisBootstrapEventBus(client)
    for _ in range(2):
        await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(
        _event(
            kind=EventKind.CONVERSATION_SKIPPED,
            payload={
                "reason": SkipReason.NO_PM_RESPONSE_AFTER_GUEST.value,
            },
        ),
    )
    await bus.emit(_event(kind=EventKind.JOB_DONE))

    history = await bus.history("job-1", since=0, limit=100)
    assert [e.kind for e in history] == [
        EventKind.CONVERSATION_LOADED,
        EventKind.CONVERSATION_LOADED,
        EventKind.CONVERSATION_SKIPPED,
        EventKind.JOB_DONE,
    ]

    filtered = await bus.history(
        "job-1", since=0, limit=100,
        kinds=(EventKind.CONVERSATION_SKIPPED,),
    )
    assert len(filtered) == 1
    assert filtered[0].payload["reason"] == (
        SkipReason.NO_PM_RESPONSE_AFTER_GUEST.value
    )

    summary = await bus.summary("job-1")
    assert summary is not None
    assert summary.status == "done"
    assert summary.counts["conversations_loaded"] == 2
    assert summary.skip_breakdown == {
        SkipReason.NO_PM_RESPONSE_AFTER_GUEST.value: 1,
    }


@pytest.mark.asyncio
async def test_redis_bus_emit_failure_is_swallowed() -> None:
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    client = fakeredis.FakeRedis()

    class _BrokenClient:
        """xadd raises — emit must still complete without bubbling."""

        async def xadd(self, *args, **kwargs):  # noqa: ANN001
            raise RuntimeError("redis is down")

        async def expire(self, *args, **kwargs):  # noqa: ANN001
            return True

        async def hset(self, *args, **kwargs):  # noqa: ANN001
            return 1

        async def hget(self, *args, **kwargs):  # noqa: ANN001
            return None

        async def xrange(self, *args, **kwargs):  # noqa: ANN001
            return []

    broken_bus = RedisBootstrapEventBus(_BrokenClient())
    await broken_bus.emit(_event())  # must not raise
    assert await broken_bus.summary("job-1") is None

    good_bus = RedisBootstrapEventBus(client)
    await good_bus.emit(_event(kind=EventKind.JOB_STARTED))
    summary = await good_bus.summary("job-1")
    assert summary is not None
    assert summary.status == "running"


def test_bootstrap_event_stream_ttl_is_24_hours() -> None:
    """Doc invariant — Redis keys age out after one day."""
    assert BOOTSTRAP_EVENT_STREAM_TTL_SECONDS == 24 * 60 * 60


# ── JobSummary serialisation ─────────────────────────────────────


def test_job_summary_to_dict_includes_all_fields() -> None:
    started = datetime(2026, 5, 12, 10, tzinfo=timezone.utc)
    summary = JobSummary(
        job_id="job",
        property_id="323133",
        started_at=started,
        finished_at=None,
        status="running",
        counts={"cases_extracted": 1},
        skip_breakdown={SkipReason.EMPTY_THREAD.value: 2},
        rule_block_breakdown={},
        last_error="",
    )
    data = summary.to_dict()
    assert data["job_id"] == "job"
    assert data["started_at"] == started.isoformat()
    assert data["finished_at"] is None
    assert data["counts"] == {"cases_extracted": 1}
    assert data["skip_breakdown"] == {SkipReason.EMPTY_THREAD.value: 2}
