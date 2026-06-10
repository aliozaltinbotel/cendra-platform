"""Sprint 6 W1 wiring tests — FL-16 orchestrator on conversation service.

Pins:

* :func:`_foundation_orchestrator_enabled` reads
  ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` and returns ``False`` by
  default — operators must opt in.
* :meth:`ConversationService._run_foundation_analysis` is a no-op
  when the flag is off, when the orchestrator is missing, or when
  the cleaned message is empty.  Orchestrator failures are
  swallowed and ``state.foundation_analysis`` stays ``None``.
* When flag-on + orchestrator wired + non-empty message, the
  orchestrator's :class:`AnalysisResult` is attached to
  :pyattr:`PipelineState.foundation_analysis` and the synthesised
  :class:`AnalysisEvent` carries the expected ``property_id``,
  ``text``, ``event_type``, ``reservation_id`` and ``guest_id``.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisEventType,
    AnalysisResult,
    FoundationMatch,
)
from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ResponseFlags,
)
from brain_engine.conversation.service import (
    ConversationService,
    _foundation_orchestrator_enabled,
)
from brain_engine.patterns.models import PatternOrigin

# ── stubs ─────────────────────────────────────────────────── #


class _RecordingOrchestrator:
    """Records every :class:`AnalysisEvent` it is handed.

    Returns a deterministic :class:`AnalysisResult` so the assertions
    can pin both the inbound event shape and the outbound state
    propagation.
    """

    def __init__(
        self,
        *,
        result: AnalysisResult | None = None,
    ) -> None:
        self.calls: list[AnalysisEvent] = []
        self._result = result

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        self.calls.append(event)
        if self._result is not None:
            return self._result
        return AnalysisResult(
            event_id=event.event_id,
            foundation_match=FoundationMatch(),
            origin=PatternOrigin(
                foundation_scenario_ids=(),
                source_event_ids=(event.event_id,),
                contributing_signal_ids=(),
            ),
        )


class _RaisingOrchestrator:
    """Always raises — used to verify exception swallowing."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        del event
        self.calls += 1
        raise RuntimeError("simulated orchestrator failure")


# ── fixtures ──────────────────────────────────────────────── #


def _state(
    *,
    message: str = "early check-in please",
    property_id: str = "prop-1",
    reservation_id: str = "res-42",
    guest_name: str = "guest-1",
) -> PipelineState:
    """Build a minimal :class:`PipelineState` for the orchestrator step."""
    request = ConversationRequest(
        customer_id="customer-1",
        property_id=property_id,
        reservation_id=reservation_id,
        guest_name=guest_name,
        history=(),
        guest_message=message,
    )
    state = PipelineState(request=request)
    state.cleaned_message = message
    state.response_flags = ResponseFlags()
    return state


def _service(
    *,
    orchestrator: Any = None,
) -> ConversationService:
    """Build a service with only the orchestrator dependency wired."""
    return ConversationService(
        foundation_orchestrator=orchestrator,
    )


# ── env flag ──────────────────────────────────────────────── #


def test_flag_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var set, the helper returns ``False``."""
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        raising=False,
    )
    assert _foundation_orchestrator_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Standard truthy strings enable the orchestrator step."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        value,
    )
    assert _foundation_orchestrator_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "off"],
)
def test_flag_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Standard falsy strings keep the orchestrator step disabled."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        value,
    )
    assert _foundation_orchestrator_enabled() is False


# ── _run_foundation_analysis ──────────────────────────────── #


@pytest.mark.asyncio
async def test_analysis_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off ⇒ orchestrator never called, state untouched."""
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        raising=False,
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state()
    await service._run_foundation_analysis(state)
    assert orchestrator.calls == []
    assert state.foundation_analysis is None


@pytest.mark.asyncio
async def test_analysis_skipped_when_orchestrator_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but no orchestrator ⇒ no-op, state untouched."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    service = _service(orchestrator=None)
    state = _state()
    await service._run_foundation_analysis(state)
    assert state.foundation_analysis is None


@pytest.mark.asyncio
async def test_analysis_skipped_when_message_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty / whitespace-only message bypasses the orchestrator."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state(message="   ")
    await service._run_foundation_analysis(state)
    assert orchestrator.calls == []
    assert state.foundation_analysis is None


@pytest.mark.asyncio
async def test_analysis_attaches_result_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on + orchestrator wired ⇒ result lands on the state."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state()
    await service._run_foundation_analysis(state)
    assert state.foundation_analysis is not None
    assert isinstance(state.foundation_analysis, AnalysisResult)
    assert state.foundation_analysis.event_id == orchestrator.calls[0].event_id


@pytest.mark.asyncio
async def test_analysis_event_carries_request_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesised :class:`AnalysisEvent` mirrors the inbound request."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state(
        message="My door lock won't open",
        property_id="villa-azul",
        reservation_id="res-2026-77",
        guest_name="Mehmet",
    )
    await service._run_foundation_analysis(state)
    assert len(orchestrator.calls) == 1
    event = orchestrator.calls[0]
    assert event.event_type is AnalysisEventType.MESSAGE
    assert event.property_id == "villa-azul"
    assert event.text == "My door lock won't open"
    assert event.reservation_id == "res-2026-77"
    assert event.guest_id == "Mehmet"
    assert event.payload == {"customer_id": "customer-1"}
    assert event.event_id  # auto-generated UUID, not empty


@pytest.mark.asyncio
async def test_analysis_event_id_is_unique_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each call generates a fresh UUID — never re-used."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    await service._run_foundation_analysis(_state())
    await service._run_foundation_analysis(_state())
    assert len({c.event_id for c in orchestrator.calls}) == 2


@pytest.mark.asyncio
async def test_analysis_handles_blank_reservation_and_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank request fields collapse to ``None`` on the event."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state(
        reservation_id="   ",
        guest_name="",
    )
    await service._run_foundation_analysis(state)
    event = orchestrator.calls[0]
    assert event.reservation_id is None
    assert event.guest_id is None


@pytest.mark.asyncio
async def test_analysis_swallows_orchestrator_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising orchestrator logs + falls through — state untouched."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RaisingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state()
    await service._run_foundation_analysis(state)
    # The orchestrator was invoked but the exception was swallowed.
    assert orchestrator.calls == 1
    assert state.foundation_analysis is None


@pytest.mark.asyncio
async def test_analysis_preserves_existing_state_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step touches only ``foundation_analysis`` — nothing else."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "1",
    )
    orchestrator = _RecordingOrchestrator()
    service = _service(orchestrator=orchestrator)
    state = _state()
    state.requires_pm_approval = False
    state.response_flags.send_status = True
    await service._run_foundation_analysis(state)
    assert state.requires_pm_approval is False
    assert state.response_flags.send_status is True
    assert state.cleaned_message == "early check-in please"
