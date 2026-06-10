"""Tests for the PM-chat temporal surface (Phase 3, PR3c).

* :func:`format_pm_reply` — answer + bulleted findings, no findings, empty
  analysis;
* :func:`respond` over a fake analyzer + fake timeline — the happy path
  (reply text + structured result + question/as_of threaded through) and
  the degraded path (no analysis ⇒ ``None``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from brain_engine.conversation.temporal_pm import (
    TemporalPmReply,
    format_pm_reply,
    respond,
)
from brain_engine.memory.memory_timeline import TimelineScope
from brain_engine.temporal_analysis import (
    TemporalAnalysis,
    TemporalAnalysisResult,
)


def _t(day: int) -> datetime:
    return datetime(2026, 5, day, 12, 0, tzinfo=UTC)


_SCOPE = TimelineScope(property_id="p1", guest_id="g1")


def _result(
    analysis: TemporalAnalysis | None,
    *,
    note: str = "",
    count: int = 3,
) -> TemporalAnalysisResult:
    return TemporalAnalysisResult(
        question="q",
        as_of=_t(15),
        scope=_SCOPE,
        analysis=analysis,
        llm_used=analysis is not None,
        context_entry_count=count,
        note=note,
    )


class _FakeTimeline:
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def read(
        self,
        scope: TimelineScope,
        *,
        as_of: datetime | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        self.seen = {"as_of": as_of, "limit": limit}
        return []


class _FakeAnalyzer:
    def __init__(self, result: TemporalAnalysisResult) -> None:
        self._result = result
        self.seen: dict[str, Any] = {}

    async def analyze(
        self,
        context: Any,
        question: str,
    ) -> TemporalAnalysisResult:
        self.seen = {"question": question, "context": context}
        return self._result


# ── format_pm_reply ─────────────────────────────────────────────────


def test_format_answer_with_findings() -> None:
    analysis = TemporalAnalysis(
        answer="The guest is mid-stay.",
        key_findings=["Active booking at the Villa", "No open incidents"],
        confidence=0.8,
    )
    text = format_pm_reply(_result(analysis))
    assert text == (
        "The guest is mid-stay.\n"
        "\n"
        "- Active booking at the Villa\n"
        "- No open incidents"
    )


def test_format_answer_without_findings() -> None:
    analysis = TemporalAnalysis(answer="Nothing notable.", key_findings=[])
    assert format_pm_reply(_result(analysis)) == "Nothing notable."


def test_format_blank_findings_are_dropped() -> None:
    analysis = TemporalAnalysis(
        answer="A.",
        key_findings=["  ", "real", ""],
    )
    assert format_pm_reply(_result(analysis)) == "A.\n\n- real"


def test_format_empty_analysis_is_empty_string() -> None:
    assert format_pm_reply(_result(None)) == ""


# ── respond ─────────────────────────────────────────────────────────


async def test_respond_builds_reply_and_threads_inputs() -> None:
    analysis = TemporalAnalysis(
        answer="One upcoming stay.",
        key_findings=["Arrival 2026-05-20"],
        confidence=0.7,
    )
    analyzer = _FakeAnalyzer(_result(analysis, count=5))
    timeline = _FakeTimeline()

    reply = await respond(
        "What's coming up?",
        _SCOPE,
        analyzer=analyzer,
        timeline=timeline,
        as_of=_t(15),
        limit=50,
    )

    assert isinstance(reply, TemporalPmReply)
    assert reply.text == "One upcoming stay.\n\n- Arrival 2026-05-20"
    assert reply.result.context_entry_count == 5
    # The PM's question reached the analyzer; the window reached the timeline.
    assert analyzer.seen["question"] == "What's coming up?"
    assert timeline.seen == {"as_of": _t(15), "limit": 50}


async def test_respond_returns_none_when_degraded() -> None:
    analyzer = _FakeAnalyzer(_result(None, note="no chat model configured"))

    reply = await respond(
        "anything?",
        _SCOPE,
        analyzer=analyzer,
        timeline=_FakeTimeline(),
    )

    assert reply is None
