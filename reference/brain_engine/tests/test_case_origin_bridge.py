"""Bridge tests for :pyattr:`DecisionCase.origin`.

Mümin 2026-05-15 round-5 #3 — the FL-12 endpoint
``GET /api/v1/patterns/rules/{rule_id}/origin`` was returning an
empty ``source_event_ids`` list because, although the
:class:`FoundationAnalysisOrchestrator` built a complete
:class:`PatternOrigin` in ``_log_origin``, nothing on the live nor
the bootstrap path persisted that origin onto the
:class:`DecisionCase`.  PR-B introduces an ``origin`` keyword on
:meth:`CaseBuilder.build` and wires both callers (live conversation
service + historical bootstrap extractor) to pass an origin so the
trail reaches storage.

These tests pin three contracts:

1. :class:`CaseBuilder` accepts the new ``origin`` kwarg and writes
   it verbatim onto :class:`DecisionCase`.  Omitting it keeps the
   pre-PR-B behaviour: the default empty :class:`PatternOrigin`.
2. The historical bootstrap path builds an origin in-place from
   ``conversation_id`` plus the resolved foundation slug — the
   orchestrator is not invoked on that code path.
3. The case round-trips through the in-memory store with the origin
   intact so the downstream miner (PR-C) sees the real
   ``source_event_ids``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
from brain_engine.patterns.store import InMemoryDecisionCaseStore

# ---------------------------------------------------------------------------
# CaseBuilder.origin keyword
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_builder_writes_supplied_origin_verbatim() -> None:
    """The new kwarg lands on :pyattr:`DecisionCase.origin`."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    origin = PatternOrigin(
        foundation_scenario_ids=("s2_63_guest_asks_if_early_checkin_is",),
        source_event_ids=("evt-1234",),
    )
    case = await builder.build(
        message_text="early check-in please",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        decision_type=DecisionType.APPROVE,
        origin=origin,
    )
    assert case.origin == origin
    assert case.origin.source_event_ids == ("evt-1234",)
    assert case.origin.foundation_scenario_ids == (
        "s2_63_guest_asks_if_early_checkin_is",
    )


@pytest.mark.asyncio
async def test_case_builder_default_origin_is_empty_pattern_origin() -> None:
    """Omitting ``origin`` keeps the pre-PR-B empty default."""
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
    assert case.origin.source_event_ids == ()
    assert case.origin.foundation_scenario_ids == ()
    assert case.origin.contributing_signal_ids == ()


@pytest.mark.asyncio
async def test_case_builder_explicit_none_origin_uses_default() -> None:
    """Explicit ``origin=None`` is treated identically to omitting the kwarg."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="early check-in please",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        decision_type=DecisionType.APPROVE,
        origin=None,
    )
    assert case.origin.source_event_ids == ()
    assert case.origin.foundation_scenario_ids == ()


# ---------------------------------------------------------------------------
# Round-trip through the in-memory store keeps origin intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_origin_survives_in_memory_store_round_trip() -> None:
    """The store does not strip ``origin`` when persisting + reading back."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    origin = PatternOrigin(
        foundation_scenario_ids=("s4_201_guest_arrives_at_the_property",),
        source_event_ids=("evt-round-trip",),
    )
    case = await builder.build(
        message_text="we just arrived",
        response_text="welcome",
        property_id="prop-rt",
        owner_id="owner-rt",
        stage=BookingStage.CHECKIN,
        scenario=Scenario.GENERAL,
        decision_type=DecisionType.INFORM,
        origin=origin,
    )
    store = InMemoryDecisionCaseStore()
    await store.store(case)

    fetched_rows = await store.search(
        property_id="prop-rt",
        limit=10,
    )
    assert len(fetched_rows) == 1
    fetched = fetched_rows[0]
    assert fetched.origin.source_event_ids == ("evt-round-trip",)
    assert fetched.origin.foundation_scenario_ids == (
        "s4_201_guest_arrives_at_the_property",
    )


# ---------------------------------------------------------------------------
# HistoricalCaseExtractor builds origin from conversation context
# ---------------------------------------------------------------------------


class _StubOrchestrator:
    """Stub orchestrator returning a fixed dominant slug.

    The bootstrap path calls
    :meth:`HistoricalCaseExtractor._resolve_foundation_scenario_id`
    which in turn invokes ``orchestrator.analyze``.  We only need
    the dominant slug here — the case's origin is built locally from
    the conversation, not from this orchestrator's result.
    """

    def __init__(self, dominant: str | None) -> None:
        self._dominant = dominant

    async def analyze(self, event: object) -> object:
        from brain_engine.analysis.models import (
            AnalysisEvent,
            AnalysisResult,
            FoundationMatch,
        )

        assert isinstance(event, AnalysisEvent)
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
            ),
        )


def _make_conversation(
    conversation_id: str = "conv-origin-1",
) -> ArchivedConversation:
    """Build a minimal :class:`ArchivedConversation` for the extractor."""
    guest_ts = datetime(2026, 5, 15, 10, 0, tzinfo=UTC)
    pm_ts = datetime(2026, 5, 15, 10, 5, tzinfo=UTC)
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
        conversation_id=conversation_id,
        property_id="prop-1",
        reservation_id="res-1",
        guest_id="guest-1",
        messages=(guest, pm),
        started_at=guest_ts,
        ended_at=pm_ts,
        channel="whatsapp",
        owner_id="owner-1",
        guest_name="guest-1",
        arrival_date=datetime(2026, 5, 16, 13, 0, tzinfo=UTC),
        departure_date=datetime(2026, 5, 19, 11, 0, tzinfo=UTC),
        reservation_data={},
    )


@pytest.mark.asyncio
async def test_historical_extractor_seeds_origin_with_conversation_id() -> (
    None
):
    """Bootstrap origin always carries the conversation_id as a source event."""
    extractor = HistoricalCaseExtractor(
        case_builder=CaseBuilder(feature_builder=FeatureBuilder()),
        classifier=DecisionClassifier(),
        foundation_orchestrator=None,  # no slug resolution needed
    )
    case = await extractor.extract(
        _make_conversation(conversation_id="conv-seeded"),
    )

    assert case is not None
    assert case.origin.source_event_ids == ("conv-seeded",)
    # No orchestrator wired → no foundation slug, so the origin's
    # ``foundation_scenario_ids`` stays empty.
    assert case.origin.foundation_scenario_ids == ()


@pytest.mark.asyncio
async def test_historical_extractor_origin_carries_foundation_slug() -> None:
    """When the resolver returns a slug it lands on origin too."""
    orchestrator = _StubOrchestrator(
        dominant="s2_63_guest_asks_if_early_checkin_is",
    )
    extractor = HistoricalCaseExtractor(
        case_builder=CaseBuilder(feature_builder=FeatureBuilder()),
        classifier=DecisionClassifier(),
        foundation_orchestrator=orchestrator,
    )
    case = await extractor.extract(
        _make_conversation(conversation_id="conv-with-slug"),
    )

    assert case is not None
    assert case.origin.source_event_ids == ("conv-with-slug",)
    assert case.origin.foundation_scenario_ids == (
        "s2_63_guest_asks_if_early_checkin_is",
    )
    # The singular field stays in sync with the origin's tuple.
    assert case.foundation_scenario_id == (
        "s2_63_guest_asks_if_early_checkin_is"
    )
