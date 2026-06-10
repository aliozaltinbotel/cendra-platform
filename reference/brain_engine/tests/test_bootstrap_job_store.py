"""Tests for the cross-replica :class:`BootstrapJobStore` (PR #D).

Mümin 2026-05-12: the legacy in-process ``_jobs`` dict broke the
moment the dev deployment scaled to two replicas — a job created
on pod A returned ``404`` when the GET landed on pod B.  These
tests pin the contract every backend must honour:

* :class:`InMemoryBootstrapJobStore` round-trips a state snapshot
  by job_id.
* :class:`RedisBootstrapJobStore` persists JSON under
  ``bootstrap:job_state:{job_id}`` with the standard 24-hour TTL.
* :class:`NullBootstrapJobStore` returns ``None`` on every read.
* The HTTP layer falls back to the store when the local cache
  misses (simulating a second-pod GET after a first-pod POST).
* Read failures (transport outage) degrade gracefully to
  ``None`` rather than raising.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.onboarding_endpoints import (
    _jobs,
    _job_tasks,
    configure_onboarding_deps,
    router,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    BootstrapJobState,
    BootstrapPropertyReport,
)
from brain_engine.onboarding.event_bus import InMemoryBootstrapEventBus
from brain_engine.onboarding.job_store import (
    BOOTSTRAP_JOB_STATE_TTL_SECONDS,
    InMemoryBootstrapJobStore,
    NullBootstrapJobStore,
    RedisBootstrapJobStore,
)


# ── Constants ────────────────────────────────────────────────────


def test_ttl_matches_audit_log_window() -> None:
    """24h aligns with the audit-log stream TTL."""
    assert BOOTSTRAP_JOB_STATE_TTL_SECONDS == 24 * 60 * 60


# ── InMemoryBootstrapJobStore ────────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_store_round_trips_state() -> None:
    store = InMemoryBootstrapJobStore()
    snapshot = {
        "job_id": "job-1",
        "status": "running",
        "submitted_at": "2026-05-12T17:00:00+00:00",
    }
    await store.put("job-1", snapshot)
    out = await store.get("job-1")
    assert out == snapshot


@pytest.mark.asyncio
async def test_in_memory_store_returns_none_for_missing_job() -> None:
    store = InMemoryBootstrapJobStore()
    assert await store.get("missing") is None


@pytest.mark.asyncio
async def test_in_memory_store_delete_removes_entry() -> None:
    store = InMemoryBootstrapJobStore()
    await store.put("job-1", {"job_id": "job-1"})
    await store.delete("job-1")
    assert await store.get("job-1") is None


@pytest.mark.asyncio
async def test_in_memory_store_isolates_copies() -> None:
    """Mutating a returned dict must not leak back into the store."""
    store = InMemoryBootstrapJobStore()
    await store.put("job-1", {"status": "running"})
    snapshot = await store.get("job-1")
    assert snapshot is not None
    snapshot["status"] = "mutated"
    again = await store.get("job-1")
    assert again is not None
    assert again["status"] == "running"


# ── NullBootstrapJobStore ────────────────────────────────────────


@pytest.mark.asyncio
async def test_null_store_is_total_noop() -> None:
    store = NullBootstrapJobStore()
    await store.put("job", {"status": "running"})
    assert await store.get("job") is None
    await store.delete("job")
    assert await store.get("job") is None


# ── RedisBootstrapJobStore over fakeredis ────────────────────────


@pytest.mark.asyncio
async def test_redis_store_round_trips_over_fakeredis() -> None:
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    client = fakeredis.FakeRedis()
    store = RedisBootstrapJobStore(client)
    snapshot = {
        "job_id": "job-x",
        "status": "completed",
        "report": {"property_id": "323133", "cases_extracted": 7},
    }
    await store.put("job-x", snapshot)
    out = await store.get("job-x")
    assert out == snapshot


@pytest.mark.asyncio
async def test_redis_store_returns_none_for_missing_job() -> None:
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    client = fakeredis.FakeRedis()
    store = RedisBootstrapJobStore(client)
    assert await store.get("missing") is None


@pytest.mark.asyncio
async def test_redis_store_swallows_write_failures() -> None:
    """A broken Redis client never raises through :meth:`put`."""

    class _BrokenClient:
        async def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("redis is down")

        async def get(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("redis is down")

        async def delete(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("redis is down")

    store = RedisBootstrapJobStore(_BrokenClient())
    await store.put("job-broken", {"status": "running"})  # no raise
    assert await store.get("job-broken") is None
    await store.delete("job-broken")  # no raise


@pytest.mark.asyncio
async def test_redis_store_coerces_non_json_safe_payload() -> None:
    """``default=str`` keeps best-effort serialisation alive.

    A payload with non-JSON-safe values (e.g. a ``set``) is coerced
    via :func:`json.dumps(default=str)` so the registry retains a
    debuggable best-effort snapshot rather than silently losing the
    write.  The exact string repr is implementation-defined but
    the entry must round-trip through the store.
    """
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    client = fakeredis.FakeRedis()
    store = RedisBootstrapJobStore(client)
    await store.put("job-coerced", {"weird": {1, 2, 3}})
    out = await store.get("job-coerced")
    assert out is not None
    assert "weird" in out
    # ``default=str`` rendered the set as its Python repr.
    assert "{" in str(out["weird"])


# ── HTTP cross-replica path ──────────────────────────────────────


class _PipelineStub:
    def __init__(self) -> None:
        self.event_bus = InMemoryBootstrapEventBus()


@pytest.mark.asyncio
async def test_status_endpoint_falls_back_to_store_after_pod_swap() -> None:
    """A job missing from the local cache is still served via the store.

    Simulates the multi-pod scenario where the
    ``POST .../async`` landed on pod A but the
    ``GET /bootstrap/{job_id}`` lands on pod B.  Without the
    store this returned ``404`` and broke Mümin's polling loop.
    """
    _jobs.clear()
    _job_tasks.clear()
    store = InMemoryBootstrapJobStore()
    pipeline = _PipelineStub()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {
            "onboarding_bootstrap_pipeline": pipeline,
            "onboarding_job_store": store,
        },
    )

    state = BootstrapJobState(
        job_id="cross-pod-job",
        status="completed",
        submitted_at=datetime(2026, 5, 12, 12, tzinfo=timezone.utc),
        started_at=datetime(2026, 5, 12, 12, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 12, 12, 5, tzinfo=timezone.utc),
        property_report=BootstrapPropertyReport(
            property_id="323133",
            conversations_loaded=7,
            cases_extracted=5,
        ),
    )
    # Pod B never put this into ``_jobs`` — the request will have
    # to read the snapshot from the Redis-equivalent store.
    await store.put("cross-pod-job", state.as_dict())
    assert "cross-pod-job" not in _jobs

    client = TestClient(app)
    response = client.get(
        "/api/v1/onboarding/bootstrap/cross-pod-job",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["report"]["property_id"] == "323133"
    assert body["report"]["conversations_loaded"] == 7


@pytest.mark.asyncio
async def test_status_endpoint_404_when_store_misses_too() -> None:
    """Genuine unknown job_id still returns ``404`` from both surfaces."""
    _jobs.clear()
    _job_tasks.clear()
    store = InMemoryBootstrapJobStore()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {
            "onboarding_bootstrap_pipeline": _PipelineStub(),
            "onboarding_job_store": store,
        },
    )
    client = TestClient(app)
    response = client.get("/api/v1/onboarding/bootstrap/never-existed")
    assert response.status_code == 404
