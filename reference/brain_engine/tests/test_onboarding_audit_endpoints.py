"""HTTP tests for the bootstrap audit-log endpoints.

Three endpoints pinned:

* ``GET  /api/v1/onboarding/jobs/{job_id}`` — aggregated summary.
* ``GET  /api/v1/onboarding/jobs/{job_id}/log`` — paginated events
  with ``since`` / ``limit`` / ``kind=`` filters.
* ``GET  /api/v1/onboarding/jobs/{job_id}/stream`` — SSE tail.

The tests bind a minimal FastAPI app with the onboarding router and
inject a pipeline-shaped stub whose :attr:`event_bus` is a populated
:class:`InMemoryBootstrapEventBus`.  Each test pre-seeds the bus
with the events the underlying pipeline would have emitted on a
real run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.onboarding_endpoints import (
    configure_onboarding_deps,
    router,
)
from brain_engine.onboarding.event_bus import (
    BootstrapEvent,
    EventKind,
    InMemoryBootstrapEventBus,
    SkipReason,
)


def _event(
    *,
    job_id: str = "job-1",
    property_id: str = "323133",
    kind: EventKind,
    payload: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> BootstrapEvent:
    return BootstrapEvent(
        ts=ts or datetime.now(timezone.utc),
        job_id=job_id,
        property_id=property_id,
        kind=kind,
        payload=payload or {},
    )


class _PipelineStub:
    """Minimum surface the audit endpoints need from the pipeline."""

    def __init__(self, bus: InMemoryBootstrapEventBus) -> None:
        self.event_bus = bus


@pytest.fixture()
def seeded_app() -> tuple[FastAPI, InMemoryBootstrapEventBus]:
    bus = InMemoryBootstrapEventBus()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {"onboarding_bootstrap_pipeline": _PipelineStub(bus)},
    )
    return app, bus


@pytest.mark.asyncio
async def test_summary_endpoint_returns_breakdowns(
    seeded_app: tuple[FastAPI, InMemoryBootstrapEventBus],
) -> None:
    """Summary surfaces the per-reason counts the operator filters on."""
    app, bus = seeded_app
    await bus.emit(_event(kind=EventKind.JOB_STARTED))
    await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(
        _event(
            kind=EventKind.CONVERSATION_SKIPPED,
            payload={"reason": SkipReason.EMPTY_THREAD.value},
        ),
    )
    await bus.emit(_event(kind=EventKind.JOB_DONE))

    client = TestClient(app)
    response = client.get("/api/v1/onboarding/jobs/job-1")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["counts"]["conversations_skipped"] == 1
    assert body["skip_breakdown"] == {
        SkipReason.EMPTY_THREAD.value: 1,
    }


def test_summary_endpoint_returns_404_for_unknown_job(
    seeded_app: tuple[FastAPI, InMemoryBootstrapEventBus],
) -> None:
    app, _ = seeded_app
    client = TestClient(app)
    response = client.get("/api/v1/onboarding/jobs/missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_log_endpoint_paginates_and_filters_by_kind(
    seeded_app: tuple[FastAPI, InMemoryBootstrapEventBus],
) -> None:
    app, bus = seeded_app
    for _ in range(5):
        await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(
        _event(
            kind=EventKind.CASE_SKIPPED,
            payload={"reason": SkipReason.MISSING_OUTCOME.value},
        ),
    )

    client = TestClient(app)

    page_one = client.get(
        "/api/v1/onboarding/jobs/job-1/log",
        params={"since": 0, "limit": 3},
    ).json()
    assert page_one["returned"] == 3
    assert all(
        e["kind"] == EventKind.CONVERSATION_LOADED.value
        for e in page_one["events"]
    )

    tail = client.get(
        "/api/v1/onboarding/jobs/job-1/log",
        params={"since": 5, "limit": 10},
    ).json()
    assert tail["returned"] == 1
    assert tail["events"][0]["kind"] == EventKind.CASE_SKIPPED.value
    assert tail["events"][0]["payload"]["reason"] == (
        SkipReason.MISSING_OUTCOME.value
    )

    filtered = client.get(
        "/api/v1/onboarding/jobs/job-1/log",
        params={"kind": [EventKind.CASE_SKIPPED.value]},
    ).json()
    assert filtered["returned"] == 1


def test_log_endpoint_rejects_unknown_kind(
    seeded_app: tuple[FastAPI, InMemoryBootstrapEventBus],
) -> None:
    app, _ = seeded_app
    client = TestClient(app)
    response = client.get(
        "/api/v1/onboarding/jobs/job-1/log",
        params={"kind": ["definitely_not_a_kind"]},
    )
    assert response.status_code == 400
    assert "definitely_not_a_kind" in response.text


@pytest.mark.asyncio
async def test_stream_endpoint_emits_sse_frames(
    seeded_app: tuple[FastAPI, InMemoryBootstrapEventBus],
) -> None:
    app, bus = seeded_app
    await bus.emit(_event(kind=EventKind.JOB_STARTED))
    await bus.emit(_event(kind=EventKind.CONVERSATION_LOADED))
    await bus.emit(_event(kind=EventKind.JOB_DONE))

    client = TestClient(app)
    with client.stream(
        "GET", "/api/v1/onboarding/jobs/job-1/stream",
    ) as response:
        assert response.status_code == 200
        assert (
            response.headers["content-type"].startswith(
                "text/event-stream",
            )
        )
        body = response.read().decode()

    frames = [f for f in body.split("\n\n") if f.strip()]
    assert len(frames) == 3
    first = frames[0]
    assert first.startswith(f"event: {EventKind.JOB_STARTED.value}")
    data_line = next(
        line for line in first.splitlines() if line.startswith("data: ")
    )
    parsed = json.loads(data_line[len("data: "):])
    assert parsed["kind"] == EventKind.JOB_STARTED.value
    assert parsed["job_id"] == "job-1"
