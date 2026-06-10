"""Tests for bootstrap-to-memory wiring (PR #E).

Mümin 2026-05-13: bootstrap historically wrote only to
DecisionCaseStore / PatternRuleStore / PropertyProfileStore.  The
9-tier memory stack — Brain Engine's signature feature — was
under-fed: ``/memory/timeline`` returned only PM corrections, the
EpisodicMemory tier was silent on the historical archive.

This module pins the new contract:

* :class:`OnboardingBootstrapPipeline` takes an optional
  ``episodic_memory`` ctor arg.  When wired, every persisted
  :class:`DecisionCase` triggers one ``add_episode`` call carrying
  the case's scenario, message, and ``decision_at`` anchor.
* :func:`_timeline_from_case_store` projects DecisionCaseStore
  rows into :class:`TimelineEpisodeResponse` ordered newest-first.
* The HTTP timeline merges PM-store + case-store sources and
  caps the union at ``top_k``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from brain_engine.api.profile_endpoints import (
    _decision_case_store_optional,
    _deps,
    _timeline_from_case_store,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
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


_BASE_TS = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)


def _conversation(cid: str) -> ArchivedConversation:
    return ArchivedConversation(
        conversation_id=cid,
        property_id="323133",
        reservation_id=f"r-{cid}",
        guest_id=f"g-{cid}",
        messages=(
            ArchivedMessage(
                sender=MessageSender.GUEST,
                text="Need the door code",
                sent_at=_BASE_TS,
                language="en",
            ),
            ArchivedMessage(
                sender=MessageSender.PM,
                text="Code is 1234",
                sent_at=_BASE_TS.replace(hour=13),
                language="en",
            ),
        ),
        started_at=_BASE_TS,
        ended_at=_BASE_TS.replace(hour=14),
    )


def _case(
    *,
    case_id: str = "case-1",
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    decision_at: datetime | None = None,
    message_text: str = "Need the door code",
) -> DecisionCase:
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.PRE_ARRIVAL,
        scenario=scenario,
        property_id="323133",
        owner_id="",
        message_text=message_text,
        decision=DecisionAction(action_type=DecisionType.INFORM),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
        created_at=decision_at or _BASE_TS,
    )


# ── Fakes ────────────────────────────────────────────────────────


class _RecordingEpisodic:
    """Captures every :meth:`add_episode` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_episode(
        self,
        event: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {"event": event, "content": content, "metadata": metadata or {}}
        )
        return None


class _RecordingSemantic:
    """Captures every :meth:`store` call (mimics SemanticMemory)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def store(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "text": text,
                "metadata": metadata or {},
                "record_id": record_id or "",
            },
        )
        return record_id or "auto"


class _RecordingKG:
    """Captures every :meth:`add_knowledge` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_knowledge(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return None


class _Loader:
    name = "memory-test-loader"

    def __init__(self, conversations: tuple[ArchivedConversation, ...]) -> None:
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
        for conv in self._conversations:
            yield conv


class _Episodes:
    def split(
        self, conv: ArchivedConversation,
    ) -> tuple[Any, Any]:
        from brain_engine.onboarding.episode_builder import EpisodeStats

        return (conv,), EpisodeStats(
            total_messages=len(conv.messages),
            emitted_episodes=1,
        )


class _Extractor:
    async def extract(
        self, conv: ArchivedConversation,
    ) -> DecisionCase | None:
        return _case(case_id=f"c-{conv.conversation_id}")

    async def extract_with_reason(
        self, conv: ArchivedConversation,
    ) -> ExtractionOutcome:
        return ExtractionOutcome(
            case=_case(case_id=f"c-{conv.conversation_id}"),
            skip_reason=None,
        )


class _CaseStore:
    def __init__(self) -> None:
        self.stored: list[DecisionCase] = []

    async def store(self, case: DecisionCase) -> str:
        self.stored.append(case)
        return case.case_id


class _SearchableCaseStore(_CaseStore):
    """Adds the search/list surface that
    :func:`_timeline_from_case_store` calls."""

    async def search(
        self,
        *,
        property_id: str | None = None,
        scenario: Any = None,
        owner_id: str | None = None,
        stage: Any = None,
        limit: int = 100,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[DecisionCase]:
        rows = [
            c for c in self.stored
            if not property_id or c.property_id == property_id
        ]
        rows.sort(key=lambda c: c.created_at, reverse=True)
        return rows[offset:offset + limit]


# ── Bootstrap → EpisodicMemory ───────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_records_episode_for_each_persisted_case() -> None:
    """Every tier (Episodic + Semantic + KG) receives one write per case."""
    episodic = _RecordingEpisodic()
    semantic = _RecordingSemantic()
    kg = _RecordingKG()
    case_store = _CaseStore()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(
            Any, _Loader((_conversation("a"), _conversation("b"))),
        ),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, case_store),
        episodic_memory=cast(Any, episodic),
        semantic_memory=cast(Any, semantic),
        knowledge_graph=cast(Any, kg),
    )
    await pipeline.bootstrap_one(
        property_id="323133",
        mine_patterns=False,
        job_id="job-mem",
    )
    assert len(case_store.stored) == 2
    assert len(episodic.calls) == 2
    assert len(semantic.calls) == 2
    assert len(kg.calls) == 2

    # Semantic write — record_id must equal case_id for dedup.
    sem = semantic.calls[0]
    assert sem["record_id"] == "c-a"
    assert sem["metadata"]["source"] == "bootstrap"
    assert sem["metadata"]["scenario"] == (
        Scenario.ACCESS_CODE_RELEASE.value
    )

    # KG write — anchored on property entity.
    kg_call = kg.calls[0]
    assert kg_call["entity_type"] == "property"
    assert kg_call["entity_id"] == "323133"
    assert kg_call["source"] == "bootstrap"
    assert "bootstrap" in kg_call["tags"]
    assert (
        Scenario.ACCESS_CODE_RELEASE.value in kg_call["keywords"]
    )

    first = episodic.calls[0]
    assert first["event"] == Scenario.ACCESS_CODE_RELEASE.value
    assert first["content"] == "Need the door code"
    meta = first["metadata"]
    assert meta["property_id"] == "323133"
    assert meta["source"] == "bootstrap"
    assert meta["case_id"] == "c-a"
    assert meta["scenario"] == Scenario.ACCESS_CODE_RELEASE.value
    assert meta["decision_type"] == DecisionType.INFORM.value
    assert meta["stage"] == BookingStage.PRE_ARRIVAL.value
    assert meta["decision_at"]  # ISO timestamp must be present


@pytest.mark.asyncio
async def test_bootstrap_skips_episode_on_dry_run() -> None:
    """dry_run=true must not write to memory or store."""
    episodic = _RecordingEpisodic()
    case_store = _CaseStore()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader((_conversation("a"),))),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, case_store),
        episodic_memory=cast(Any, episodic),
    )
    await pipeline.bootstrap_one(
        property_id="323133",
        mine_patterns=False,
        dry_run=True,
        job_id="job-dry",
    )
    assert case_store.stored == []
    assert episodic.calls == []


@pytest.mark.asyncio
async def test_bootstrap_swallows_episodic_failures() -> None:
    """A broken EpisodicMemory must not abort the pipeline."""

    class _BrokenEpisodic:
        async def add_episode(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("episodic is down")

    case_store = _CaseStore()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader((_conversation("a"),))),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, case_store),
        episodic_memory=cast(Any, _BrokenEpisodic()),
    )
    report = await pipeline.bootstrap_one(
        property_id="323133",
        mine_patterns=False,
        job_id="job-broken",
    )
    assert report.cases_extracted == 1
    assert len(case_store.stored) == 1  # case still landed


@pytest.mark.asyncio
async def test_bootstrap_without_episodic_is_silent_noop() -> None:
    """Pipeline built without ``episodic_memory`` keeps prior behaviour."""
    case_store = _CaseStore()
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=cast(Any, _Loader((_conversation("a"),))),
        episode_builder=cast(Any, _Episodes()),
        case_extractor=cast(Any, _Extractor()),
        case_store=cast(Any, case_store),
    )
    report = await pipeline.bootstrap_one(
        property_id="323133",
        mine_patterns=False,
        job_id="job-noopt",
    )
    assert report.cases_extracted == 1
    assert len(case_store.stored) == 1


# ── Timeline reads from DecisionCaseStore ────────────────────────


@pytest.mark.asyncio
async def test_timeline_from_case_store_returns_none_when_unwired() -> None:
    """No store configured ⇒ None so caller routes to PM-only path."""
    _deps.pop("decision_case_store", None)
    result = await _timeline_from_case_store(
        property_id="323133",
        top_k=10,
        time_from=None,
        time_to=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_timeline_from_case_store_projects_cases() -> None:
    """Each persisted case becomes one timeline episode (newest-first)."""
    store = _SearchableCaseStore()
    older = _case(
        case_id="c-old",
        decision_at=_BASE_TS.replace(year=2026, month=4, day=1),
        message_text="Older question",
    )
    newer = _case(
        case_id="c-new",
        decision_at=_BASE_TS.replace(year=2026, month=5, day=10),
        message_text="Newer question",
    )
    await store.store(older)
    await store.store(newer)
    _deps["decision_case_store"] = store

    result = await _timeline_from_case_store(
        property_id="323133",
        top_k=10,
        time_from=None,
        time_to=None,
    )
    assert result is not None
    assert len(result) == 2
    # Newest-first ordering.
    assert result[0].name == Scenario.ACCESS_CODE_RELEASE.value
    assert "Newer question" in result[0].content
    assert "Older question" in result[1].content
    assert result[0].source == "decision_case_store"


@pytest.mark.asyncio
async def test_timeline_from_case_store_honours_top_k_and_bounds() -> None:
    """top_k caps the result; from_time / to_time filter inclusively."""
    store = _SearchableCaseStore()
    cases = [
        _case(
            case_id=f"c-{i}",
            decision_at=_BASE_TS.replace(day=1 + i),
            message_text=f"msg-{i}",
        )
        for i in range(5)
    ]
    for c in cases:
        await store.store(c)
    _deps["decision_case_store"] = store

    capped = await _timeline_from_case_store(
        property_id="323133",
        top_k=2,
        time_from=None,
        time_to=None,
    )
    assert capped is not None
    assert len(capped) == 2

    windowed = await _timeline_from_case_store(
        property_id="323133",
        top_k=10,
        time_from=_BASE_TS.replace(day=2),
        time_to=_BASE_TS.replace(day=4),
    )
    assert windowed is not None
    assert len(windowed) == 3  # days 2, 3, 4
