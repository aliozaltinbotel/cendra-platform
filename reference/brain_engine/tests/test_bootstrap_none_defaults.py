"""Tests for the ``None`` → "ingest the entire archive" defaults.

Mümin 2026-05-12 follow-up: the synchronous defaults
(``days=180``, ``limit=500``) silently dropped legitimate
historical conversations for any property whose archive exceeded
6 months or 500 threads.  PR :pr:`252` reframed the defaults: a
caller that omits either knob is treated as "give me the whole
archive", and the pipeline resolves the missing value to the
system ceiling (:data:`_MAX_DAYS` = 3650, :data:`_MAX_LIMIT_PER_PROPERTY`
= 100 000).

This module pins:

* :class:`BootstrapRequest` accepts ``days=None`` and
  ``limit_per_property=None`` without raising.
* :func:`_resolve_days` maps ``None`` → :data:`_MAX_DAYS` and
  clamps numerics into ``[_MIN_DAYS, _MAX_DAYS]``.
* The pydantic schemas default both knobs to ``None`` and accept
  empty request bodies.
* :meth:`OnboardingBootstrapPipeline.bootstrap_one` invoked
  without ``days`` / ``limit`` ingests every conversation the
  loader yields and reports the system ceiling as
  ``loader_limit``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from brain_engine.api.onboarding_endpoints import (
    BootstrapJobRequest,
    FastSinglePropertyBootstrapRequest,
    SinglePropertyBootstrapRequest,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    _MAX_DAYS,
    _MAX_LIMIT_PER_PROPERTY,
    _MIN_DAYS,
    BootstrapRequest,
    OnboardingBootstrapPipeline,
    _resolve_days,
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


# ── _resolve_days ───────────────────────────────────────────────


def test_resolve_days_none_maps_to_system_max() -> None:
    assert _resolve_days(None) == _MAX_DAYS


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, _MIN_DAYS),
        (-5, _MIN_DAYS),
        (1, 1),
        (180, 180),
        (3650, 3650),
        (10_000, _MAX_DAYS),
    ],
)
def test_resolve_days_clamps_numeric_bounds(
    value: int, expected: int,
) -> None:
    assert _resolve_days(value) == expected


# ── Dataclass + pydantic schema defaults ────────────────────────


def test_bootstrap_request_defaults_to_none() -> None:
    req = BootstrapRequest(property_ids=("323133",))
    assert req.days is None
    assert req.limit_per_property is None


def test_bootstrap_job_request_accepts_empty_body() -> None:
    payload = BootstrapJobRequest(property_ids=["323133"])
    assert payload.days is None
    assert payload.limit_per_property is None


def test_single_property_request_accepts_empty_body() -> None:
    body = SinglePropertyBootstrapRequest()
    assert body.days is None
    assert body.limit is None


def test_fast_single_property_request_accepts_empty_body() -> None:
    body = FastSinglePropertyBootstrapRequest()
    assert body.days is None


def test_explicit_values_still_accepted_within_bounds() -> None:
    payload = BootstrapJobRequest(
        property_ids=["323133"], days=30, limit_per_property=1000,
    )
    assert payload.days == 30
    assert payload.limit_per_property == 1000


# ── End-to-end: omitted knobs → whole archive ───────────────────


def _conv(cid: str) -> ArchivedConversation:
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    return ArchivedConversation(
        conversation_id=cid,
        property_id="323133",
        reservation_id=f"r-{cid}",
        guest_id=f"g-{cid}",
        messages=(
            ArchivedMessage(
                sender=MessageSender.GUEST,
                text="hello",
                sent_at=base,
                language="en",
            ),
            ArchivedMessage(
                sender=MessageSender.PM,
                text="hi",
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
    """Honours ``limit`` exactly like the real GraphQL adapter."""

    name = "none-defaults-loader"

    def __init__(self, count: int) -> None:
        self._count = count

    def load(
        self, *, property_id: str, since: datetime,
        until: datetime, limit: int,
    ) -> AsyncIterator[ArchivedConversation]:
        return self._iter(limit)

    async def _iter(
        self, limit: int,
    ) -> AsyncIterator[ArchivedConversation]:
        emitted = 0
        for i in range(self._count):
            if emitted >= limit:
                return
            emitted += 1
            yield _conv(f"c-{i}")


class _Episodes:
    def split(
        self, conv: ArchivedConversation,
    ) -> tuple[Any, Any]:
        from brain_engine.onboarding.episode_builder import EpisodeStats

        return (conv,), EpisodeStats(
            total_messages=len(conv.messages), emitted_episodes=1,
        )


class _Extractor:
    async def extract(
        self, conv: ArchivedConversation,
    ) -> DecisionCase | None:
        return _case()

    async def extract_with_reason(
        self, conv: ArchivedConversation,
    ) -> ExtractionOutcome:
        return ExtractionOutcome(case=_case(), skip_reason=None)


class _Store:
    async def store(self, case: DecisionCase) -> str:
        return case.case_id


@pytest.mark.asyncio
async def test_bootstrap_one_with_no_knobs_ingests_everything() -> None:
    """Caller omits ``days``/``limit`` → entire 25-conversation archive."""
    bus = InMemoryBootstrapEventBus()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader(25)),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, _Store()),
        event_bus=bus,
    )
    report = await pipeline.bootstrap_one(
        property_id="323133",
        mine_patterns=False,
        job_id="job-defaults",
    )
    assert report.conversations_loaded == 25
    assert report.cases_extracted == 25
    assert report.loader_truncated is False
    # The pipeline reports the *effective* cap it ran against.
    assert report.loader_limit == _MAX_LIMIT_PER_PROPERTY

    started_events = [
        e
        for e in await bus.history("job-defaults", since=0, limit=100)
        if e.kind is EventKind.JOB_STARTED
    ]
    assert len(started_events) == 1
    payload = started_events[0].payload
    # The event also carries the resolved values so the operator
    # never has to guess what was applied.
    assert payload["days"] == _MAX_DAYS
    assert payload["limit"] == _MAX_LIMIT_PER_PROPERTY


@pytest.mark.asyncio
async def test_bootstrap_one_explicit_limit_still_truncates() -> None:
    """Passing a small ``limit`` keeps the truncation contract intact."""
    bus = InMemoryBootstrapEventBus()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader(25)),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, _Store()),
        event_bus=bus,
    )
    report = await pipeline.bootstrap_one(
        property_id="323133",
        limit=5,
        mine_patterns=False,
        job_id="job-explicit",
    )
    assert report.conversations_loaded == 5
    assert report.loader_truncated is True
    assert report.loader_limit == 5
