"""Tests for the temporal analysis core (Phase 3).

Two layers:

* :func:`format_context` — the deterministic, LLM-free context renderer
  (header + scope, the LIVE / UPCOMING / HISTORY sections, empty sections,
  per-entry line shape, confidence);
* :class:`TemporalAnalyzer` over a fake chat model — the happy path
  (structured answer + provenance + the prompt the model saw), and every
  degradation branch (no model, model raises, wrong output type) returning
  a no-analysis result instead of raising.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from brain_engine.memory.memory_timeline import TimelineEntry, TimelineScope
from brain_engine.memory.temporal_context import TemporalContext
from brain_engine.temporal_analysis import (
    TemporalAnalysis,
    TemporalAnalyzer,
    format_context,
)


def _t(day: int) -> datetime:
    return datetime(2026, 5, day, 12, 0, tzinfo=UTC)


_SCOPE = TimelineScope(property_id="p1", guest_id="g1", customer_id="c1")


def _entry(
    *,
    at: datetime,
    tier: str,
    kind: str,
    content: str,
    confidence: float | None = None,
) -> TimelineEntry:
    return TimelineEntry(
        at=at,
        tier=tier,
        kind=kind,
        entity_id="g1",
        content=content,
        source="test",
        confidence=confidence,
    )


def _ctx(
    *,
    history: list[TimelineEntry] | None = None,
    live: list[TimelineEntry] | None = None,
    upcoming: list[TimelineEntry] | None = None,
    as_of: datetime | None = None,
) -> TemporalContext:
    return TemporalContext(
        scope=_SCOPE,
        as_of=as_of or _t(15),
        history=history or [],
        live=live or [],
        upcoming=upcoming or [],
    )


# ── format_context ──────────────────────────────────────────────────


def test_header_and_scope() -> None:
    text = format_context(_ctx())
    assert "CLIENT TEMPORAL CONTEXT (as of 2026-05-15T12:00:00+00:00)" in text
    assert "Scope: property=p1 guest=g1 customer=c1" in text


def test_empty_sections_say_none() -> None:
    text = format_context(_ctx())
    assert "LIVE NOW: none." in text
    assert "UPCOMING: none." in text
    assert "HISTORY (oldest first): none." in text


def test_sections_render_entries_with_counts() -> None:
    fact = _entry(
        at=_t(1),
        tier="kg",
        kind="fact",
        content="prefers late checkout",
        confidence=0.9,
    )
    booking = _entry(
        at=_t(2),
        tier="operations",
        kind="booking",
        content="Booking confirmed: Villa 2026-05-14→2026-05-17",
    )
    text = format_context(_ctx(history=[fact], live=[booking]))

    assert "LIVE NOW (1):" in text
    assert "HISTORY (oldest first) (1):" in text
    # Entry line shape: "- <date> [<tier>/<kind>] <content>".
    assert (
        "- 2026-05-01 [kg/fact] prefers late checkout (confidence 0.90)"
        in text
    )
    assert (
        "- 2026-05-02 [operations/booking] Booking confirmed: Villa "
        "2026-05-14→2026-05-17" in text
    )


def test_scope_omits_empty_ids() -> None:
    ctx = TemporalContext(
        scope=TimelineScope(property_id="p1"),
        as_of=_t(15),
    )
    text = format_context(ctx)
    assert "Scope: property=p1" in text
    assert "guest=" not in text


# ── TemporalAnalyzer ────────────────────────────────────────────────


class _FakeModel:
    """A fake chat model recording the structured call it received."""

    def __init__(
        self,
        result: BaseModel | None = None,
        *,
        boom: bool = False,
    ) -> None:
        self._result = result
        self._boom = boom
        self.seen_messages: Any = None
        self.seen_schema: Any = None

    async def invoke_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
    ) -> BaseModel:
        self.seen_messages = messages
        self.seen_schema = output_schema
        if self._boom:
            raise RuntimeError("model down")
        assert self._result is not None
        return self._result


async def test_no_model_degrades_gracefully() -> None:
    fact = _entry(at=_t(1), tier="kg", kind="fact", content="x")
    result = await TemporalAnalyzer().analyze(
        _ctx(history=[fact]),
        "How is this guest?",
    )
    assert result.analysis is None
    assert result.llm_used is False
    assert result.note == "no chat model configured"
    assert result.context_entry_count == 1
    assert result.question == "How is this guest?"
    assert result.as_of == _t(15)
    assert result.scope == _SCOPE


async def test_happy_path_returns_analysis_and_prompt_grounding() -> None:
    answer = TemporalAnalysis(
        answer="The guest is mid-stay at the Villa.",
        key_findings=["Active booking 2026-05-14→2026-05-17"],
        confidence=0.8,
    )
    model = _FakeModel(answer)
    booking = _entry(
        at=_t(2),
        tier="operations",
        kind="booking",
        content="Booking confirmed: Villa 2026-05-14→2026-05-17",
    )
    ctx = _ctx(history=[booking], live=[booking])

    result = await TemporalAnalyzer(model).analyze(ctx, "Where is the guest?")

    assert result.llm_used is True
    assert result.analysis is answer
    assert result.context_entry_count == 1
    assert result.note == ""
    # The model was asked for the right schema and saw the question +
    # the rendered context (so the answer is grounded, not free-floating).
    assert model.seen_schema is TemporalAnalysis
    user_turn = model.seen_messages[1]["content"]
    assert "QUESTION: Where is the guest?" in user_turn
    assert "LIVE NOW (1):" in user_turn
    assert "Villa 2026-05-14" in user_turn


async def test_model_failure_degrades_gracefully() -> None:
    model = _FakeModel(boom=True)
    result = await TemporalAnalyzer(model).analyze(_ctx(), "q?")
    assert result.analysis is None
    assert result.llm_used is False
    assert "analysis failed" in result.note
    assert "model down" in result.note


async def test_unexpected_output_type_degrades_gracefully() -> None:
    class _Other(BaseModel):
        value: int = 0

    model = _FakeModel(_Other(value=1))
    result = await TemporalAnalyzer(model).analyze(_ctx(), "q?")
    assert result.analysis is None
    assert result.llm_used is False
    assert result.note == "unexpected model output type"
