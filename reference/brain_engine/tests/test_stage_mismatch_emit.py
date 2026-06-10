"""Tests for the Q5-C SSE visibility hooks (PR #302).

Covers two surfaces that PR #301 left invisible to the operator:

* ``_maybe_emit_stage_mismatch`` — emits one
  ``STAGE_MISMATCH_DETECTED`` SSE event to PM Chat when the
  FL-16 orchestrator reported a contradiction.  Re-uses the
  missing-info dedup ledger so a repeated adversarial turn does
  not flood the panel.  Failure-tolerant: never raises into
  the main pipeline.
* ``_parse_stage_mismatch_detail`` — splits the stable
  ``"calendar=X scenario=Y"`` detail into its two parts.

The endpoint projection (``/api/admin/foundation/analyze``
``decisions.stage_mismatch`` + ``decisions.stage_mismatch_detail``
fields) is covered by ``tests/test_foundation_audit_router.py``
indirectly — the orchestrator tests already verify the source
fields land on :class:`AnalysisResult`, and any consumer that
calls ``getattr(result, ...)`` will see them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from brain_engine.conversation.service import (
    _MISSING_INFO_DEDUP,
    _maybe_emit_stage_mismatch,
    _parse_stage_mismatch_detail,
)
from brain_engine.streaming import current_emitter as _current_emitter
from brain_engine.streaming.event_types import EventType

# ── _parse_stage_mismatch_detail ─────────────────────────── #


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "calendar=post_checkout scenario=pre_arrival",
            ("post_checkout", "pre_arrival"),
        ),
        (
            "calendar=in_stay scenario=pre_booking",
            ("in_stay", "pre_booking"),
        ),
        ("", ("", "")),
        ("garbage", ("", "")),
        ("calendar= scenario=", ("", "")),
        ("scenario=foo calendar=bar", ("bar", "foo")),
    ],
)
def test_parse_detail_splits_correctly(
    detail: str,
    expected: tuple[str, str],
) -> None:
    """Stable format ``"calendar=X scenario=Y"`` splits cleanly."""
    assert _parse_stage_mismatch_detail(detail) == expected


# ── _maybe_emit_stage_mismatch ───────────────────────────── #


@dataclass(slots=True)
class _CapturedEvent:
    """Captures emitted events for assertions."""

    event_type: Any
    payload: dict[str, Any]


class _FakeEmitter:
    """Captures every ``emit()`` call for verification."""

    def __init__(self) -> None:
        self.events: list[_CapturedEvent] = []

    def emit(self, event_type: Any, payload: dict[str, Any]) -> None:
        self.events.append(_CapturedEvent(event_type, payload))


@dataclass(slots=True)
class _FakeMatch:
    dominant_catalog_entry: Any | None = None
    dominant_scenario_id: str = ""


@dataclass(slots=True)
class _FakeCatalogEntry:
    scenario_id: str = ""


@dataclass(slots=True)
class _FakeAnalysisResult:
    stage_mismatch: bool = False
    stage_mismatch_detail: str = ""
    foundation_match: Any = field(default_factory=_FakeMatch)


@dataclass(slots=True)
class _FakeConversation:
    conversation_id: str = ""


@pytest.fixture
def captured_emitter() -> _FakeEmitter:
    """Bind a capturing emitter for the duration of the test."""
    emitter = _FakeEmitter()
    token = _current_emitter.set_current_emitter(emitter)
    yield emitter
    _current_emitter.reset_current_emitter(token)


@pytest.fixture(autouse=True)
def _reset_dedup_ledger() -> None:
    """Each test starts with a clean dedup ledger."""
    _MISSING_INFO_DEDUP.clear()
    yield
    _MISSING_INFO_DEDUP.clear()


def test_emit_skipped_when_foundation_analysis_none(
    captured_emitter: _FakeEmitter,
) -> None:
    """No FoundationAnalysisResult ⇒ no SSE event."""
    _maybe_emit_stage_mismatch(
        foundation_analysis=None,
        conversation=_FakeConversation(),
    )
    assert captured_emitter.events == []


def test_emit_skipped_when_no_mismatch(
    captured_emitter: _FakeEmitter,
) -> None:
    """``stage_mismatch=False`` ⇒ no SSE event."""
    result = _FakeAnalysisResult(
        stage_mismatch=False,
        stage_mismatch_detail="",
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(),
    )
    assert captured_emitter.events == []


def test_emit_skipped_when_detail_empty(
    captured_emitter: _FakeEmitter,
) -> None:
    """``stage_mismatch=True`` but empty detail ⇒ no SSE event."""
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail="",
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(),
    )
    assert captured_emitter.events == []


def test_emit_fires_on_hard_mismatch(
    captured_emitter: _FakeEmitter,
) -> None:
    """Hard mismatch with full detail ⇒ one SSE event with parsed parts."""
    entry = _FakeCatalogEntry(
        scenario_id="s3_103_guest_asks_for_wifi_password_before",
    )
    match = _FakeMatch(
        dominant_catalog_entry=entry,
        dominant_scenario_id=entry.scenario_id,
    )
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail=("calendar=post_checkout scenario=pre_arrival"),
        foundation_match=match,
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(conversation_id="conv-1"),
    )
    assert len(captured_emitter.events) == 1
    evt = captured_emitter.events[0]
    assert evt.event_type == EventType.STAGE_MISMATCH_DETECTED
    assert evt.payload == {
        "detail": "calendar=post_checkout scenario=pre_arrival",
        "scenario_id": ("s3_103_guest_asks_for_wifi_password_before"),
        "calendar_stage": "post_checkout",
        "scenario_stage": "pre_arrival",
    }


def test_emit_dedups_within_same_conversation(
    captured_emitter: _FakeEmitter,
) -> None:
    """Same mismatch fingerprint within TTL ⇒ second call suppressed."""
    entry = _FakeCatalogEntry(scenario_id="s3_103_test")
    match = _FakeMatch(
        dominant_catalog_entry=entry,
        dominant_scenario_id=entry.scenario_id,
    )
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail=("calendar=post_checkout scenario=pre_arrival"),
        foundation_match=match,
    )
    conv = _FakeConversation(conversation_id="conv-2")
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=conv,
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=conv,
    )
    # First fired, second deduplicated.
    assert len(captured_emitter.events) == 1


def test_emit_different_conversations_not_deduplicated(
    captured_emitter: _FakeEmitter,
) -> None:
    """Different conversation_id ⇒ both events fire (dedup is per-conv)."""
    entry = _FakeCatalogEntry(scenario_id="s3_103_test")
    match = _FakeMatch(
        dominant_catalog_entry=entry,
        dominant_scenario_id=entry.scenario_id,
    )
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail=("calendar=post_checkout scenario=pre_arrival"),
        foundation_match=match,
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(conversation_id="conv-A"),
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(conversation_id="conv-B"),
    )
    assert len(captured_emitter.events) == 2


def test_emit_falls_back_to_dominant_scenario_id_when_entry_missing(
    captured_emitter: _FakeEmitter,
) -> None:
    """No catalog entry ⇒ scenario_id comes from match.dominant_scenario_id."""
    match = _FakeMatch(
        dominant_catalog_entry=None,
        dominant_scenario_id="s5_242_fallback",
    )
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail=("calendar=in_stay scenario=pre_booking"),
        foundation_match=match,
    )
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(conversation_id="conv-3"),
    )
    assert len(captured_emitter.events) == 1
    assert (
        captured_emitter.events[0].payload["scenario_id"] == "s5_242_fallback"
    )


def test_emit_never_raises_on_unexpected_input(
    captured_emitter: _FakeEmitter,
) -> None:
    """Garbage foundation_analysis ⇒ no event, no exception."""

    class _Broken:
        @property
        def stage_mismatch(self) -> bool:
            raise RuntimeError("broken")

    # Must not propagate the RuntimeError — visibility detection
    # is non-fatal.
    _maybe_emit_stage_mismatch(
        foundation_analysis=_Broken(),
        conversation=_FakeConversation(conversation_id="conv-4"),
    )
    assert captured_emitter.events == []


def test_emit_no_op_when_emitter_unbound() -> None:
    """No ContextVar emitter ⇒ helper is a silent no-op."""
    # Do not bind any emitter — the helper must still succeed.
    entry = _FakeCatalogEntry(scenario_id="s3_103_test")
    match = _FakeMatch(
        dominant_catalog_entry=entry,
        dominant_scenario_id=entry.scenario_id,
    )
    result = _FakeAnalysisResult(
        stage_mismatch=True,
        stage_mismatch_detail=("calendar=post_checkout scenario=pre_arrival"),
        foundation_match=match,
    )
    # No assertion — only checks the call does not raise.
    _maybe_emit_stage_mismatch(
        foundation_analysis=result,
        conversation=_FakeConversation(conversation_id="conv-5"),
    )
