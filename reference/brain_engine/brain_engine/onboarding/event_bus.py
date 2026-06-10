"""Realtime audit log for the bootstrap pipeline.

Mümin 2026-05-12: the bootstrap pipeline silently drops a third
of every property's archive (cases / rules) without telling the
operator *which* rows went missing or *why*.  The operator sees
``cases_skipped: 535`` and a single ``rules_emitted: 3`` and is
forced to read pod logs to figure out what actually happened.

This module is the foundation for the realtime fix: every
ingestion decision becomes a structured event the operator can
poll or stream over HTTP.  The pipeline emits one
:class:`BootstrapEvent` per conversation loaded, per case
extracted or skipped, per rule emitted or blocked.  Events
persist for 24 hours so the operator can audit a completed run
hours after it finishes.

Architectural surface
---------------------

* :class:`BootstrapEvent` — frozen value object, JSON-safe
  payload.
* :class:`EventKind` — enumerated event taxonomy.
* :class:`SkipReason` — enumerated skip / block taxonomy.
* :class:`BootstrapEventBus` — Protocol every backend honours.
* :class:`InMemoryBootstrapEventBus` — test double, also the
  no-op default the pipeline falls back to when no Redis is
  wired.
* :class:`RedisBootstrapEventBus` — production backend using
  Redis Streams for history + Pub/Sub for live tail.

The Protocol intentionally hides the transport so the pipeline
can stay backend-agnostic; tests bind the in-memory variant,
production wires the Redis variant.

Honest scope
------------

* Events are best-effort.  The pipeline's correctness never
  depends on an event having landed — a Redis outage degrades
  the audit log to "no visibility", not "ingestion stops".
* Event payloads are caller-defined dicts; the schema is *not*
  enforced at runtime.  Consumers should treat any unknown
  field as forward-compatible noise.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final, Protocol

import structlog


__all__ = [
    "BOOTSTRAP_EVENT_STREAM_TTL_SECONDS",
    "BootstrapEvent",
    "BootstrapEventBus",
    "EventKind",
    "InMemoryBootstrapEventBus",
    "JobSummary",
    "NullBootstrapEventBus",
    "SkipReason",
]


# Redis streams + summary hashes are kept for 24 hours so an
# operator can audit a completed run later in the day.  Longer
# retention is intentionally avoided — once the run is done the
# canonical record lives in the ``decision_cases`` and
# ``pattern_rules`` Postgres tables.
BOOTSTRAP_EVENT_STREAM_TTL_SECONDS: Final[int] = 24 * 60 * 60


logger = structlog.get_logger(__name__)


class EventKind(StrEnum):
    """Taxonomy of every event the pipeline can emit."""

    JOB_STARTED = "job_started"
    CONVERSATION_LOADED = "conversation_loaded"
    CONVERSATION_SKIPPED = "conversation_skipped"
    CASE_EXTRACTED = "case_extracted"
    CASE_SKIPPED = "case_skipped"
    RULE_EMITTED = "rule_emitted"
    RULE_BLOCKED = "rule_blocked"
    PROFILE_BUILT = "profile_built"
    LOADER_TRUNCATED = "loader_truncated"
    JOB_DONE = "job_done"
    JOB_FAILED = "job_failed"


class SkipReason(StrEnum):
    """Enumerated reasons the pipeline drops a conversation / case / rule.

    Operators can filter the log by this field to focus on a
    specific failure mode (``GET /onboarding/jobs/{id}/log?reason=
    no_pm_response_after_guest``).
    """

    # Conversation-level
    EMPTY_THREAD = "empty_thread"
    NO_GUEST_MESSAGE = "no_guest_message"
    NO_PM_RESPONSE_AFTER_GUEST = "no_pm_response_after_guest"
    MISSING_DATES = "missing_dates"
    LOADER_ERROR = "loader_error"

    # Case-level
    NOT_LEARNABLE = "not_learnable"
    MISSING_OUTCOME = "missing_outcome"
    SCENARIO_GENERAL = "scenario_general"
    CLASSIFIER_FAILED = "classifier_failed"

    # Rule-level
    NEVER_AUTO_LEARN = "never_auto_learn"
    INSUFFICIENT_SUPPORT = "insufficient_support"
    LOW_CONFIDENCE = "low_confidence"
    TOO_MANY_COUNTEREXAMPLES = "too_many_counterexamples"
    NO_CONDITIONS = "no_conditions"

    # Catch-all
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class BootstrapEvent:
    """One row in the per-job audit log.

    Attributes:
        ts: UTC timestamp of when the pipeline emitted the event.
        job_id: Stable identifier of the bootstrap run.
        property_id: Property the event belongs to.
        kind: One of :class:`EventKind`.
        payload: Free-form JSON-serialisable dict carrying
            event-specific fields (conversation_id, scenario,
            confidence, ...).  Consumers should treat unknown
            keys as forward-compatible.
    """

    ts: datetime
    job_id: str
    property_id: str
    kind: EventKind
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("ts must be tz-aware")
        if not self.job_id:
            raise ValueError("job_id required")
        if not self.property_id:
            raise ValueError("property_id required")

    def to_dict(self) -> dict[str, Any]:
        """Render the event as a JSON-safe dict."""
        return {
            "ts": self.ts.isoformat(),
            "job_id": self.job_id,
            "property_id": self.property_id,
            "kind": self.kind.value,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class JobSummary:
    """Aggregated snapshot of a bootstrap job's state.

    Returned by :meth:`BootstrapEventBus.summary` and the
    ``GET /onboarding/jobs/{id}`` endpoint.  The numerator/
    denominator pairs let the operator render a progress bar
    without scrolling through the full event log.
    """

    job_id: str
    property_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    counts: Mapping[str, int]
    skip_breakdown: Mapping[str, int]
    rule_block_breakdown: Mapping[str, int]
    last_error: str

    def to_dict(self) -> dict[str, Any]:
        """Render the summary as a JSON-safe dict."""
        return {
            "job_id": self.job_id,
            "property_id": self.property_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat()
                if self.finished_at is not None
                else None
            ),
            "status": self.status,
            "counts": dict(self.counts),
            "skip_breakdown": dict(self.skip_breakdown),
            "rule_block_breakdown": dict(self.rule_block_breakdown),
            "last_error": self.last_error,
        }


class BootstrapEventBus(Protocol):
    """Transport-agnostic interface every backend honours."""

    async def emit(self, event: BootstrapEvent) -> None:
        """Append ``event`` to the per-job history."""
        ...

    async def history(
        self,
        job_id: str,
        *,
        since: int = 0,
        limit: int = 100,
        kinds: tuple[EventKind, ...] | None = None,
    ) -> tuple[BootstrapEvent, ...]:
        """Return up to ``limit`` events for ``job_id`` after ``since``.

        ``since`` is the index of the last event the caller
        already observed (paging cursor).
        """
        ...

    async def stream(
        self, job_id: str,
    ) -> AsyncIterator[BootstrapEvent]:
        """Yield every new event for ``job_id`` until the caller stops.

        Implementations MAY block awaiting fresh events; callers
        wrap the iterator in a timeout when they expect bounded
        consumption (e.g. SSE responses with a heartbeat).
        """
        ...

    async def summary(self, job_id: str) -> JobSummary | None:
        """Return the aggregated state of ``job_id`` (``None`` if unknown)."""
        ...


def _empty_summary(
    *,
    job_id: str,
    property_id: str,
    started_at: datetime,
) -> JobSummary:
    """Construct the initial summary before any event arrives."""
    return JobSummary(
        job_id=job_id,
        property_id=property_id,
        started_at=started_at,
        finished_at=None,
        status="queued",
        counts={},
        skip_breakdown={},
        rule_block_breakdown={},
        last_error="",
    )


def _apply_event(summary: JobSummary, event: BootstrapEvent) -> JobSummary:
    """Fold ``event`` into ``summary`` and return the new snapshot."""
    counts = dict(summary.counts)
    skip = dict(summary.skip_breakdown)
    rule_block = dict(summary.rule_block_breakdown)
    status = summary.status
    finished_at = summary.finished_at
    last_error = summary.last_error

    kind = event.kind
    if kind is EventKind.JOB_STARTED:
        status = "running"
    elif kind is EventKind.CONVERSATION_LOADED:
        counts["conversations_loaded"] = (
            counts.get("conversations_loaded", 0) + 1
        )
    elif kind is EventKind.CONVERSATION_SKIPPED:
        counts["conversations_skipped"] = (
            counts.get("conversations_skipped", 0) + 1
        )
        reason = str(event.payload.get("reason") or "other")
        skip[reason] = skip.get(reason, 0) + 1
    elif kind is EventKind.CASE_EXTRACTED:
        counts["cases_extracted"] = (
            counts.get("cases_extracted", 0) + 1
        )
    elif kind is EventKind.CASE_SKIPPED:
        counts["cases_skipped"] = (
            counts.get("cases_skipped", 0) + 1
        )
        reason = str(event.payload.get("reason") or "other")
        skip[reason] = skip.get(reason, 0) + 1
    elif kind is EventKind.RULE_EMITTED:
        counts["rules_emitted"] = (
            counts.get("rules_emitted", 0) + 1
        )
    elif kind is EventKind.RULE_BLOCKED:
        counts["rules_blocked"] = (
            counts.get("rules_blocked", 0) + 1
        )
        reason = str(event.payload.get("reason") or "other")
        rule_block[reason] = rule_block.get(reason, 0) + 1
    elif kind is EventKind.PROFILE_BUILT:
        counts["profiles_built"] = (
            counts.get("profiles_built", 0) + 1
        )
    elif kind is EventKind.LOADER_TRUNCATED:
        # Operator-visible warning: the loader stopped before
        # exhausting the source archive because the caller's
        # ``limit`` was reached.  Each emit is one property whose
        # ingestion was capped.
        counts["loader_truncations"] = (
            counts.get("loader_truncations", 0) + 1
        )
    elif kind is EventKind.JOB_DONE:
        status = "done"
        finished_at = event.ts
    elif kind is EventKind.JOB_FAILED:
        status = "failed"
        finished_at = event.ts
        last_error = str(event.payload.get("error") or "")

    return JobSummary(
        job_id=summary.job_id,
        property_id=summary.property_id,
        started_at=summary.started_at,
        finished_at=finished_at,
        status=status,
        counts=counts,
        skip_breakdown=skip,
        rule_block_breakdown=rule_block,
        last_error=last_error,
    )


class NullBootstrapEventBus:
    """No-op bus the pipeline falls back to when no transport is wired.

    Every method is a synchronous return; ``stream`` yields
    nothing.  Tests that do not care about events drop this in;
    production environments with Redis configure the
    :class:`RedisBootstrapEventBus` instead.
    """

    async def emit(self, event: BootstrapEvent) -> None:
        return None

    async def history(
        self,
        job_id: str,
        *,
        since: int = 0,
        limit: int = 100,
        kinds: tuple[EventKind, ...] | None = None,
    ) -> tuple[BootstrapEvent, ...]:
        return ()

    async def stream(
        self, job_id: str,
    ) -> AsyncIterator[BootstrapEvent]:
        if False:  # pragma: no cover — empty async generator pattern
            yield  # type: ignore[unreachable]

    async def summary(self, job_id: str) -> JobSummary | None:
        return None


class InMemoryBootstrapEventBus:
    """Per-process bus suitable for tests + single-replica deployments.

    Events for each ``job_id`` accumulate in a list; ``stream``
    uses an :class:`asyncio.Event` to wake consumers when new
    events land.  Memory is bounded by the natural duration of a
    bootstrap job — the bus does not GC entries automatically;
    callers that need long-running cleanliness should reset
    after each job.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[BootstrapEvent]] = {}
        self._summaries: dict[str, JobSummary] = {}
        self._wakeups: dict[str, asyncio.Event] = {}

    async def emit(self, event: BootstrapEvent) -> None:
        history = self._events.setdefault(event.job_id, [])
        history.append(event)
        summary = self._summaries.get(event.job_id) or _empty_summary(
            job_id=event.job_id,
            property_id=event.property_id,
            started_at=event.ts,
        )
        self._summaries[event.job_id] = _apply_event(summary, event)
        wake = self._wakeups.get(event.job_id)
        if wake is not None:
            wake.set()

    async def history(
        self,
        job_id: str,
        *,
        since: int = 0,
        limit: int = 100,
        kinds: tuple[EventKind, ...] | None = None,
    ) -> tuple[BootstrapEvent, ...]:
        events = self._events.get(job_id, [])
        sliced = events[max(0, since):]
        if kinds is not None:
            allowed = set(kinds)
            sliced = [e for e in sliced if e.kind in allowed]
        return tuple(sliced[:limit])

    async def stream(
        self, job_id: str,
    ) -> AsyncIterator[BootstrapEvent]:
        cursor = 0
        wake = self._wakeups.setdefault(job_id, asyncio.Event())
        while True:
            events = self._events.get(job_id, [])
            while cursor < len(events):
                yield events[cursor]
                cursor += 1
            summary = self._summaries.get(job_id)
            if summary is not None and summary.finished_at is not None:
                return
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

    async def summary(self, job_id: str) -> JobSummary | None:
        return self._summaries.get(job_id)


def make_event(
    *,
    job_id: str,
    property_id: str,
    kind: EventKind,
    payload: Mapping[str, Any] | None = None,
) -> BootstrapEvent:
    """Convenience factory that stamps ``ts`` with the current UTC instant."""
    return BootstrapEvent(
        ts=datetime.now(timezone.utc),
        job_id=job_id,
        property_id=property_id,
        kind=kind,
        payload=dict(payload or {}),
    )


# ── Redis backend ───────────────────────────────────────────── #


_REDIS_STREAM_PREFIX: Final[str] = "bootstrap:job:"
_SUMMARY_HASH_FIELD: Final[str] = "summary"


def _summary_key(job_id: str) -> str:
    return f"{_REDIS_STREAM_PREFIX}{job_id}"


def _stream_key(job_id: str) -> str:
    return f"{_REDIS_STREAM_PREFIX}{job_id}:events"


class RedisBootstrapEventBus:
    """Production bus over Redis Streams + summary hash.

    Layout
    ------
    * ``bootstrap:job:{job_id}:events`` — Redis Stream, one
      entry per event with the JSON payload under field
      ``payload`` and the event ``kind`` under ``kind``.
    * ``bootstrap:job:{job_id}`` — Redis Hash carrying the
      latest :class:`JobSummary` serialised JSON under field
      ``summary``.

    Both keys carry a 24-hour TTL so finished jobs eventually
    age out without manual cleanup.  Producers always set the
    TTL again on every write — Redis renews it idempotently.

    The class accepts any Redis client that exposes the async
    ``xadd`` / ``xrange`` / ``hset`` / ``hget`` / ``expire``
    methods.  Production wires :class:`redis.asyncio.Redis`;
    tests bind an in-memory fake.
    """

    def __init__(
        self,
        client: Any,
        *,
        ttl_seconds: int = BOOTSTRAP_EVENT_STREAM_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._log = logger.bind(component="redis_bootstrap_bus")

    async def emit(self, event: BootstrapEvent) -> None:
        stream_key = _stream_key(event.job_id)
        summary_key = _summary_key(event.job_id)
        try:
            await self._client.xadd(
                stream_key,
                {
                    "ts": event.ts.isoformat(),
                    "property_id": event.property_id,
                    "kind": event.kind.value,
                    "payload": json.dumps(dict(event.payload)),
                },
            )
            await self._client.expire(stream_key, self._ttl)
            current = await self._read_summary(event.job_id)
            if current is None:
                current = _empty_summary(
                    job_id=event.job_id,
                    property_id=event.property_id,
                    started_at=event.ts,
                )
            current = _apply_event(current, event)
            await self._client.hset(
                summary_key,
                _SUMMARY_HASH_FIELD,
                json.dumps(current.to_dict()),
            )
            await self._client.expire(summary_key, self._ttl)
        except Exception as exc:  # noqa: BLE001 - audit is best-effort
            self._log.warning(
                "emit.failed",
                kind=event.kind.value,
                error=str(exc),
            )

    async def history(
        self,
        job_id: str,
        *,
        since: int = 0,
        limit: int = 100,
        kinds: tuple[EventKind, ...] | None = None,
    ) -> tuple[BootstrapEvent, ...]:
        stream_key = _stream_key(job_id)
        try:
            entries = await self._client.xrange(
                stream_key, count=since + limit,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort read
            self._log.warning("history.failed", error=str(exc))
            return ()
        allowed = set(kinds) if kinds is not None else None
        out: list[BootstrapEvent] = []
        for idx, entry in enumerate(entries):
            if idx < since:
                continue
            if len(out) >= limit:
                break
            _entry_id, fields = entry
            kind_raw = (
                fields.get(b"kind")
                if isinstance(fields, dict) and b"kind" in fields
                else fields.get("kind")
            )
            if kind_raw is None:
                continue
            kind = EventKind(
                kind_raw.decode()
                if isinstance(kind_raw, bytes)
                else str(kind_raw),
            )
            if allowed is not None and kind not in allowed:
                continue
            ts_raw = fields.get(b"ts") or fields.get("ts")
            property_raw = (
                fields.get(b"property_id")
                or fields.get("property_id")
            )
            payload_raw = (
                fields.get(b"payload") or fields.get("payload")
            )
            ts = datetime.fromisoformat(
                ts_raw.decode()
                if isinstance(ts_raw, bytes)
                else str(ts_raw),
            )
            property_id = (
                property_raw.decode()
                if isinstance(property_raw, bytes)
                else str(property_raw or "")
            )
            payload_str = (
                payload_raw.decode()
                if isinstance(payload_raw, bytes)
                else str(payload_raw or "{}")
            )
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {}
            out.append(
                BootstrapEvent(
                    ts=ts,
                    job_id=job_id,
                    property_id=property_id,
                    kind=kind,
                    payload=payload,
                )
            )
        return tuple(out)

    async def stream(
        self, job_id: str,
    ) -> AsyncIterator[BootstrapEvent]:
        cursor = 0
        deadline = time.monotonic() + self._ttl
        while time.monotonic() < deadline:
            batch = await self.history(
                job_id, since=cursor, limit=128,
            )
            for event in batch:
                yield event
                cursor += 1
                if event.kind in (
                    EventKind.JOB_DONE,
                    EventKind.JOB_FAILED,
                ):
                    return
            if not batch:
                await asyncio.sleep(0.5)

    async def summary(self, job_id: str) -> JobSummary | None:
        return await self._read_summary(job_id)

    async def _read_summary(self, job_id: str) -> JobSummary | None:
        try:
            raw = await self._client.hget(
                _summary_key(job_id), _SUMMARY_HASH_FIELD,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            self._log.warning("summary.read_failed", error=str(exc))
            return None
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return JobSummary(
            job_id=str(data.get("job_id") or job_id),
            property_id=str(data.get("property_id") or ""),
            started_at=datetime.fromisoformat(
                str(data.get("started_at")),
            ),
            finished_at=(
                datetime.fromisoformat(data["finished_at"])
                if data.get("finished_at")
                else None
            ),
            status=str(data.get("status") or "unknown"),
            counts=dict(data.get("counts") or {}),
            skip_breakdown=dict(data.get("skip_breakdown") or {}),
            rule_block_breakdown=dict(
                data.get("rule_block_breakdown") or {},
            ),
            last_error=str(data.get("last_error") or ""),
        )
