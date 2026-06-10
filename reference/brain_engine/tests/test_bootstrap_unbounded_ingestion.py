"""Tests for the unbounded-ingestion contract (PR #B).

Pins:

* Pipeline constants raised — ``_MAX_DAYS = 3650`` (10 years) and
  ``_MAX_LIMIT_PER_PROPERTY = 100_000``.
* ``_clamp_limit`` clamps any int into ``[1, _MAX_LIMIT_PER_PROPERTY]``.
* The HTTP schemas (``BootstrapJobRequest``,
  ``SinglePropertyBootstrapRequest``, ``FastSinglePropertyBootstrapRequest``)
  accept the new ceilings and reject values above them.
* ``BootstrapPropertyReport.loader_truncated`` flips True when the
  loader stopped because the caller's cap was reached, and the bus
  receives one :attr:`EventKind.LOADER_TRUNCATED` event with the
  cap + actual count in its payload.
* The natural-end path keeps ``loader_truncated=False`` and emits no
  truncation event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import ValidationError

from brain_engine.api.onboarding_endpoints import (
    BootstrapJobRequest,
    FastSinglePropertyBootstrapRequest,
    SinglePropertyBootstrapRequest,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    _MAX_DAYS,
    _MAX_LIMIT_PER_PROPERTY,
    OnboardingBootstrapPipeline,
    _clamp_limit,
)
from brain_engine.onboarding.event_bus import (
    EventKind,
    InMemoryBootstrapEventBus,
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


# ── Pipeline constants ──────────────────────────────────────────


def test_max_days_is_ten_years() -> None:
    assert _MAX_DAYS == 3650


def test_max_limit_per_property_is_one_hundred_thousand() -> None:
    assert _MAX_LIMIT_PER_PROPERTY == 100_000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, 1),
        (0, 1),
        (1, 1),
        (500, 500),
        (100_000, 100_000),
        (250_000, 100_000),
    ],
)
def test_clamp_limit_bounds(value: int, expected: int) -> None:
    assert _clamp_limit(value) == expected


# ── HTTP schema ceilings ────────────────────────────────────────


def test_bootstrap_job_request_accepts_new_ceilings() -> None:
    payload = BootstrapJobRequest(
        property_ids=["323133"],
        days=3650,
        limit_per_property=100_000,
    )
    assert payload.days == 3650
    assert payload.limit_per_property == 100_000


def test_bootstrap_job_request_rejects_above_ceiling() -> None:
    with pytest.raises(ValidationError):
        BootstrapJobRequest(property_ids=["323133"], days=3651)
    with pytest.raises(ValidationError):
        BootstrapJobRequest(
            property_ids=["323133"], limit_per_property=100_001,
        )


def test_single_property_bootstrap_request_accepts_new_ceilings() -> None:
    body = SinglePropertyBootstrapRequest(
        days=3650, limit=100_000,
    )
    assert body.days == 3650
    assert body.limit == 100_000


def test_fast_single_property_bootstrap_request_accepts_new_ceiling() -> None:
    body = FastSinglePropertyBootstrapRequest(days=3650)
    assert body.days == 3650


# ── Fixtures for pipeline truncation ────────────────────────────


def _conversation(cid: str) -> ArchivedConversation:
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    return ArchivedConversation(
        conversation_id=cid,
        property_id="323133",
        reservation_id=f"r-{cid}",
        guest_id=f"g-{cid}",
        messages=(
            ArchivedMessage(
                sender=MessageSender.GUEST,
                text="hi",
                sent_at=base,
                language="en",
            ),
            ArchivedMessage(
                sender=MessageSender.PM,
                text="hello",
                sent_at=base.replace(hour=13),
                language="en",
            ),
        ),
        started_at=base,
        ended_at=base.replace(hour=14),
    )


def _case() -> DecisionCase:
    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id="323133",
        owner_id="",
        decision=DecisionAction(action_type=DecisionType.INFORM),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
    )


class _Loader:
    """Honors ``limit`` like the production GraphQL loader does."""

    name = "fixture-loader"

    def __init__(self, count: int) -> None:
        self._conversations = tuple(
            _conversation(f"c-{i}") for i in range(count)
        )

    def load(
        self, *, property_id: str, since: datetime,
        until: datetime, limit: int,
    ) -> AsyncIterator[ArchivedConversation]:
        return self._iter(limit)

    async def _iter(
        self, limit: int,
    ) -> AsyncIterator[ArchivedConversation]:
        emitted = 0
        for c in self._conversations:
            if emitted >= limit:
                return
            emitted += 1
            yield c


class _Episodes:
    def split(
        self, conv: ArchivedConversation,
    ) -> tuple[Any, Any]:
        from brain_engine.onboarding.episode_builder import EpisodeStats

        return (conv,), EpisodeStats(
            total_messages=len(conv.messages), emitted_episodes=1,
        )


class _Extractor:
    async def extract(self, conv: ArchivedConversation) -> DecisionCase:
        return _case()

    async def extract_with_reason(
        self, conv: ArchivedConversation,
    ) -> ExtractionOutcome:
        return ExtractionOutcome(case=_case(), skip_reason=None)


class _CaseStore:
    async def store(self, case: DecisionCase) -> str:
        return case.case_id


def _build_pipeline(
    *,
    conversation_count: int,
    event_bus: InMemoryBootstrapEventBus | None = None,
) -> OnboardingBootstrapPipeline:
    return OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader(conversation_count)),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, _CaseStore()),
        event_bus=event_bus,
    )


# ── Pipeline truncation contract ────────────────────────────────


@pytest.mark.asyncio
async def test_loader_truncation_flag_flips_when_limit_reached() -> None:
    """When the loader fills its quota the report flags truncation."""
    bus = InMemoryBootstrapEventBus()
    pipeline = _build_pipeline(conversation_count=10, event_bus=bus)
    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=5,
        mine_patterns=False,
        job_id="job-truncated",
    )
    assert report.conversations_loaded == 5
    assert report.loader_truncated is True
    assert report.loader_limit == 5

    history = await bus.history("job-truncated", since=0, limit=100)
    truncation_events = [
        e for e in history if e.kind is EventKind.LOADER_TRUNCATED
    ]
    assert len(truncation_events) == 1
    event = truncation_events[0]
    assert event.payload["limit"] == 5
    assert event.payload["conversations_loaded"] == 5


@pytest.mark.asyncio
async def test_loader_truncation_flag_stays_false_on_natural_end() -> None:
    """Loader exhausted before cap → no truncation flag, no event."""
    bus = InMemoryBootstrapEventBus()
    pipeline = _build_pipeline(conversation_count=3, event_bus=bus)
    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=10,
        mine_patterns=False,
        job_id="job-natural-end",
    )
    assert report.conversations_loaded == 3
    assert report.loader_truncated is False
    assert report.loader_limit == 10

    history = await bus.history(
        "job-natural-end", since=0, limit=100,
    )
    assert all(
        e.kind is not EventKind.LOADER_TRUNCATED for e in history
    )


@pytest.mark.asyncio
async def test_report_as_dict_includes_truncation_fields() -> None:
    pipeline = _build_pipeline(conversation_count=2)
    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=1,
        mine_patterns=False,
    )
    data = report.as_dict()
    assert data["loader_truncated"] is True
    assert data["loader_limit"] == 1


@pytest.mark.asyncio
async def test_summary_counts_loader_truncations() -> None:
    """Bus summary surfaces ``loader_truncations`` so the operator can filter."""
    bus = InMemoryBootstrapEventBus()
    pipeline = _build_pipeline(conversation_count=5, event_bus=bus)
    await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=2,
        mine_patterns=False,
        job_id="job-summary",
    )
    summary = await bus.summary("job-summary")
    assert summary is not None
    assert summary.counts.get("loader_truncations") == 1
