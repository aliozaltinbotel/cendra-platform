"""HTTP tests for the Phase 1 per-request tenant override.

The single-property bootstrap routes (sync + async, regular + fast)
all accept optional ``customer_id`` / ``org_id`` / ``provider_type``
fields in the request body that get forwarded to the pipeline as
``*_override`` kwargs.  When omitted the env-default applies — full
backward compatibility with every existing caller.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.onboarding_endpoints import (
    _job_tasks,
    _jobs,
    configure_onboarding_deps,
    router,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    BootstrapPropertyReport,
)


class _RecordingPipeline:
    """Captures ``bootstrap_one`` / ``bootstrap_fast`` kwargs."""

    def __init__(self) -> None:
        self.bootstrap_one_calls: list[dict[str, Any]] = []
        self.bootstrap_fast_calls: list[dict[str, Any]] = []
        self.event_bus = None

    async def bootstrap_one(self, **kwargs: Any) -> BootstrapPropertyReport:
        self.bootstrap_one_calls.append(kwargs)
        return BootstrapPropertyReport(
            property_id=kwargs["property_id"],
            conversations_loaded=1,
            cases_extracted=0,
            cases_skipped=0,
            rules_emitted=0,
            loader_limit=int(kwargs.get("limit") or 0),
        )

    async def bootstrap_fast(self, **kwargs: Any) -> BootstrapPropertyReport:
        self.bootstrap_fast_calls.append(kwargs)
        return BootstrapPropertyReport(
            property_id=kwargs["property_id"],
            conversations_loaded=1,
            cases_extracted=0,
            cases_skipped=0,
            rules_emitted=0,
            loader_limit=10_000,
        )


@pytest.fixture()
def wired_app() -> tuple[FastAPI, _RecordingPipeline]:
    _jobs.clear()
    _job_tasks.clear()
    pipeline = _RecordingPipeline()
    app = FastAPI()
    app.include_router(router)
    configure_onboarding_deps({"onboarding_bootstrap_pipeline": pipeline})
    return app, pipeline


async def _wait_completed(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = datetime.now(UTC).timestamp() + 5.0
    while datetime.now(UTC).timestamp() < deadline:
        body = client.get(f"/api/v1/onboarding/bootstrap/{job_id}").json()
        if body["status"] == "completed":
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached completed")


# ── Sync route — override forwarding ─────────────────────────────


def test_sync_route_forwards_overrides_to_pipeline(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """POST body with tenant fields → pipeline kwargs."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/598808",
        json={
            "days": 30,
            "limit": 10,
            "customer_id": "ec9013b9",
            "org_id": "626ee566",
            "provider_type": "LODGIFY",
        },
    )

    assert response.status_code == 200
    assert len(pipeline.bootstrap_one_calls) == 1
    call = pipeline.bootstrap_one_calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] == "626ee566"
    assert call["provider_type_override"] == "LODGIFY"


def test_sync_route_without_overrides_passes_none(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """Missing override fields → pipeline receives ``None`` (env default)."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/323133",
        json={"days": 30, "limit": 10},
    )

    assert response.status_code == 200
    call = pipeline.bootstrap_one_calls[0]
    assert call["customer_id_override"] is None
    assert call["org_id_override"] is None
    assert call["provider_type_override"] is None


def test_sync_route_partial_override(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """One override + two defaults — typical single-field switch."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/598808",
        json={"customer_id": "ec9013b9"},
    )

    assert response.status_code == 200
    call = pipeline.bootstrap_one_calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] is None
    assert call["provider_type_override"] is None


# ── Async route — override forwarding ────────────────────────────


@pytest.mark.asyncio
async def test_async_route_forwards_overrides(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """POST ``/async`` body fields land on the background pipeline call."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/598808/async",
        json={
            "days": 30,
            "limit": 10,
            "customer_id": "ec9013b9",
            "org_id": "626ee566",
            "provider_type": "LODGIFY",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    await _wait_completed(client, job_id)

    call = pipeline.bootstrap_one_calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] == "626ee566"
    assert call["provider_type_override"] == "LODGIFY"


# ── Fast routes — same wiring ────────────────────────────────────


def test_fast_sync_route_forwards_overrides(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """Fast sync route plumbs override fields into ``bootstrap_fast``."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/598808/fast",
        json={
            "days": 30,
            "inner_concurrency": 2,
            "customer_id": "ec9013b9",
            "provider_type": "LODGIFY",
        },
    )

    assert response.status_code == 200
    call = pipeline.bootstrap_fast_calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] is None
    assert call["provider_type_override"] == "LODGIFY"


@pytest.mark.asyncio
async def test_fast_async_route_forwards_overrides(
    wired_app: tuple[FastAPI, _RecordingPipeline],
) -> None:
    """Fast async route plumbs override fields into ``bootstrap_fast``."""
    app, pipeline = wired_app
    client = TestClient(app)

    response = client.post(
        "/api/v1/onboarding/bootstrap/property/598808/fast/async",
        json={
            "days": 30,
            "customer_id": "ec9013b9",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    await _wait_completed(client, job_id)

    call = pipeline.bootstrap_fast_calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] is None
    assert call["provider_type_override"] is None
