"""HTTP tests for the async single-property bootstrap routes (PR #C).

The synchronous single-property routes (``POST .../property/{id}``
and ``.../property/{id}/fast``) block the request for the full
pipeline duration.  With PR #B raising the per-property cap to
100 000 conversations a deep cold-start easily exceeds the
30-second ingress timeout.  PR #C adds the matching async
endpoints:

* ``POST /api/v1/onboarding/bootstrap/property/{id}/async``
* ``POST /api/v1/onboarding/bootstrap/property/{id}/fast/async``

These tests pin:

* The async routes return ``202 Accepted`` plus a job envelope
  immediately and the background task drives the pipeline behind
  the scenes.
* The status endpoint surfaces ``status=completed`` and the
  populated :class:`BootstrapPropertyReport` once the job
  finishes.
* The audit bus sees every event the pipeline emitted under the
  same job id (so ``GET /jobs/{id}/log`` continues to work).
* Path validation: empty ``property_id`` → ``400``.
* Failure path: pipeline raising propagates into
  ``status=failed`` with ``error`` populated.
"""

from __future__ import annotations

import asyncio
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
    BootstrapPropertyReport,
)
from brain_engine.onboarding.event_bus import (
    EventKind,
    InMemoryBootstrapEventBus,
    SkipReason,
)


class _RecordingPipeline:
    """Captures ``bootstrap_one`` / ``bootstrap_fast`` invocations.

    Returns a deterministic :class:`BootstrapPropertyReport` after
    emitting a couple of events into the in-memory bus so the
    audit-log endpoints continue to work end-to-end.
    """

    def __init__(self, *, raise_on_call: bool = False) -> None:
        self.event_bus = InMemoryBootstrapEventBus()
        self.bootstrap_one_calls: list[dict[str, Any]] = []
        self.bootstrap_fast_calls: list[dict[str, Any]] = []
        self._raise = raise_on_call

    async def bootstrap_one(
        self, **kwargs: Any,
    ) -> BootstrapPropertyReport:
        self.bootstrap_one_calls.append(kwargs)
        if self._raise:
            raise RuntimeError("pipeline blew up")
        await self._emit_sample(kwargs["job_id"], kwargs["property_id"])
        return BootstrapPropertyReport(
            property_id=kwargs["property_id"],
            conversations_loaded=7,
            cases_extracted=5,
            cases_skipped=2,
            rules_emitted=1,
            loader_limit=int(kwargs["limit"]),
            loader_truncated=False,
        )

    async def bootstrap_fast(
        self, **kwargs: Any,
    ) -> BootstrapPropertyReport:
        self.bootstrap_fast_calls.append(kwargs)
        if self._raise:
            raise RuntimeError("pipeline blew up")
        await self._emit_sample(kwargs["job_id"], kwargs["property_id"])
        return BootstrapPropertyReport(
            property_id=kwargs["property_id"],
            conversations_loaded=3,
            cases_extracted=2,
            cases_skipped=1,
            rules_emitted=0,
            loader_limit=10_000,
        )

    async def _emit_sample(self, job_id: str, property_id: str) -> None:
        from brain_engine.onboarding.event_bus import make_event

        await self.event_bus.emit(
            make_event(
                job_id=job_id,
                property_id=property_id,
                kind=EventKind.JOB_STARTED,
                payload={"mode": "stub"},
            ),
        )
        await self.event_bus.emit(
            make_event(
                job_id=job_id,
                property_id=property_id,
                kind=EventKind.CONVERSATION_LOADED,
                payload={"conversation_id": "c-1"},
            ),
        )
        await self.event_bus.emit(
            make_event(
                job_id=job_id,
                property_id=property_id,
                kind=EventKind.JOB_DONE,
                payload={"cases_extracted": 2},
            ),
        )


@pytest.fixture()
def wired_app() -> tuple[FastAPI, _RecordingPipeline]:
    _jobs.clear()
    _job_tasks.clear()
    pipeline = _RecordingPipeline()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {"onboarding_bootstrap_pipeline": pipeline},
    )
    return app, pipeline


async def _wait_for_status(
    client: TestClient,
    job_id: str,
    *,
    target: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll the status endpoint until ``target`` is observed."""
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        response = client.get(f"/api/v1/onboarding/bootstrap/{job_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] == target:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"job {job_id} never reached status={target!r}; "
        f"last poll = {body}",
    )


@pytest.mark.asyncio
async def test_async_route_returns_job_id_immediately_and_completes(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """``POST .../async`` returns 202 + the background task drives the pipeline."""
    app, pipeline = wired_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 365, "limit": 100, "mine_patterns": False},
    )
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "pending"
    job_id = accepted["job_id"]

    final = await _wait_for_status(
        client, job_id, target="completed",
    )
    assert final["status"] == "completed"
    assert final["report"]["property_id"] == "323133"
    assert final["report"]["conversations_loaded"] == 7
    assert final["report"]["rules_emitted"] == 1
    assert final["report"]["loader_truncated"] is False

    assert len(pipeline.bootstrap_one_calls) == 1
    call = pipeline.bootstrap_one_calls[0]
    assert call["property_id"] == "323133"
    assert call["days"] == 365
    assert call["limit"] == 100
    assert call["job_id"] == job_id


@pytest.mark.asyncio
async def test_async_route_propagates_events_to_audit_bus(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """Audit-log endpoints see the same events under the new job_id."""
    app, _ = wired_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 30, "limit": 10},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    await _wait_for_status(client, job_id, target="completed")

    log = client.get(
        f"/api/v1/onboarding/jobs/{job_id}/log",
    ).json()
    assert log["returned"] == 3
    assert [e["kind"] for e in log["events"]] == [
        EventKind.JOB_STARTED.value,
        EventKind.CONVERSATION_LOADED.value,
        EventKind.JOB_DONE.value,
    ]

    summary = client.get(
        f"/api/v1/onboarding/jobs/{job_id}",
    ).json()
    assert summary["status"] == "done"
    assert summary["counts"]["conversations_loaded"] == 1


@pytest.mark.asyncio
async def test_async_fast_route_uses_bootstrap_fast(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """``.../fast/async`` invokes ``bootstrap_fast`` and reports completion."""
    app, pipeline = wired_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/fast/async",
        json={"days": 30, "inner_concurrency": 4},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = await _wait_for_status(
        client, job_id, target="completed",
    )
    assert final["report"]["property_id"] == "323133"
    assert final["report"]["conversations_loaded"] == 3
    assert pipeline.bootstrap_fast_calls
    call = pipeline.bootstrap_fast_calls[0]
    assert call["property_id"] == "323133"
    assert call["days"] == 30
    assert call["inner_concurrency"] == 4
    assert call["job_id"] == job_id


def test_async_route_rejects_blank_property_id(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """Blank / whitespace-only property id → 400."""
    app, _ = wired_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/onboarding/bootstrap/property/%20/async",
        json={"days": 30, "limit": 10},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_async_route_records_pipeline_failure_in_state() -> None:
    """A raising pipeline collapses to ``status=failed`` with ``error`` populated."""
    _jobs.clear()
    _job_tasks.clear()
    pipeline = _RecordingPipeline(raise_on_call=True)
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {"onboarding_bootstrap_pipeline": pipeline},
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 30, "limit": 10},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = await _wait_for_status(client, job_id, target="failed")
    assert final["error"] == "pipeline blew up"
    assert final["report"] is None


def test_async_route_payload_caps_match_pr_b(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """The async route honours the PR #B ceilings (3650 days, 100k limit)."""
    app, _ = wired_app
    client = TestClient(app)
    # Within caps → 202.
    ok = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 3650, "limit": 100_000},
    )
    assert ok.status_code == 202
    # Above caps → 422 from pydantic.
    bad = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 3651, "limit": 100_000},
    )
    assert bad.status_code == 422
    bad_limit = client.post(
        "/api/v1/onboarding/bootstrap/property/323133/async",
        json={"days": 30, "limit": 100_001},
    )
    assert bad_limit.status_code == 422


_ = SkipReason  # keep import alive for skip-reason payload assertions in
                # future tests that exercise CASE_SKIPPED routing.
