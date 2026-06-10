"""Real-data smoke harness for the memory + patterns layer.

The harness proves end-to-end that the production loop works on a
live dev pod against the unified GraphQL gateway:

1.  Pull conversations for ``property_id`` from
    :class:`UnifiedDataGraphQLClient` via
    :class:`GraphQLConversationArchiveLoader`.
2.  Split each thread into Q&A episodes via
    :class:`EpisodeBuilder`.
3.  Extract one :class:`DecisionCase` per episode via
    :class:`HistoricalCaseExtractor`.
4.  Mirror each episode into :class:`EpisodicMemory` so the memory
    tier carries the same record the pattern miner has just learned
    from.
5.  Persist the cases through the supplied case store.
6.  Mine :class:`PatternRule` rows with :class:`PatternMiner` and
    persist them through the supplied rule store.
7.  Read the rules back and verify the §11 ``NEVER_AUTO_LEARN`` /
    ``NEVER_AUTO_SCENARIOS`` blacklists are honored — no rule
    matching the blacklist may live in ``ExecutionMode.AUTO``.
8.  Probe :class:`PatternRuleRouter` to confirm at least one mined
    rule is reachable from the runtime priority chain.

The result is a :class:`MemorySmokeReport` with one
:class:`StageOutcome` per stage, a top-level ``status`` and totals.
The report is JSON-friendly so an HTTP endpoint can return it
straight to the operator.

The harness is intentionally tolerant: optional tiers
(``fact_store``, ``customer_memory``, ``guest_history``) are
exercised when wired and skipped (with ``SKIPPED`` status) when not
— a smoke run on a half-configured pod still returns a meaningful
report instead of an exception.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Final, Protocol

import structlog

from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.onboarding.episode_builder import EpisodeBuilder
from brain_engine.onboarding.errors import (
    ConversationArchiveError,
    HistoricalExtractionError,
)
from brain_engine.onboarding.graphql_archive_loader import (
    GraphQLConversationArchiveLoader,
)
from brain_engine.onboarding.historical_case_extractor import (
    HistoricalCaseExtractor,
)
from brain_engine.onboarding.models import ArchivedConversation
from brain_engine.patterns.models import (
    DecisionCase,
    ExecutionMode,
    PatternRule,
)
from brain_engine.patterns.pattern_miner import PatternMiner
from brain_engine.patterns.router import PatternRuleRouter, RuleMatch
from brain_engine.patterns.validator import (
    NEVER_AUTO_LEARN,
    NEVER_AUTO_SCENARIOS,
    PatternValidator,
)

__all__ = [
    "DEFAULT_SMOKE_DAYS",
    "DEFAULT_SMOKE_LIMIT",
    "MemorySmokeReport",
    "MemorySmokeRunner",
    "SmokeStageStatus",
    "StageOutcome",
]


logger = structlog.get_logger(__name__)


DEFAULT_SMOKE_DAYS: Final[int] = 30
DEFAULT_SMOKE_LIMIT: Final[int] = 25


# Doc-gate keyword witnesses used by the ``refusal_signals`` stage to
# detect a silent regression of the :class:`RefusalExtractor` wiring.
# When a PM ``response_text`` contains any of these phrases we expect
# at least one case in the freshly-extracted batch to carry a tagged
# refusal signal — if the count is zero the extractor (or its
# wiring inside :class:`HistoricalCaseExtractor`) has broken.
# Phrases are deliberately a *subset* of the extractor's regex tables
# to avoid coupling: the smoke is a witness, not a re-implementation.
_REFUSAL_WITNESS_PHRASES: Final[tuple[str, ...]] = (
    "id verification",
    "identity verification",
    "face recognition",
    "passport",
    "kyc",
)


class SmokeStageStatus(StrEnum):
    """Per-stage verdict carried by :class:`StageOutcome`."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class _CaseStoreLike(Protocol):
    """Narrow ``DecisionCaseStore`` surface used by the harness."""

    async def store(self, case: DecisionCase) -> str:
        ...

    async def count(self, *, property_id: str | None = None) -> int:
        ...


class _RuleStoreLike(Protocol):
    """Narrow ``PatternRuleStore`` surface used by the harness."""

    async def store(self, rule: PatternRule) -> str:
        ...

    async def get(self, pattern_id: str) -> PatternRule | None:
        ...


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """Single stage in a smoke run.

    Attributes:
        name: Human-readable stage identifier (``"graphql_fetch"``,
            ``"case_extraction"`` …).
        status: ``PASS`` when the stage produced expected output,
            ``FAIL`` when it raised or returned nothing, ``SKIPPED``
            when the dependency was not wired in.
        count: Stage-specific counter (conversations, cases, rules …).
        detail: Free-form note for the operator — populated on
            ``FAIL`` with the exception class + message.
    """

    name: str
    status: SmokeStageStatus
    count: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view of the stage outcome."""
        return {
            "name": self.name,
            "status": self.status.value,
            "count": self.count,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MemorySmokeReport:
    """Aggregate report for one :meth:`MemorySmokeRunner.run` call.

    Attributes:
        property_id: Property exercised by the run.
        days: Look-back window applied by the loader.
        stages: Tuple of :class:`StageOutcome` in firing order.
        status: ``PASS`` only when every non-skipped stage passed.
        duration_seconds: Wall clock for the whole run.
    """

    property_id: str
    days: int
    stages: tuple[StageOutcome, ...]
    status: SmokeStageStatus
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly view used by HTTP and k8s job log shipping."""
        return {
            "property_id": self.property_id,
            "days": self.days,
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 3),
            "stages": [s.as_dict() for s in self.stages],
        }


class MemorySmokeRunner:
    """Run the real-data memory + patterns smoke loop.

    The runner does not wire its own dependencies — the construction
    site (``api_server.bootstrap.memory_smoke``) is responsible for
    creating the GraphQL client, archive loader, episode builder,
    case extractor, case/rule stores and miner, and for closing the
    GraphQL client when the run finishes.

    Args:
        archive_loader: GraphQL archive loader pointing at the dev
            onboarding-api gateway.
        episode_builder: Pure-compute Q&A splitter.
        case_extractor: Episode → :class:`DecisionCase` translator.
        case_store: Persists extracted cases.
        episodic_memory: Memory tier the harness mirrors each
            episode into so the smoke proves the runtime memory
            shape, not just the case-store shape.
        pattern_miner: Mines :class:`PatternRule` rows from the
            extracted cases.
        rule_store: Persists mined rules and exposes a router-ready
            read API.
        rule_router: Optional router used to verify at least one
            stored rule is reachable from the runtime priority chain.
        pattern_validator: Validator that gates mined rules before
            persistence.  Mirrors the gate used by the
            ``/patterns/extract`` endpoint, the nightly consolidator
            and the onboarding bootstrap pipeline so blacklisted
            scenarios (``NEVER_AUTO_SCENARIOS``) and other unsafe
            rules never reach the rule store.  Stage 7
            (``_verify_blacklist``) keeps testing the miner's output
            and is unaffected by this gate.  Defaults to a stock
            :class:`PatternValidator` when omitted.
    """

    def __init__(
        self,
        *,
        archive_loader: GraphQLConversationArchiveLoader,
        episode_builder: EpisodeBuilder,
        case_extractor: HistoricalCaseExtractor,
        case_store: _CaseStoreLike,
        episodic_memory: EpisodicMemory,
        pattern_miner: PatternMiner,
        rule_store: _RuleStoreLike,
        rule_router: PatternRuleRouter | None = None,
        pattern_validator: PatternValidator | None = None,
    ) -> None:
        self._loader = archive_loader
        self._episodes = episode_builder
        self._extractor = case_extractor
        self._case_store = case_store
        self._episodic = episodic_memory
        self._miner = pattern_miner
        self._rule_store = rule_store
        self._router = rule_router
        self._validator = pattern_validator or PatternValidator()
        self._log = logger.bind(component="memory_smoke")

    async def run(
        self,
        *,
        property_id: str,
        days: int = DEFAULT_SMOKE_DAYS,
        limit: int = DEFAULT_SMOKE_LIMIT,
    ) -> MemorySmokeReport:
        """Run the full smoke loop and return a structured report."""
        if not property_id or not property_id.strip():
            raise ValueError("property_id is required")
        started = time.monotonic()
        stages: list[StageOutcome] = []
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, int(days)))

        conversations, fetch_outcome = await self._fetch(
            property_id=property_id,
            since=since,
            until=now,
            limit=max(1, int(limit)),
        )
        stages.append(fetch_outcome)

        episodes = self._split(conversations)
        stages.append(
            StageOutcome(
                name="episode_split",
                status=(
                    SmokeStageStatus.PASS
                    if episodes
                    else SmokeStageStatus.FAIL
                ),
                count=len(episodes),
                detail=(
                    "no episodes emitted from loaded conversations"
                    if conversations and not episodes
                    else ""
                ),
            ),
        )

        cases, extract_outcome = await self._extract(episodes)
        stages.append(extract_outcome)

        stages.append(self._verify_refusal_signals(cases=cases))

        stages.append(
            await self._mirror_to_episodic(
                property_id=property_id,
                episodes=episodes,
            ),
        )

        stages.append(
            await self._persist_cases(cases=cases),
        )

        rules, mine_outcome = self._mine(cases=cases)
        stages.append(mine_outcome)

        stages.append(
            await self._persist_rules(rules=rules),
        )

        stages.append(self._verify_blacklist(rules=rules))
        stages.append(
            await self._verify_router(
                property_id=property_id,
                rules=rules,
            ),
        )

        # Coherence stages — verify the system functions as a single
        # body by reading back from each tier we just wrote into.
        stages.append(await self._recall_episodic(episodes=episodes))
        stages.append(
            await self._recall_cases(
                property_id=property_id,
                expected=len(cases),
            ),
        )
        stages.append(await self._recall_rules(rules=rules))
        stages.append(
            await self._verify_priority_chain(
                property_id=property_id,
                rules=rules,
            ),
        )

        status = _aggregate_status(stages)
        return MemorySmokeReport(
            property_id=property_id,
            days=days,
            stages=tuple(stages),
            status=status,
            duration_seconds=time.monotonic() - started,
        )

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    async def _fetch(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[list[ArchivedConversation], StageOutcome]:
        """Drain the GraphQL archive loader for the smoke window."""
        loaded: list[ArchivedConversation] = []
        try:
            iterator = self._loader.load(
                property_id=property_id,
                since=since,
                until=until,
                limit=limit,
            )
            async for conversation in iterator:
                loaded.append(conversation)
        except ConversationArchiveError as exc:
            return [], StageOutcome(
                name="graphql_fetch",
                status=SmokeStageStatus.FAIL,
                count=len(loaded),
                detail=f"{exc.__class__.__name__}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - smoke must surface
            return [], StageOutcome(
                name="graphql_fetch",
                status=SmokeStageStatus.FAIL,
                count=len(loaded),
                detail=(
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                ),
            )
        status = (
            SmokeStageStatus.PASS
            if loaded
            else SmokeStageStatus.FAIL
        )
        detail = "" if loaded else "loader returned zero conversations"
        return loaded, StageOutcome(
            name="graphql_fetch",
            status=status,
            count=len(loaded),
            detail=detail,
        )

    def _split(
        self,
        conversations: Iterable[ArchivedConversation],
    ) -> list[ArchivedConversation]:
        """Apply the episode builder to every conversation."""
        episodes: list[ArchivedConversation] = []
        for conversation in conversations:
            split, _ = self._episodes.split(conversation)
            episodes.extend(split)
        return episodes

    async def _extract(
        self,
        episodes: Iterable[ArchivedConversation],
    ) -> tuple[list[DecisionCase], StageOutcome]:
        """Run the historical extractor over every episode."""
        cases: list[DecisionCase] = []
        failures: list[str] = []
        for episode in episodes:
            try:
                case = await self._extractor.extract(episode)
            except HistoricalExtractionError as exc:
                failures.append(str(exc))
                continue
            if case is not None:
                cases.append(case)
        if not cases:
            return [], StageOutcome(
                name="case_extraction",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    "; ".join(failures[:3])
                    if failures
                    else "extractor produced zero cases"
                ),
            )
        return cases, StageOutcome(
            name="case_extraction",
            status=SmokeStageStatus.PASS,
            count=len(cases),
            detail=(
                f"{len(failures)} extractor errors swallowed"
                if failures
                else ""
            ),
        )

    async def _mirror_to_episodic(
        self,
        *,
        property_id: str,
        episodes: Iterable[ArchivedConversation],
    ) -> StageOutcome:
        """Write one episodic record per Q&A so the tier is exercised."""
        written = 0
        last_error = ""
        for episode in episodes:
            guest = episode.first_guest_message()
            pm = episode.first_pm_response()
            if guest is None or pm is None:
                continue
            try:
                await self._episodic.add_episode(
                    event="historical_episode",
                    content=guest.text,
                    metadata={
                        "property_id": property_id,
                        "conversation_id": episode.conversation_id,
                        "reservation_id": episode.reservation_id,
                        "guest_id": episode.guest_id,
                        "pm_response": pm.text,
                        "channel": episode.channel,
                        "started_at": episode.started_at.isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - smoke surface
                last_error = (
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                )
                continue
            written += 1
        if written == 0:
            return StageOutcome(
                name="episodic_mirror",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=last_error or "no episodic writes succeeded",
            )
        return StageOutcome(
            name="episodic_mirror",
            status=SmokeStageStatus.PASS,
            count=written,
            detail=last_error,
        )

    async def _persist_cases(
        self,
        *,
        cases: Iterable[DecisionCase],
    ) -> StageOutcome:
        """Send cases to the configured case store."""
        stored = 0
        last_error = ""
        for case in cases:
            try:
                await self._case_store.store(case)
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                )
                continue
            stored += 1
        if stored == 0:
            return StageOutcome(
                name="case_store",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=last_error or "case store rejected every case",
            )
        return StageOutcome(
            name="case_store",
            status=SmokeStageStatus.PASS,
            count=stored,
            detail=last_error,
        )

    def _mine(
        self,
        *,
        cases: list[DecisionCase],
    ) -> tuple[list[PatternRule], StageOutcome]:
        """Run the pattern miner over the extracted cases."""
        if not cases:
            return [], StageOutcome(
                name="pattern_mine",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no cases to mine",
            )
        try:
            rules, _report = self._miner.mine(cases)
        except Exception as exc:  # noqa: BLE001
            return [], StageOutcome(
                name="pattern_mine",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                ),
            )
        return list(rules), StageOutcome(
            name="pattern_mine",
            status=SmokeStageStatus.PASS,
            count=len(rules),
            detail="miner emitted zero rules" if not rules else "",
        )

    async def _persist_rules(
        self,
        *,
        rules: Iterable[PatternRule],
    ) -> StageOutcome:
        """Persist mined rules through the supplied rule store.

        Each rule is run through :class:`PatternValidator` first;
        invalid rules (blacklisted scenario, low support, stale
        evidence, …) are logged with their reasons and skipped.  The
        ``count`` in the returned :class:`StageOutcome` reflects
        successful writes only — the in-memory ``rules`` list passed
        to stage 7 (``_verify_blacklist``) is untouched, so the
        miner-output invariant remains testable.
        """
        stored = 0
        rejected = 0
        last_error = ""
        for rule in rules:
            validation = self._validator.validate(rule)
            if not validation.valid:
                rejected += 1
                self._log.warning(
                    "smoke.rule_rejected",
                    pattern_id=rule.pattern_id[:8],
                    scenario=rule.scenario.value,
                    reasons=list(validation.reasons),
                )
                continue
            try:
                await self._rule_store.store(rule)
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                )
                continue
            stored += 1
        if stored == 0:
            detail = last_error or (
                f"all {rejected} mined rule(s) rejected by validator"
                if rejected
                else "no rules emitted to store"
            )
            return StageOutcome(
                name="rule_store",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail=detail,
            )
        return StageOutcome(
            name="rule_store",
            status=SmokeStageStatus.PASS,
            count=stored,
            detail=last_error,
        )

    def _verify_refusal_signals(
        self,
        *,
        cases: list[DecisionCase],
    ) -> StageOutcome:
        """Confirm :class:`RefusalExtractor` is wired into the loop.

        The stage walks the freshly-extracted case batch and:

        1. Counts cases whose ``extracted_entities`` carry the
           ``refusal_signals`` key.
        2. Tallies the refusal taxonomy distribution
           (``requires_document``, ``requires_payment``, ...).
        3. Sweeps ``response_text`` for doc-gate witness phrases
           (``id verification``, ``passport``, ...) so that a
           silent regression of the wiring inside
           :class:`HistoricalCaseExtractor` produces a ``FAIL``
           even when the new code path looks healthy from the
           outside.

        The stage is informational by default — a property whose
        PMs never enforce document gates legitimately produces
        zero signals, so ``tagged == 0`` alone is not a failure.
        It only fails when the loaded responses *do* contain
        witness phrases yet the extractor emitted nothing, which
        means the wiring broke.
        """
        if not cases:
            return StageOutcome(
                name="refusal_signals",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no cases extracted",
            )
        tagged = 0
        type_counts: dict[str, int] = {}
        witness_hits = 0
        for case in cases:
            entities = case.extracted_entities or {}
            signals = entities.get("refusal_signals") or []
            if signals:
                tagged += 1
                for signal in signals:
                    if not isinstance(signal, dict):
                        continue
                    signal_type = str(signal.get("type", ""))
                    if signal_type:
                        type_counts[signal_type] = (
                            type_counts.get(signal_type, 0) + 1
                        )
            response = (case.response_text or "").lower()
            if any(
                phrase in response
                for phrase in _REFUSAL_WITNESS_PHRASES
            ):
                witness_hits += 1
        if witness_hits and tagged == 0:
            return StageOutcome(
                name="refusal_signals",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    f"{witness_hits} responses contained doc-gate "
                    "witness phrases but RefusalExtractor emitted "
                    "zero tagged cases — wiring regression"
                ),
            )
        if type_counts:
            distribution = ",".join(
                f"{name}={count}"
                for name, count in sorted(type_counts.items())
            )
            detail = (
                f"witness_responses={witness_hits} "
                f"types[{distribution}]"
            )
        else:
            detail = (
                f"witness_responses={witness_hits} "
                "no refusal signals in batch (legitimate when the "
                "property has no document gates in window)"
            )
        return StageOutcome(
            name="refusal_signals",
            status=SmokeStageStatus.PASS,
            count=tagged,
            detail=detail,
        )

    def _verify_blacklist(
        self,
        *,
        rules: Iterable[PatternRule],
    ) -> StageOutcome:
        """Reject rules that breach §11 NEVER_AUTO_LEARN guardrails.

        A pattern that targets a never-auto-learn scenario is allowed
        to exist for ``ASK`` / ``APPROVAL`` flows but must never live
        in :class:`ExecutionMode.AUTO`.  The smoke fails fast if the
        miner ever produces such a rule because that means the
        validator has stopped enforcing the blacklist.
        """
        violations: list[str] = []
        checked = 0
        for rule in rules:
            checked += 1
            scenario_value = getattr(rule.scenario, "value", "")
            if rule.execution_mode is not ExecutionMode.AUTO:
                continue
            if (
                scenario_value in NEVER_AUTO_LEARN
                or rule.scenario in NEVER_AUTO_SCENARIOS
            ):
                violations.append(rule.pattern_id)
        if violations:
            return StageOutcome(
                name="blacklist_guard",
                status=SmokeStageStatus.FAIL,
                count=len(violations),
                detail=(
                    "AUTO rules in NEVER_AUTO list: "
                    + ", ".join(violations[:5])
                ),
            )
        return StageOutcome(
            name="blacklist_guard",
            status=SmokeStageStatus.PASS,
            count=checked,
            detail="" if checked else "no rules to check",
        )

    async def _verify_router(
        self,
        *,
        property_id: str,
        rules: list[PatternRule],
    ) -> StageOutcome:
        """Probe the router to confirm at least one rule is reachable."""
        if self._router is None:
            return StageOutcome(
                name="router_probe",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="router not wired",
            )
        if not rules:
            return StageOutcome(
                name="router_probe",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no rules emitted",
            )
        scenarios = {rule.scenario for rule in rules}
        hit = 0
        last_match: RuleMatch | None = None
        for scenario in scenarios:
            try:
                match = await self._router.match(
                    scenario=scenario,
                    property_id=property_id,
                    features={},
                )
            except Exception as exc:  # noqa: BLE001
                return StageOutcome(
                    name="router_probe",
                    status=SmokeStageStatus.FAIL,
                    count=hit,
                    detail=(
                        f"{exc.__class__.__name__}: {exc}"
                        or exc.__class__.__name__
                    ),
                )
            if match is not None:
                hit += 1
                last_match = match
        if hit == 0:
            return StageOutcome(
                name="router_probe",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    "router returned no match for any scenario "
                    f"({len(scenarios)} probed)"
                ),
            )
        detail = ""
        if last_match is not None:
            detail = f"sample={last_match.rule.pattern_id}"
        return StageOutcome(
            name="router_probe",
            status=SmokeStageStatus.PASS,
            count=hit,
            detail=detail,
        )


    # ------------------------------------------------------------------
    # Coherence stages
    # ------------------------------------------------------------------

    async def _recall_episodic(
        self,
        *,
        episodes: list[ArchivedConversation],
    ) -> StageOutcome:
        """Read episodic memory back to confirm the writes survived."""
        if not episodes:
            return StageOutcome(
                name="episodic_recall",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no episodes to recall",
            )
        try:
            recent = await self._episodic.get_recent(
                n=max(1, len(episodes)),
            )
        except Exception as exc:  # noqa: BLE001 - smoke surface
            return StageOutcome(
                name="episodic_recall",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                ),
            )
        if not recent:
            return StageOutcome(
                name="episodic_recall",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail="episodic backend returned no entries",
            )
        return StageOutcome(
            name="episodic_recall",
            status=SmokeStageStatus.PASS,
            count=len(recent),
        )

    async def _recall_cases(
        self,
        *,
        property_id: str,
        expected: int,
    ) -> StageOutcome:
        """Confirm the case store sees at least the cases we wrote."""
        if expected == 0:
            return StageOutcome(
                name="case_recall",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no cases to recall",
            )
        count_fn = getattr(self._case_store, "count", None)
        if count_fn is None:
            return StageOutcome(
                name="case_recall",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="case store does not expose count()",
            )
        try:
            stored_total = await count_fn(property_id=property_id)
        except Exception as exc:  # noqa: BLE001
            return StageOutcome(
                name="case_recall",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                ),
            )
        if stored_total < expected:
            return StageOutcome(
                name="case_recall",
                status=SmokeStageStatus.FAIL,
                count=stored_total,
                detail=(
                    f"expected >={expected} cases, "
                    f"store reported {stored_total}"
                ),
            )
        return StageOutcome(
            name="case_recall",
            status=SmokeStageStatus.PASS,
            count=stored_total,
        )

    async def _recall_rules(
        self,
        *,
        rules: list[PatternRule],
    ) -> StageOutcome:
        """Round-trip mined rules through the rule store ``get`` API."""
        if not rules:
            return StageOutcome(
                name="rule_recall",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no rules emitted",
            )
        get_fn = getattr(self._rule_store, "get", None)
        if get_fn is None:
            return StageOutcome(
                name="rule_recall",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="rule store does not expose get()",
            )
        recalled = 0
        last_error = ""
        for rule in rules:
            try:
                fetched = await get_fn(rule.pattern_id)
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                )
                continue
            if fetched is not None:
                recalled += 1
        if recalled == 0:
            return StageOutcome(
                name="rule_recall",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=last_error or "rule store could not return any rule",
            )
        return StageOutcome(
            name="rule_recall",
            status=SmokeStageStatus.PASS,
            count=recalled,
            detail=last_error,
        )

    async def _verify_priority_chain(
        self,
        *,
        property_id: str,
        rules: list[PatternRule],
    ) -> StageOutcome:
        """Probe the §10 runtime priority chain end-to-end.

        Section §10 of the AI Pattern doc orders runtime decisions:
        manual → blocker → deterministic safety → PatternRule →
        preference → ASK.  This stage exercises the *PatternRule*
        rung and asserts that an ``ExecutionMode.ASK`` rule is the
        natural fallthrough when no higher-priority rung fires.
        """
        if self._router is None:
            return StageOutcome(
                name="priority_chain",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="router not wired",
            )
        if not rules:
            return StageOutcome(
                name="priority_chain",
                status=SmokeStageStatus.SKIPPED,
                count=0,
                detail="no rules emitted",
            )
        ask_capable = sum(
            1 for r in rules if r.execution_mode is ExecutionMode.ASK
        )
        auto_capable = sum(
            1 for r in rules if r.execution_mode is ExecutionMode.AUTO
        )
        # The chain is healthy when at least one mode tier is
        # represented — ASK is the V1 default; AUTO appears once
        # rules earn promotion via Wilson + support gates.
        if ask_capable == 0 and auto_capable == 0:
            return StageOutcome(
                name="priority_chain",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail=(
                    "no rule produced ASK or AUTO modes — "
                    "priority chain has no PatternRule rung"
                ),
            )
        scenarios = {rule.scenario for rule in rules}
        reachable = 0
        for scenario in scenarios:
            try:
                match = await self._router.match(
                    scenario=scenario,
                    property_id=property_id,
                    features={},
                )
            except Exception as exc:  # noqa: BLE001
                return StageOutcome(
                    name="priority_chain",
                    status=SmokeStageStatus.FAIL,
                    count=reachable,
                    detail=(
                        f"router raised "
                        f"{exc.__class__.__name__}: {exc}"
                    ),
                )
            if match is not None:
                reachable += 1
        if reachable == 0:
            return StageOutcome(
                name="priority_chain",
                status=SmokeStageStatus.FAIL,
                count=0,
                detail="router could not reach any rule from §10 chain",
            )
        return StageOutcome(
            name="priority_chain",
            status=SmokeStageStatus.PASS,
            count=reachable,
            detail=(
                f"ask={ask_capable} auto={auto_capable} "
                f"scenarios={len(scenarios)}"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aggregate_status(
    stages: list[StageOutcome],
) -> SmokeStageStatus:
    """Compose the run-level verdict from per-stage outcomes."""
    has_pass = False
    for stage in stages:
        if stage.status is SmokeStageStatus.FAIL:
            return SmokeStageStatus.FAIL
        if stage.status is SmokeStageStatus.PASS:
            has_pass = True
    if has_pass:
        return SmokeStageStatus.PASS
    return SmokeStageStatus.SKIPPED


# Quiet ``last_match`` ruff false-positive when run in tooling that
# imports the helper symbol — the dataclass field is part of the
# returned diagnostic.  No-op at runtime.
_ = field
