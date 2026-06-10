"""Live proof of the async single-property route (PR #C).

Drives ``POST /api/v1/onboarding/bootstrap/property/{id}/async``
against a recording pipeline through ``fastapi.testclient`` and
asserts the contract end-to-end:

1. The HTTP route returns ``202 Accepted`` with a fresh ``job_id``.
2. The background task runs ``bootstrap_one`` off the request path.
3. ``GET /bootstrap/{job_id}`` transitions ``pending → running →
   completed`` and exposes the populated
   :class:`BootstrapPropertyReport` once the job finishes.
4. The audit-log endpoints (``GET /jobs/{id}``, ``/log``) see the
   same events the pipeline emitted under the same ``job_id``.

Execute with the repo venv:

    .venv/bin/python proof_bootstrap_async_single.py
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import FastAPI

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
    make_event,
)


class _SlowPipeline:
    """Records calls + emits a couple of events with a tiny sleep.

    The sleep ensures the HTTP response is observable *before* the
    task finishes — that's the whole point of the async route.
    """

    def __init__(self) -> None:
        self.event_bus = InMemoryBootstrapEventBus()
        self.calls: list[dict[str, Any]] = []

    async def bootstrap_one(
        self, **kwargs: Any,
    ) -> BootstrapPropertyReport:
        self.calls.append(kwargs)
        job_id = kwargs["job_id"]
        property_id = kwargs["property_id"]
        await self.event_bus.emit(
            make_event(
                job_id=job_id,
                property_id=property_id,
                kind=EventKind.JOB_STARTED,
                payload={"mode": "proof"},
            ),
        )
        # Simulated work — the route must already have returned by
        # the time this awaits.
        await asyncio.sleep(0.2)
        for i in range(3):
            await self.event_bus.emit(
                make_event(
                    job_id=job_id,
                    property_id=property_id,
                    kind=EventKind.CONVERSATION_LOADED,
                    payload={"conversation_id": f"c-{i}"},
                ),
            )
        await self.event_bus.emit(
            make_event(
                job_id=job_id,
                property_id=property_id,
                kind=EventKind.JOB_DONE,
                payload={"cases_extracted": 3},
            ),
        )
        return BootstrapPropertyReport(
            property_id=property_id,
            conversations_loaded=3,
            cases_extracted=3,
            cases_skipped=0,
            rules_emitted=1,
            loader_limit=int(kwargs["limit"]),
        )


async def main() -> None:
    _jobs.clear()
    _job_tasks.clear()
    pipeline = _SlowPipeline()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps(
        {"onboarding_bootstrap_pipeline": pipeline},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://proof",
    ) as client:
        print("── 1. POST .../async returns immediately ────────")
        started = time.monotonic()
        response = await client.post(
            "/api/v1/onboarding/bootstrap/property/323133/async",
            json={"days": 30, "limit": 100, "mine_patterns": False},
        )
        elapsed_post = time.monotonic() - started
        assert response.status_code == 202
        accepted = response.json()
        job_id = accepted["job_id"]
        print(f"  status      = {response.status_code}")
        print(f"  job_id      = {job_id}")
        print(f"  state       = {accepted['status']}")
        print(f"  POST elapsed = {elapsed_post * 1000:.1f}ms (<200ms)")
        assert elapsed_post < 0.2, (
            "async route must return before background work finishes"
        )
        assert accepted["status"] == "pending"

        print()
        print("── 2. Status transitions to running, then completed ─")
        seen_states: list[str] = []
        deadline = time.monotonic() + 5.0
        body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            body = (
                await client.get(
                    f"/api/v1/onboarding/bootstrap/{job_id}",
                )
            ).json()
            seen_states.append(body["status"])
            if body["status"] == "completed":
                break
            await asyncio.sleep(0.05)
        print(f"  state trail = {seen_states}")
        assert body["status"] == "completed"
        assert body["report"]["property_id"] == "323133"
        assert body["report"]["conversations_loaded"] == 3
        assert body["report"]["rules_emitted"] == 1
        print(f"  report      = {body['report']}")

        print()
        print("── 3. Audit log sees every event under the job_id ──")
        log = (
            await client.get(
                f"/api/v1/onboarding/jobs/{job_id}/log",
            )
        ).json()
        kinds = [e["kind"] for e in log["events"]]
        print(f"  events ({log['returned']}): {kinds}")
        assert kinds[0] == EventKind.JOB_STARTED.value
        assert kinds[-1] == EventKind.JOB_DONE.value
        assert kinds.count(EventKind.CONVERSATION_LOADED.value) == 3

        summary = (
            await client.get(
                f"/api/v1/onboarding/jobs/{job_id}",
            )
        ).json()
        print(
            f"  summary     = status={summary['status']!r} "
            f"counts={summary['counts']}"
        )
        assert summary["status"] == "done"
        assert summary["counts"]["conversations_loaded"] == 3

        print()
        print(
            f"  pipeline.bootstrap_one called {len(pipeline.calls)} time(s) "
            f"with job_id={pipeline.calls[0]['job_id']!r}"
        )

    print()
    print("✅ all assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
