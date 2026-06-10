"""Tests for the PM-chat temporal pipeline hook (Phase 3, PR3c.1).

Drives :func:`maybe_emit_temporal_analysis` against a fake emitter +
fake analyzer / timeline: the flag gate (off ⇒ silent), the unwired and
no-scope guards, the happy path (emits one ``TEMPORAL_ANALYSIS`` event
with the shaped payload), the degraded path (no analysis ⇒ no event), and
non-fatal isolation (an analyzer that raises never propagates).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import brain_engine.conversation.temporal_pm_hook as hook
from brain_engine.conversation.temporal_pm_hook import (
    configure_temporal_pm_deps,
    maybe_emit_temporal_analysis,
)
from brain_engine.memory.memory_timeline import TimelineScope
from brain_engine.streaming.current_emitter import (
    reset_current_emitter,
    set_current_emitter,
)
from brain_engine.streaming.event_types import EventType
from brain_engine.temporal_analysis import (
    TemporalAnalysis,
    TemporalAnalysisResult,
)

_ENABLED_ENV = "BRAIN_TEMPORAL_PM_ENABLED"


def _t() -> datetime:
    return datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


def _result(analysis: TemporalAnalysis | None) -> TemporalAnalysisResult:
    return TemporalAnalysisResult(
        question="q",
        as_of=_t(),
        scope=TimelineScope(property_id="p1"),
        analysis=analysis,
        llm_used=analysis is not None,
        context_entry_count=4,
        note="" if analysis is not None else "no chat model configured",
    )


class _FakeEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[Any, dict[str, Any]]] = []

    def emit(self, event_type: Any, data: dict[str, Any]) -> None:
        self.events.append((event_type, data))


class _FakeTimeline:
    async def read(
        self,
        scope: TimelineScope,
        *,
        as_of: datetime | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        return []


class _FakeAnalyzer:
    def __init__(self, result: TemporalAnalysisResult) -> None:
        self._result = result

    async def analyze(
        self,
        context: Any,
        question: str,
    ) -> TemporalAnalysisResult:
        return self._result


class _BoomAnalyzer:
    async def analyze(self, context: Any, question: str) -> Any:
        raise RuntimeError("analyzer down")


@pytest.fixture(autouse=True)
def _clear_deps() -> Any:
    hook._deps.clear()
    yield
    hook._deps.clear()


@pytest.fixture
def emitter() -> Any:
    em = _FakeEmitter()
    token = set_current_emitter(em)  # type: ignore[arg-type]
    yield em
    reset_current_emitter(token)


def _wire(analysis: TemporalAnalysis | None) -> None:
    configure_temporal_pm_deps(
        analyzer=_FakeAnalyzer(_result(analysis)),  # type: ignore[arg-type]
        timeline=_FakeTimeline(),  # type: ignore[arg-type]
    )


async def test_disabled_emits_nothing(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENABLED_ENV, raising=False)
    _wire(TemporalAnalysis(answer="A", key_findings=[], confidence=0.5))

    await maybe_emit_temporal_analysis(property_id="p1")

    assert emitter.events == []


async def test_unwired_emits_nothing(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")  # no configure_temporal_pm_deps
    await maybe_emit_temporal_analysis(property_id="p1")
    assert emitter.events == []


async def test_no_scope_emits_nothing(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    _wire(TemporalAnalysis(answer="A", key_findings=[], confidence=0.5))

    await maybe_emit_temporal_analysis(property_id="", customer_id="")

    assert emitter.events == []


async def test_happy_path_emits_shaped_event(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    _wire(
        TemporalAnalysis(
            answer="Recent quiet stay.",
            key_findings=["No incidents"],
            confidence=0.6,
        ),
    )

    await maybe_emit_temporal_analysis(property_id="p1", customer_id="c1")

    assert len(emitter.events) == 1
    event_type, data = emitter.events[0]
    assert event_type is EventType.TEMPORAL_ANALYSIS
    assert data["text"] == "Recent quiet stay.\n\n- No incidents"
    assert data["answer"] == "Recent quiet stay."
    assert data["key_findings"] == ["No incidents"]
    assert data["confidence"] == 0.6
    assert data["context_entry_count"] == 4
    assert data["as_of"] == _t().isoformat()
    assert data["scope"] == {"property_id": "p1", "customer_id": "c1"}


async def test_degraded_analysis_emits_nothing(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    _wire(None)  # analyzer returns a result with analysis=None

    await maybe_emit_temporal_analysis(property_id="p1")

    assert emitter.events == []


async def test_analyzer_failure_is_non_fatal(
    emitter: _FakeEmitter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    configure_temporal_pm_deps(
        analyzer=_BoomAnalyzer(),  # type: ignore[arg-type]
        timeline=_FakeTimeline(),  # type: ignore[arg-type]
    )

    # Must not raise.
    await maybe_emit_temporal_analysis(property_id="p1")

    assert emitter.events == []
