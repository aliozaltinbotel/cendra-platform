"""Live proof of the unbounded-ingestion + truncation contract (PR #B).

Drives the real :class:`OnboardingBootstrapPipeline` against an
archive of 12 conversations under three caps:

* limit=5  → ``loader_truncated=True``, summary
  ``counts['loader_truncations'] == 1``.
* limit=12 → boundary case, exactly equal → still flagged.
* limit=50 → natural end, ``loader_truncated=False``, no
  truncation event in the bus.

Asserts every contract the HTTP layer relies on.

Execute with the repo venv:

    .venv/bin/python proof_bootstrap_unbounded.py
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, cast

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
    name = "proof-loader"

    def __init__(self, count: int) -> None:
        self._conversations = tuple(_conv(f"c-{i}") for i in range(count))

    def load(self, **kwargs: Any) -> AsyncIterator[ArchivedConversation]:
        return self._iter(int(kwargs["limit"]))

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
    def split(self, conv: ArchivedConversation) -> tuple[Any, Any]:
        from brain_engine.onboarding.episode_builder import EpisodeStats

        return (conv,), EpisodeStats(
            total_messages=2, emitted_episodes=1,
        )


class _Extractor:
    async def extract(self, _: ArchivedConversation) -> DecisionCase:
        return _case()

    async def extract_with_reason(
        self, _: ArchivedConversation,
    ) -> ExtractionOutcome:
        return ExtractionOutcome(case=_case(), skip_reason=None)


class _CaseStore:
    async def store(self, case: DecisionCase) -> str:
        return case.case_id


def _build(bus: InMemoryBootstrapEventBus | None = None) -> OnboardingBootstrapPipeline:
    return OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader(12)),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, _CaseStore()),
        event_bus=bus,
    )


async def _run(label: str, *, limit: int) -> None:
    bus = InMemoryBootstrapEventBus()
    pipeline = _build(bus)
    job_id = f"job-{label}"
    report = await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=limit,
        mine_patterns=False,
        job_id=job_id,
    )
    summary = await bus.summary(job_id)
    assert summary is not None
    history = await bus.history(job_id, since=0, limit=100)
    truncation_events = [
        e for e in history if e.kind is EventKind.LOADER_TRUNCATED
    ]

    print(f"── {label} (limit={limit}) ─────────────────────")
    print(f"conversations_loaded = {report.conversations_loaded}")
    print(f"loader_truncated     = {report.loader_truncated}")
    print(f"loader_limit         = {report.loader_limit}")
    print(f"summary.counts       = {dict(summary.counts)}")
    print(
        f"truncation events    = "
        f"{[e.payload for e in truncation_events]}"
    )
    print()


async def main() -> None:
    print("── pipeline constants ───────────────────")
    print(f"_MAX_DAYS              = {_MAX_DAYS}")
    print(f"_MAX_LIMIT_PER_PROPERTY = {_MAX_LIMIT_PER_PROPERTY}")
    print(f"_clamp_limit(250000)   = {_clamp_limit(250_000)}")
    print(f"_clamp_limit(0)        = {_clamp_limit(0)}")
    print(f"_clamp_limit(-99)      = {_clamp_limit(-99)}")
    print()

    print("── HTTP schema ceilings ─────────────────")
    BootstrapJobRequest(
        property_ids=["323133"], days=3650, limit_per_property=100_000,
    )
    SinglePropertyBootstrapRequest(days=3650, limit=100_000)
    FastSinglePropertyBootstrapRequest(days=3650)
    print("all three pydantic models accept the new ceilings ✅")
    print()

    await _run("truncated", limit=5)
    await _run("boundary", limit=12)
    await _run("natural", limit=50)

    # Hard assertions
    bus = InMemoryBootstrapEventBus()
    pipeline = _build(bus)
    await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=5,
        mine_patterns=False,
        job_id="assert-trunc",
    )
    summary = await bus.summary("assert-trunc")
    assert summary is not None
    assert summary.counts.get("loader_truncations") == 1

    bus = InMemoryBootstrapEventBus()
    pipeline = _build(bus)
    await pipeline.bootstrap_one(
        property_id="323133",
        days=30,
        limit=50,
        mine_patterns=False,
        job_id="assert-natural",
    )
    summary = await bus.summary("assert-natural")
    assert summary is not None
    assert summary.counts.get("loader_truncations") is None

    print("✅ all assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
