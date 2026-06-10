"""Bridge tests for foundation_scenario_id on DecisionCase.

These tests pin the three-layer chain that propagates the FL-16
matcher's dominant scenario id from analysis -> case -> rule:

1. :class:`CaseBuilder` exposes a keyword-only
   ``foundation_scenario_id`` parameter and writes it verbatim
   onto :class:`DecisionCase`.  ``None`` keeps the field ``None``
   so the pre-W1 path is bit-for-bit identical.

2. :class:`HistoricalCaseExtractor` calls the injected orchestrator
   on the guest message and attaches the resulting dominant slug
   to the case.  No orchestrator wired ⇒ case.foundation_scenario_id
   stays ``None``.  Orchestrator raises ⇒ the failure is swallowed
   (logged at WARNING) and the case still emits with ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisResult,
    FoundationMatch,
)
from brain_engine.onboarding.historical_case_extractor import (
    HistoricalCaseExtractor,
)
from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.models import (
    BookingStage,
    DecisionType,
    PatternOrigin,
    Scenario,
)

# ── CaseBuilder.build keyword propagation ─────────────────── #


@pytest.mark.asyncio
async def test_case_builder_writes_foundation_scenario_id() -> None:
    """The new kwarg lands on :class:`DecisionCase`."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="early check-in please",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        decision_type=DecisionType.APPROVE,
        foundation_scenario_id="s1_16_guest_asks_for_early_checkin_before",
    )
    assert case.foundation_scenario_id == (
        "s1_16_guest_asks_for_early_checkin_before"
    )


@pytest.mark.asyncio
async def test_case_builder_default_keeps_field_none() -> None:
    """No kwarg ⇒ ``foundation_scenario_id`` stays ``None``."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="early check-in please",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        decision_type=DecisionType.APPROVE,
    )
    assert case.foundation_scenario_id is None


@pytest.mark.asyncio
async def test_case_builder_empty_string_collapses_to_none() -> None:
    """An empty string is normalised to ``None`` so the DB sees ``NULL``."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="early check-in please",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        decision_type=DecisionType.APPROVE,
        foundation_scenario_id="",
    )
    assert case.foundation_scenario_id is None


# ── HistoricalCaseExtractor orchestrator wiring ───────────── #


class _RecordingOrchestrator:
    """Stub that records every event + returns a fixed dominant slug."""

    def __init__(
        self,
        *,
        dominant: str | None = "s3_112_guest_asks_if_they_can_check",
    ) -> None:
        self.calls: list[AnalysisEvent] = []
        self._dominant = dominant

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        self.calls.append(event)
        return AnalysisResult(
            event_id=event.event_id,
            foundation_match=FoundationMatch(
                dominant_scenario_id=self._dominant,
            ),
            origin=PatternOrigin(
                foundation_scenario_ids=(
                    (self._dominant,) if self._dominant else ()
                ),
                source_event_ids=(event.event_id,),
                contributing_signal_ids=(),
            ),
        )


class _RaisingOrchestrator:
    """Always raises — verifies the extractor swallows + degrades."""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        del event
        self.calls += 1
        raise RuntimeError("simulated matcher failure")


def _make_conversation() -> ArchivedConversation:
    """Build a minimal usable :class:`ArchivedConversation`."""
    guest_ts = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
    pm_ts = datetime(2026, 5, 13, 10, 5, tzinfo=UTC)
    guest = ArchivedMessage(
        sender=MessageSender.GUEST,
        text="Erken check-in mumkun mu?",
        sent_at=guest_ts,
        language="tr",
    )
    pm = ArchivedMessage(
        sender=MessageSender.PM,
        text="Tabi, saat 13:00'ten itibaren mumkun.",
        sent_at=pm_ts,
        language="tr",
    )
    return ArchivedConversation(
        conversation_id="conv-1",
        property_id="prop-1",
        reservation_id="res-1",
        guest_id="guest-1",
        messages=(guest, pm),
        started_at=guest_ts,
        ended_at=pm_ts,
        channel="whatsapp",
        owner_id="owner-1",
        guest_name="guest-1",
        arrival_date=datetime(2026, 5, 14, 13, 0, tzinfo=UTC),
        departure_date=datetime(2026, 5, 17, 11, 0, tzinfo=UTC),
        reservation_data={},
    )


def _build_extractor(orchestrator: object | None) -> HistoricalCaseExtractor:
    return HistoricalCaseExtractor(
        case_builder=CaseBuilder(feature_builder=FeatureBuilder()),
        classifier=DecisionClassifier(),
        foundation_orchestrator=orchestrator,
    )


@pytest.mark.asyncio
async def test_extractor_attaches_dominant_slug_when_orchestrator_wired() -> None:
    """Wired orchestrator ⇒ case carries the dominant scenario id."""
    orchestrator = _RecordingOrchestrator()
    extractor = _build_extractor(orchestrator)
    case = await extractor.extract(_make_conversation())
    assert case is not None
    assert case.foundation_scenario_id == (
        "s3_112_guest_asks_if_they_can_check"
    )
    # The matcher saw the guest text verbatim
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0].text == "Erken check-in mumkun mu?"
    assert orchestrator.calls[0].property_id == "prop-1"


@pytest.mark.asyncio
async def test_extractor_keeps_field_none_when_orchestrator_missing() -> None:
    """Default ``None`` orchestrator ⇒ case.foundation_scenario_id None."""
    extractor = _build_extractor(None)
    case = await extractor.extract(_make_conversation())
    assert case is not None
    assert case.foundation_scenario_id is None


@pytest.mark.asyncio
async def test_extractor_swallows_orchestrator_failure() -> None:
    """Raising orchestrator ⇒ case still emits with ``None`` slug."""
    orchestrator = _RaisingOrchestrator()
    extractor = _build_extractor(orchestrator)
    case = await extractor.extract(_make_conversation())
    assert case is not None
    assert case.foundation_scenario_id is None
    assert orchestrator.calls == 1


@pytest.mark.asyncio
async def test_extractor_handles_orchestrator_returning_no_dominant() -> None:
    """Orchestrator returns ``None`` slug ⇒ case.foundation_scenario_id None."""
    orchestrator = _RecordingOrchestrator(dominant=None)
    extractor = _build_extractor(orchestrator)
    case = await extractor.extract(_make_conversation())
    assert case is not None
    assert case.foundation_scenario_id is None
