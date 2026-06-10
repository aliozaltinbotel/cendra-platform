"""Tests for the temporal analysis HTTP endpoint (Phase 3, PR3b).

Drives the router with a :class:`TestClient` over a minimal app and
fake stores / model injected via :func:`configure_temporal_analysis_deps`:
the flag gate (404 when off), the not-wired guard (503), the no-model
degraded path, and the happy path (timeline assembled from a fake store,
analysed by a fake model, shaped into the response).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import brain_engine.api.temporal_analysis_endpoints as endpoints
from brain_engine.api.temporal_analysis_endpoints import (
    configure_temporal_analysis_deps,
    router,
)
from brain_engine.temporal_analysis import TemporalAnalysis

_ENABLED_ENV = "BRAIN_TEMPORAL_ANALYSIS_ENABLED"


def _t(day: int) -> datetime:
    return datetime(2026, 5, day, 12, 0, tzinfo=UTC)


class _FakeGuestHistory:
    """One confirmed booking for the guest; no incidents."""

    async def get_guest_bookings(self, guest_id: str) -> list[Any]:
        return [
            SimpleNamespace(
                booking_id="b1",
                guest_id=guest_id,
                property_id="p1",
                property_name="Villa",
                check_in=_t(14).isoformat(),
                check_out=_t(17).isoformat(),
                status="confirmed",
                num_guests=2,
                total_price=300.0,
                currency="EUR",
                booking_source="airbnb",
                payment_status="paid",
                created_at=_t(1).isoformat(),
            ),
        ]

    async def get_guest_incidents(self, guest_id: str) -> list[Any]:
        return []

    async def get_property_incidents(self, property_id: str) -> list[Any]:
        return []


class _FakeModel:
    """Returns a fixed structured analysis; records the call."""

    def __init__(self, result: TemporalAnalysis) -> None:
        self._result = result
        self.seen_messages: Any = None

    async def invoke_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[Any],
    ) -> TemporalAnalysis:
        self.seen_messages = messages
        return self._result


@pytest.fixture(autouse=True)
def _reset_deps() -> Any:
    """Isolate the module-global deps between tests."""
    endpoints._deps.clear()
    yield
    endpoints._deps.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _body(**over: Any) -> dict[str, Any]:
    base = {"question": "How is this guest?", "guest_id": "g1"}
    base.update(over)
    return base


def test_disabled_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENABLED_ENV, raising=False)
    configure_temporal_analysis_deps({"guest_history": _FakeGuestHistory()})

    resp = _client().post("/api/v1/temporal/analyze", json=_body())

    assert resp.status_code == 404
    assert resp.json()["error"] == "temporal_analysis_disabled"


def test_enabled_but_not_wired_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")

    resp = _client().post("/api/v1/temporal/analyze", json=_body())

    assert resp.status_code == 503
    assert resp.json()["error"] == "temporal_analysis_not_wired"


def test_no_model_degrades_to_200_without_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    configure_temporal_analysis_deps({"guest_history": _FakeGuestHistory()})

    resp = _client().post("/api/v1/temporal/analyze", json=_body())

    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_used"] is False
    assert data["analysis"] is None
    assert data["note"] == "no chat model configured"
    # The one booking was assembled into the timeline / history.
    assert data["context_entry_count"] == 1
    assert data["scope"] == {"guest_id": "g1"}


def test_happy_path_returns_structured_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    answer = TemporalAnalysis(
        answer="The guest has one upcoming stay.",
        key_findings=["Confirmed booking at the Villa"],
        confidence=0.7,
    )
    model = _FakeModel(answer)
    configure_temporal_analysis_deps(
        {"guest_history": _FakeGuestHistory(), "chat_model": model},
    )

    resp = _client().post(
        "/api/v1/temporal/analyze",
        json=_body(property_id="p1", as_of=_t(15).isoformat()),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_used"] is True
    assert data["analysis"]["answer"] == "The guest has one upcoming stay."
    assert data["analysis"]["confidence"] == 0.7
    assert data["context_entry_count"] == 1
    assert data["scope"] == {"property_id": "p1", "guest_id": "g1"}
    # The model actually saw the rendered context.
    assert "CLIENT TEMPORAL CONTEXT" in model.seen_messages[1]["content"]


def test_validation_rejects_empty_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    configure_temporal_analysis_deps({"guest_history": _FakeGuestHistory()})

    resp = _client().post(
        "/api/v1/temporal/analyze",
        json=_body(question=""),
    )

    assert resp.status_code == 422
