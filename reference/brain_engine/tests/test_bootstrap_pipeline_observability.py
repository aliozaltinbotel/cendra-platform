"""End-to-end tests for the bootstrap pipeline's realtime audit log.

These tests drive a real :class:`OnboardingBootstrapPipeline`
through :class:`InMemoryBootstrapEventBus` so the contract between
the pipeline's emit points and the audit-log consumer is exercised
without mocks at the boundary.

Coverage pinned:

* Three conversations land — one is empty, one fails to extract,
  one succeeds → the bus sees the corresponding
  :class:`EventKind` sequence with the right :class:`SkipReason`
  on every skip.
* :meth:`InMemoryBootstrapEventBus.summary` reports per-reason
  breakdowns the HTTP layer hands back to the operator.
* No bus configured → the pipeline emits nothing (pre-Phase-A
  bit-for-bit equivalence).
* When :meth:`HistoricalCaseExtractor.extract_with_reason` raises
  :class:`HistoricalExtractionError`, the bus records
  ``SkipReason.CLASSIFIER_FAILED`` rather than letting the error
  propagate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.onboarding.errors import HistoricalExtractionError
from brain_engine.onboarding.event_bus import (
    EventKind,
    InMemoryBootstrapEventBus,
    SkipReason,
)
from brain_engine.onboarding.historical_case_extractor import (
    ExtractionOutcome,
)
from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)


# ── Fixtures ─────────────────────────────────────────────────────


def _conversation(
    *,
    conversation_id: str,
    property_id: str = "323133",
    with_pm: bool = True,
    with_guest: bool = True,
) -> ArchivedConversation:
    """Build a minimal archived conversation."""
    messages: list[ArchivedMessage] = []
    base_ts = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    if with_guest:
        messages.append(
            ArchivedMessage(
                sender=MessageSender.GUEST,
                text="Can I have the door code?",
                sent_at=base_ts,
                language="en",
            )
        )
    if with_pm:
        messages.append(
            ArchivedMessage(
                sender=MessageSender.PM,
                text="Sure, the door code is 1234.",
                sent_at=base_ts.replace(hour=13),
                language="en",
            )
        )
    return ArchivedConversation(
        conversation_id=conversation_id,
        property_id=property_id,
        reservation_id=f"r-{conversation_id}",
        guest_id=f"g-{conversation_id}",
        messages=tuple(messages),
        started_at=base_ts,
        ended_at=base_ts.replace(hour=14),
    )


def _decision_case(
    *,
    property_id: str = "323133",
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    decision_type: DecisionType = DecisionType.INFORM,
) -> DecisionCase:
    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=scenario,
        property_id=property_id,
        owner_id="",
        decision=DecisionAction(action_type=decision_type),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
    )


class _FakeArchiveLoader:
    """Yields a pre-baked tuple of conversations."""

    name = "fake-loader"

    def __init__(
        self, conversations: tuple[ArchivedConversation, ...],
    ) -> None:
        self._conversations = conversations

    def load(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> AsyncIterator[ArchivedConversation]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ArchivedConversation]:
        for c in self._conversations:
            yield c


class _PassthroughEpisodeBuilder:
    """Returns each conversation as its single own episode."""

    def split(
        self,
        conversation: ArchivedConversation,
    ) -> tuple[tuple[ArchivedConversation, ...], Any]:
        from brain_engine.onboarding.episode_builder import EpisodeStats

        if not conversation.messages:
            return (), EpisodeStats()
        return (
            (conversation,),
            EpisodeStats(
                total_messages=len(conversation.messages),
                emitted_episodes=1,
            ),
        )


class _ScriptedExtractor:
    """Returns a pre-baked outcome per conversation_id."""

    def __init__(
        self,
        outcomes: dict[str, ExtractionOutcome],
        *,
        raises_for: tuple[str, ...] = (),
    ) -> None:
        self._outcomes = outcomes
        self._raises = set(raises_for)

    async def extract(
        self, conversation: ArchivedConversation,
    ) -> DecisionCase | None:
        outcome = await self.extract_with_reason(conversation)
        return outcome.case

    async def extract_with_reason(
        self, conversation: ArchivedConversation,
    ) -> ExtractionOutcome:
        cid = conversation.conversation_id
        if cid in self._raises:
            raise HistoricalExtractionError(
                "classifier blew up",
                conversation_id=cid,
                property_id=conversation.property_id,
            )
        return self._outcomes[cid]


class _MemoryCaseStore:
    """Captures every persisted :class:`DecisionCase`."""

    def __init__(self) -> None:
        self.stored: list[DecisionCase] = []

    async def store(self, case: DecisionCase) -> str:
        self.stored.append(case)
        return case.case_id


def _build_pipeline(
    *,
    conversations: tuple[ArchivedConversation, ...],
    extractor: _ScriptedExtractor,
    event_bus: InMemoryBootstrapEventBus | None = None,
) -> tuple[OnboardingBootstrapPipeline, _MemoryCaseStore]:
    case_store = _MemoryCaseStore()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _FakeArchiveLoader(conversations)),
        episode_builder=cast(Any, _PassthroughEpisodeBuilder()),
        case_extractor=cast(Any, extractor),
        case_store=cast(Any, case_store),
        event_bus=event_bus,
    )
    return pipeline, case_store


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_emits_full_event_sequence_for_mixed_outcomes() -> None:
    """Pipeline emits one event per decision point with stable kinds."""
    good = _conversation(conversation_id="c-good")
    empty = _conversation(
        conversation_id="c-empty", with_guest=False, with_pm=False,
    )
    no_pm = _conversation(conversation_id="c-no-pm", with_pm=False)

    extractor = _ScriptedExtractor(
        outcomes={
            "c-good": ExtractionOutcome(
                case=_decision_case(), skip_reason=None,
            ),
            "c-no-pm": ExtractionOutcome(
                case=None,
                skip_reason=SkipReason.NO_PM_RESPONSE_AFTER_GUEST,
            ),
        },
    )
    bus = InMemoryBootstrapEventBus()
    pipeline, case_store = _build_pipeline(
        conversations=(good, empty, no_pm),
        extractor=extractor,
        event_bus=bus,
    )

    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=50,
        mine_patterns=False,
        job_id="job-mixed",
    )

    assert report.conversations_loaded == 3
    assert report.cases_extracted == 1
    assert report.cases_skipped == 2
    assert len(case_store.stored) == 1

    history = await bus.history("job-mixed", since=0, limit=100)
    kinds = [e.kind for e in history]
    assert kinds[0] is EventKind.JOB_STARTED
    assert kinds[-1] is EventKind.JOB_DONE
    # All three conversations emit CONVERSATION_LOADED — the
    # empty-thread skip still counts as "loaded" since it cost the
    # loader an iteration.
    assert kinds.count(EventKind.CONVERSATION_LOADED) == 3
    assert kinds.count(EventKind.CONVERSATION_SKIPPED) == 1
    assert kinds.count(EventKind.CASE_EXTRACTED) == 1
    assert kinds.count(EventKind.CASE_SKIPPED) == 1

    case_skipped = next(
        e for e in history if e.kind is EventKind.CASE_SKIPPED
    )
    assert case_skipped.payload["reason"] == (
        SkipReason.NO_PM_RESPONSE_AFTER_GUEST.value
    )
    case_extracted = next(
        e for e in history if e.kind is EventKind.CASE_EXTRACTED
    )
    assert case_extracted.payload["scenario"] == (
        Scenario.ACCESS_CODE_RELEASE.value
    )

    summary = await bus.summary("job-mixed")
    assert summary is not None
    assert summary.status == "done"
    assert summary.counts["conversations_loaded"] == 3
    assert summary.counts["cases_extracted"] == 1
    assert summary.counts["cases_skipped"] == 1
    assert summary.counts["conversations_skipped"] == 1
    assert summary.skip_breakdown == {
        # `c-empty` has zero messages → diagnosed as
        # ``no_guest_message`` by the pipeline-level helper.
        SkipReason.NO_GUEST_MESSAGE.value: 1,
        # `c-no-pm` carries a guest message but no PM reply, so
        # the extractor returns the case-level reason.
        SkipReason.NO_PM_RESPONSE_AFTER_GUEST.value: 1,
    }


@pytest.mark.asyncio
async def test_pipeline_records_classifier_failure_as_skip_reason() -> None:
    """A propagated HistoricalExtractionError collapses to CLASSIFIER_FAILED."""
    boom = _conversation(conversation_id="c-boom")
    extractor = _ScriptedExtractor(
        outcomes={},
        raises_for=("c-boom",),
    )
    bus = InMemoryBootstrapEventBus()
    pipeline, _ = _build_pipeline(
        conversations=(boom,),
        extractor=extractor,
        event_bus=bus,
    )

    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=10,
        mine_patterns=False,
        job_id="job-boom",
    )
    assert report.cases_skipped == 1

    history = await bus.history("job-boom", since=0, limit=100)
    case_skipped = next(
        e for e in history if e.kind is EventKind.CASE_SKIPPED
    )
    assert case_skipped.payload["reason"] == (
        SkipReason.CLASSIFIER_FAILED.value
    )


@pytest.mark.asyncio
async def test_pipeline_without_bus_emits_nothing() -> None:
    """No bus wired → behaviour is bit-for-bit equivalent to the legacy path."""
    good = _conversation(conversation_id="c-good")
    extractor = _ScriptedExtractor(
        outcomes={
            "c-good": ExtractionOutcome(
                case=_decision_case(), skip_reason=None,
            ),
        },
    )
    pipeline, case_store = _build_pipeline(
        conversations=(good,),
        extractor=extractor,
        event_bus=None,
    )
    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=10,
        mine_patterns=False,
    )
    assert report.cases_extracted == 1
    assert len(case_store.stored) == 1
    # NullBootstrapEventBus has no observable surface — the absence of
    # an emission is the contract.  Confirm via the type:
    from brain_engine.onboarding.event_bus import NullBootstrapEventBus

    assert isinstance(pipeline.event_bus, NullBootstrapEventBus)


@pytest.mark.asyncio
async def test_pipeline_auto_generates_job_id_when_not_supplied() -> None:
    """Without an explicit ``job_id`` the pipeline still emits under a hex id."""
    good = _conversation(conversation_id="c-good")
    extractor = _ScriptedExtractor(
        outcomes={
            "c-good": ExtractionOutcome(
                case=_decision_case(), skip_reason=None,
            ),
        },
    )
    bus = InMemoryBootstrapEventBus()
    pipeline, _ = _build_pipeline(
        conversations=(good,),
        extractor=extractor,
        event_bus=bus,
    )
    await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=10,
        mine_patterns=False,
    )
    # The InMemory bus exposes its underlying dict via the summary API;
    # we cannot enumerate jobs without poking the private state, but
    # the contract guarantees *some* job_id ended up keyed.
    assert bus._summaries  # noqa: SLF001 - intentional whitebox probe
    assert all(
        len(jid) == 32
        for jid in bus._summaries  # noqa: SLF001
    )
