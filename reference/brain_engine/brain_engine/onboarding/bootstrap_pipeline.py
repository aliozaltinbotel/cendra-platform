"""Full cold-start bootstrap pipeline for a fresh property tenant.

:class:`OnboardingBootstrapPipeline` composes the four V2 building
blocks into a single async call:

1. :class:`ConversationArchiveLoader` — pulls the 6-month conversation
   archive from the onboarding-api unified GraphQL gateway.
2. :class:`EpisodeBuilder` — splits each reservation thread into
   Q&A episodes so a multi-exchange conversation becomes multiple
   :class:`DecisionCase` rows rather than just one.
3. :class:`HistoricalCaseExtractor` — turns every episode into at
   most one :class:`DecisionCase` and persists it.
4. :class:`PatternMiner` (optional) — scans the just-persisted cases,
   mines dominant-action :class:`PatternRule` rows and writes them.

The pipeline is intentionally separate from the simpler
:class:`OnboardingService`: the V1 service stays one-case-per-thread,
while V2 runs the full episode-aware + pattern-mining loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Protocol

import structlog

from brain_engine.onboarding.conversation_archive import (
    ConversationArchiveLoader,
)
from brain_engine.onboarding.episode_builder import (
    EpisodeBuilder,
    EpisodeStats,
)
from brain_engine.onboarding.errors import (
    ConversationArchiveError,
    HistoricalExtractionError,
)
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.fanout import (
    MemoryFanOut,
    MemoryFanOutProtocol,
    NullMemoryFanOut,
)
from brain_engine.memory.knowledge_graph import TemporalKnowledgeGraph
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.onboarding.event_bus import (
    BootstrapEventBus,
    EventKind,
    NullBootstrapEventBus,
    SkipReason,
    make_event,
)
from brain_engine.onboarding.historical_case_extractor import (
    ExtractionOutcome,
    HistoricalCaseExtractor,
)
from brain_engine.onboarding.models import (
    ArchivedConversation,
    MessageSender,
)
from brain_engine.patterns.models import (
    DecisionCase,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.extractor import (
    PatternExtractor,
    _merge_subsumed_rules,
)
from brain_engine.patterns.pattern_miner import (
    PatternMiner,
    PatternMiningReport,
    _resolve_pattern_rule_contradictions,
)
from brain_engine.patterns.validator import PatternValidator
from brain_engine.profiles.harvester import (
    HarvestCounts,
    PropertyProfileHarvester,
)
from brain_engine.sandbox.generator import ExampleReplyGenerator
from brain_engine.sandbox.models import UnansweredThread
from brain_engine.sandbox.review_heuristics import classify_review_need
from brain_engine.sandbox.store import UnansweredThreadStore

__all__ = [
    "BootstrapJobState",
    "BootstrapPropertyReport",
    "BootstrapReport",
    "BootstrapRequest",
    "OnboardingBootstrapPipeline",
]


logger = structlog.get_logger(__name__)


_MIN_DAYS: Final[int] = 1
# Mümin 2026-05-12 (PR #B): bumped from 730 (2 years) to 3650 (10
# years) so a deep cold-start can replay an entire portfolio's
# archive in one pass.  The HTTP layer mirrors this cap in its
# pydantic schemas so the wire validator and pipeline clamp stay in
# lock-step.
_MAX_DAYS: Final[int] = 3650
# Mümin 2026-05-12 (PR #B): raised from 5000 to 100_000 so the
# operator can request a property-wide archive replay without
# splitting the request.  GraphQL pagination already streams in
# bounded pages, so memory pressure stays flat — the cap only
# affects the size of the in-memory ``DecisionCase`` buffer the
# fast path accumulates.
_MAX_LIMIT_PER_PROPERTY: Final[int] = 100_000
_DEFAULT_MAX_CONCURRENCY: Final[int] = 4


# ── Realtime audit-log plumbing ────────────────────────────────────
#
# Mümin 2026-05-12: bootstrap was opaque — the operator saw a single
# ``cases_skipped: 535`` counter and had to read pod logs to figure
# out *which* threads and *why*.  The pipeline now exposes every
# decision through a :class:`BootstrapEventBus` keyed on a per-call
# ``job_id``.  The job id is stashed in a :class:`ContextVar` so the
# fan-out helpers (``_process_one_conversation``, ``_mine_and_store``,
# ``_extract_per_scenario_and_store``, ``_harvest_profile``) read it
# without us threading a parameter through every signature.
#
# The variable defaults to the empty string; helpers treat that as
# "no audit log requested" and short-circuit the emit.  Callers that
# do not opt in (legacy tests, the in-process ``onboarding/service``)
# observe zero behaviour change.
_CURRENT_JOB_ID: ContextVar[str] = ContextVar(
    "bootstrap_job_id", default="",
)


def _now() -> datetime:
    """Return the current UTC instant — extracted for test hooks."""
    return datetime.now(timezone.utc)


def _generate_job_id() -> str:
    """Return a stable hex id used as the bootstrap job key."""
    return uuid.uuid4().hex


def _loader_tenant_kwargs(
    *,
    customer_id_override: str | None,
    org_id_override: str | None,
    provider_type_override: str | None,
) -> dict[str, str]:
    """Build the override kwargs dict for the loader call site.

    Returns an empty dict when every override is ``None`` — that way
    the loader receives ZERO extra kwargs and the
    :class:`ConversationArchiveLoader` Protocol contract holds for
    every implementation (mocks, stubs, the in-memory PMS loader)
    without forcing them to advertise the new tenant slots.

    Only the GraphQL adapter consumes these slots today.  Future
    Protocol versions can hoist the contract once every active
    loader supports it.
    """
    kwargs: dict[str, str] = {}
    if customer_id_override is not None:
        kwargs["customer_id_override"] = customer_id_override
    if org_id_override is not None:
        kwargs["org_id_override"] = org_id_override
    if provider_type_override is not None:
        kwargs["provider_type_override"] = provider_type_override
    return kwargs

# ── Fast cold-start defaults ───────────────────────────────────────
# ``bootstrap_fast`` is the cold-start path used by V1 onboarding
# when the operator must see a property as ready_for_live within
# seconds rather than minutes.  Three knobs co-operate:
#   * a tighter look-back window — 30 days instead of the legacy
#     180-day default — shrinks the input set 4–6x;
#   * the per-conversation pipeline (episode split, case extraction,
#     sandbox drafts) fans out via an :class:`asyncio.Semaphore`;
#   * pattern mining defers to a fire-and-forget background task so
#     the HTTP response is not held back by the heaviest LLM step.
# The concurrency cap is intentionally below the per-property
# ``_DEFAULT_MAX_CONCURRENCY`` × N pattern: every concurrent worker
# can fan out into Azure OpenAI calls during case extraction.
# Eight is a safe ceiling on the dev tenant's TPM budget; raise
# via the request body when Azure quotas allow.
_DEFAULT_DAYS_FAST: Final[int] = 30
_DEFAULT_INNER_CONCURRENCY: Final[int] = 8
_FAST_LOADER_LIMIT: Final[int] = 10_000


class _CaseStoreLike(Protocol):
    """Narrow ``DecisionCaseStore`` surface used by the pipeline."""

    async def store(self, case: DecisionCase) -> str:
        ...


class _RuleStoreLike(Protocol):
    """Narrow ``PatternRuleStore`` surface used by the pipeline."""

    async def store(self, rule: PatternRule) -> str:
        ...

    async def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        ...


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    """Input to :meth:`OnboardingBootstrapPipeline.bootstrap`.

    Attributes:
        property_ids: Properties to bootstrap.  Order is preserved in
            the resulting per-property reports.
        days: Size of the look-back window in days.  Clamped into
            ``[1, 730]``.
        limit_per_property: Cap on conversations pulled per property
            to keep a large portfolio bootable in a single pass.
        dry_run: Run the full pipeline but skip persistence so an
            operator can preview volume before committing.
        mine_patterns: Run the :class:`PatternMiner` after case
            extraction.  Requires a configured rule store on the
            pipeline; otherwise this flag is ignored.
    """

    property_ids: tuple[str, ...]
    # ``None`` means "no time window / no per-property cap" — the
    # pipeline resolves both to the system ceilings
    # (:data:`_MAX_DAYS`, :data:`_MAX_LIMIT_PER_PROPERTY`) so the
    # batch path ingests every property's full archive by default.
    days: int | None = None
    limit_per_property: int | None = None
    dry_run: bool = False
    mine_patterns: bool = True


@dataclass(frozen=True, slots=True)
class BootstrapPropertyReport:
    """Per-property outcome of a bootstrap run."""

    property_id: str
    conversations_loaded: int = 0
    episodes_emitted: int = 0
    cases_extracted: int = 0
    cases_skipped: int = 0
    rules_emitted: int = 0
    error: str = ""
    episode_stats: EpisodeStats = field(default_factory=EpisodeStats)
    mining_report: PatternMiningReport = field(
        default_factory=PatternMiningReport,
    )
    profile_built: bool = False
    unanswered_thread_count: int = 0
    rate_plans_seen: int = 0
    reviews_seen: int = 0
    conversations_with_dates: int = 0
    stage_distribution: dict[str, int] = field(default_factory=dict)
    # Mümin 2026-05-12 (PR #B): True when the loader stopped before
    # exhausting the source archive because the caller's ``limit``
    # was reached.  The audit log also carries a ``LOADER_TRUNCATED``
    # event so a UI can surface the "missing tail" without polling
    # the report.
    loader_truncated: bool = False
    # Effective cap the loader ran against — stored alongside the
    # truncation flag so an operator can re-run with a larger cap
    # without reading server logs.
    loader_limit: int = 0

    def as_dict(self) -> dict[str, object]:
        """JSON-friendly per-property view used by the HTTP layer.

        Mirrors the per-report payload embedded in
        :meth:`BootstrapReport.as_dict` so single-property responses
        share the wire shape with the multi-property job report.
        """
        return {
            "property_id": self.property_id,
            "conversations_loaded": self.conversations_loaded,
            "conversations_with_dates": self.conversations_with_dates,
            "episodes_emitted": self.episodes_emitted,
            "cases_extracted": self.cases_extracted,
            "cases_skipped": self.cases_skipped,
            "rules_emitted": self.rules_emitted,
            "profile_built": self.profile_built,
            "unanswered_thread_count": self.unanswered_thread_count,
            "rate_plans_seen": self.rate_plans_seen,
            "reviews_seen": self.reviews_seen,
            "stage_distribution": dict(self.stage_distribution),
            "loader_truncated": self.loader_truncated,
            "loader_limit": self.loader_limit,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Aggregate report returned by :meth:`bootstrap`."""

    property_reports: tuple[BootstrapPropertyReport, ...]
    total_conversations: int
    total_episodes: int
    total_cases: int
    total_skipped: int
    total_rules: int
    duration_seconds: float
    dry_run: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        """JSON-friendly serialisation for the HTTP layer."""
        return {
            "total_conversations": self.total_conversations,
            "total_episodes": self.total_episodes,
            "total_cases": self.total_cases,
            "total_skipped": self.total_skipped,
            "total_rules": self.total_rules,
            "duration_seconds": self.duration_seconds,
            "dry_run": self.dry_run,
            "errors": list(self.errors),
            "property_reports": [
                report.as_dict() for report in self.property_reports
            ],
        }


@dataclass(slots=True)
class _ConversationOutcome:
    """Outcome of processing one conversation in the fast inner loop.

    The fast bootstrap path runs N conversation workers concurrently
    via :func:`asyncio.gather` and folds their outcomes after the
    fan-out completes.  Every counter here is fold-friendly
    (additive); ``last_ended_at`` is reduced via a max combinator at
    the call site.

    A worker isolates its own failures into ``error`` rather than
    raising — a single bad thread must not poison the rest of the
    cold-start run.
    """

    episodes_emitted: int = 0
    cases_skipped: int = 0
    unanswered: bool = False
    extracted_cases: tuple[DecisionCase, ...] = ()
    stats: EpisodeStats = field(default_factory=EpisodeStats)
    last_ended_at: datetime | None = None
    error: str = ""


@dataclass(slots=True)
class _FanOutResult:
    """Aggregate of all per-conversation outcomes after fan-out."""

    conversations_loaded: int
    episodes_emitted: int
    cases_skipped: int
    unanswered: int
    cases: list[DecisionCase]
    stats: EpisodeStats
    last_ended_at: datetime | None


@dataclass(slots=True)
class BootstrapJobState:
    """State of one background bootstrap job.

    The state carries *either* a multi-property
    :class:`BootstrapReport` (V2 batch path) *or* a single-property
    :class:`BootstrapPropertyReport` (V1 PR #C async-single path) —
    never both.  ``as_dict`` exposes whichever is populated under
    the same ``report`` key so the wire shape stays consistent
    across the two job kinds.
    """

    job_id: str
    status: str = "pending"
    submitted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report: BootstrapReport | None = None
    property_report: BootstrapPropertyReport | None = None
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        """Wire form suitable for the status endpoint."""
        data: dict[str, object] = {
            "job_id": self.job_id,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat(),
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "error": self.error,
        }
        if self.report is not None:
            data["report"] = self.report.as_dict()
        elif self.property_report is not None:
            data["report"] = self.property_report.as_dict()
        else:
            data["report"] = None
        return data


class OnboardingBootstrapPipeline:
    """Compose loader + episodes + cases + patterns into one call.

    Args:
        archive_loader: Any :class:`ConversationArchiveLoader`
            (canonical implementation:
            :class:`GraphQLConversationArchiveLoader`).
        episode_builder: Splits each conversation into Q&A episodes.
        case_extractor: Turns one episode into at most one
            :class:`DecisionCase`.
        case_store: Persists extracted cases.  Only used when
            ``dry_run`` is ``False``.
        pattern_miner: Optional miner.  When supplied together with a
            rule store, each property's cases are mined into
            :class:`PatternRule` rows at the end of the run.
        rule_store: Persistence for mined rules.  Required when
            ``pattern_miner`` is set; ignored otherwise.
        pattern_validator: Validator that gates mined rules before
            persistence.  Mirrors the gate used by the
            ``/patterns/extract`` endpoint and by the nightly
            consolidator so blacklisted scenarios
            (``NEVER_AUTO_SCENARIOS``) and other unsafe rules never
            reach the rule store.  Defaults to a stock
            :class:`PatternValidator` when omitted.
        max_concurrency: Per-property fan-out cap.  Defaults to 4.
    """

    def __init__(
        self,
        *,
        archive_loader: ConversationArchiveLoader,
        episode_builder: EpisodeBuilder,
        case_extractor: HistoricalCaseExtractor,
        case_store: _CaseStoreLike,
        pattern_miner: PatternMiner | None = None,
        pattern_extractor: PatternExtractor | None = None,
        rule_store: _RuleStoreLike | None = None,
        pattern_validator: PatternValidator | None = None,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        profile_harvester: PropertyProfileHarvester | None = None,
        profile_customer_id: str = "",
        profile_org_id: str = "",
        profile_provider_type: str = "",
        sandbox_generator: ExampleReplyGenerator | None = None,
        sandbox_store: UnansweredThreadStore | None = None,
        event_bus: BootstrapEventBus | None = None,
        episodic_memory: EpisodicMemory | None = None,
        semantic_memory: SemanticMemory | None = None,
        knowledge_graph: TemporalKnowledgeGraph | None = None,
        memory_fanout: MemoryFanOutProtocol | None = None,
    ) -> None:
        if pattern_miner is not None and rule_store is None:
            raise ValueError(
                "rule_store is required when pattern_miner is set",
            )
        if profile_harvester is not None and not profile_customer_id:
            raise ValueError(
                "profile_customer_id is required when profile_harvester is set",
            )
        if sandbox_generator is not None and sandbox_store is None:
            raise ValueError(
                "sandbox_store is required when sandbox_generator is set",
            )
        self._loader = archive_loader
        self._episodes = episode_builder
        self._extractor = case_extractor
        self._case_store = case_store
        self._miner = pattern_miner
        # Mümin round-4 follow-up (2026-05-11): the legacy bootstrap
        # path used :class:`PatternMiner` alone, which synthesises
        # weak conditional rules (support = 2–3) the validator then
        # rejects (``MIN_SUPPORT_AUTO = 5``).  ``PatternExtractor``
        # produces stronger whole-group rules (support = 9+ on the
        # 323133 archive) that pass the same validator.  We run both
        # — the miner stays as the candidate-synthesis layer, the
        # extractor catches whole-group patterns the miner does not.
        self._pattern_extractor = pattern_extractor
        self._rule_store = rule_store
        self._validator = pattern_validator or PatternValidator()
        self._max_concurrency = max(1, int(max_concurrency))
        self._profile_harvester = profile_harvester
        self._profile_customer_id = profile_customer_id
        self._profile_org_id = profile_org_id
        self._profile_provider_type = profile_provider_type
        self._sandbox_generator = sandbox_generator
        self._sandbox_store = sandbox_store
        self._bus: BootstrapEventBus = (
            event_bus if event_bus is not None else NullBootstrapEventBus()
        )
        # Mümin 2026-05-13 (PR #E): memory is the platform's
        # killer feature.  When an EpisodicMemory is wired the
        # bootstrap path records one episode per persisted
        # DecisionCase so ``/memory/timeline`` carries the
        # historical archive alongside the live event stream.  No
        # episodic = silent no-op; the rest of the pipeline is
        # unaffected.
        # Mümin 2026-05-13 (PR #F): memory fan-out is shared
        # across every write path (bootstrap + live conversation
        # + regenerate + nightly consolidator).  Prefer an
        # explicit ``memory_fanout`` argument; fall back to
        # building one from the legacy per-tier arguments so
        # existing wiring keeps working.  Absence of every
        # backend collapses to :class:`NullMemoryFanOut`.
        if memory_fanout is not None:
            self._memory_fanout: MemoryFanOutProtocol = memory_fanout
        elif (
            episodic_memory is not None
            or semantic_memory is not None
            or knowledge_graph is not None
        ):
            self._memory_fanout = MemoryFanOut(
                episodic=episodic_memory,
                semantic=semantic_memory,
                knowledge_graph=knowledge_graph,
            )
        else:
            self._memory_fanout = NullMemoryFanOut()
        self._log = logger.bind(component="bootstrap_pipeline")

    async def _record_episode(
        self,
        *,
        property_id: str,  # noqa: ARG002 - kept for call-site clarity
        case: DecisionCase,
    ) -> None:
        """Fan one persisted case out to every wired memory tier.

        Delegates to the shared :class:`MemoryFanOut` so the
        bootstrap path and the live conversation path emit
        identical timeline / semantic / KG entries.
        """
        await self._memory_fanout.record_case(case, source="bootstrap")

    @property
    def event_bus(self) -> BootstrapEventBus:
        """The audit-log transport the pipeline emits into.

        Exposed so the HTTP layer can fetch summaries / logs / SSE
        streams for a job without holding a second reference.
        """
        return self._bus

    async def _emit(
        self,
        *,
        property_id: str,
        kind: EventKind,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Best-effort emit; reads the active job id from the contextvar.

        Returns immediately when no job id is active (legacy callers
        that do not opt into the audit log) so the pipeline stays
        bit-for-bit equivalent to the pre-bus path.
        """
        job_id = _CURRENT_JOB_ID.get()
        if not job_id:
            return
        try:
            await self._bus.emit(
                make_event(
                    job_id=job_id,
                    property_id=property_id,
                    kind=kind,
                    payload=payload,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - audit log is best-effort
            self._log.exception(
                "bootstrap.event_emit_failed",
                job_id=job_id,
                property_id=property_id,
                kind=kind.value,
            )

    async def bootstrap_one(
        self,
        property_id: str,
        *,
        days: int | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        mine_patterns: bool = True,
        job_id: str | None = None,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> BootstrapPropertyReport:
        """Bootstrap exactly one property and return its report.

        V1 onboarding (Mümin's step 4) lets the PM pick a single
        property from the listing screen and only ingest that one.
        This convenience method reuses the per-property loop driven by
        :meth:`bootstrap` so the V1 single-pick path and the V2 batch
        path share semantics, error handling, and side effects.

        Args:
            property_id: Property to bootstrap.  Must be non-empty.
            days: Look-back window in days.  ``None`` means "ingest the
                entire archive" — the helper resolves it to
                :data:`_MAX_DAYS` (10 years) which is the practical
                upper bound the GraphQL adapter honours.  Explicit
                numeric values are clamped into ``[1, _MAX_DAYS]`` via
                :meth:`_window`.
            limit: Cap on conversations pulled for this property.
                ``None`` means "no cap" and resolves to
                :data:`_MAX_LIMIT_PER_PROPERTY` (100 000).  Explicit
                values are clamped into ``[1, _MAX_LIMIT_PER_PROPERTY]``
                via :func:`_clamp_limit`.
            dry_run: Run the pipeline without persisting cases, rules,
                sandbox examples, or profile snapshots.
            mine_patterns: Run the pattern miner over the extracted
                cases.  Honored only when a miner was injected at
                construction time.

        Returns:
            The :class:`BootstrapPropertyReport` produced by the
            shared per-property loop.

        Raises:
            ValueError: When ``property_id`` is empty or whitespace.
        """
        if not property_id or not property_id.strip():
            raise ValueError("property_id is required")
        effective_days = _resolve_days(days)
        effective_limit = _clamp_limit(
            limit if limit is not None else _MAX_LIMIT_PER_PROPERTY,
        )
        now = _now()
        since, until = self._window(days=effective_days, now=now)
        mine = mine_patterns and self._miner is not None
        resolved_job_id = job_id or _generate_job_id()
        token = _CURRENT_JOB_ID.set(resolved_job_id)
        try:
            await self._emit(
                property_id=property_id,
                kind=EventKind.JOB_STARTED,
                payload={
                    "mode": "bootstrap_one",
                    "days": effective_days,
                    "limit": effective_limit,
                    "dry_run": dry_run,
                    "mine_patterns": bool(mine),
                },
            )
            try:
                report = await self._bootstrap_property(
                    property_id=property_id,
                    since=since,
                    until=until,
                    limit=effective_limit,
                    dry_run=dry_run,
                    mine_patterns=mine,
                    customer_id_override=customer_id_override,
                    org_id_override=org_id_override,
                    provider_type_override=provider_type_override,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - re-raise after emit
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.JOB_FAILED,
                    payload={"error": str(exc) or exc.__class__.__name__},
                )
                raise
            await self._emit(
                property_id=property_id,
                kind=EventKind.JOB_DONE,
                payload={
                    "conversations_loaded": report.conversations_loaded,
                    "cases_extracted": report.cases_extracted,
                    "cases_skipped": report.cases_skipped,
                    "rules_emitted": report.rules_emitted,
                    "error": report.error,
                },
            )
            return report
        finally:
            _CURRENT_JOB_ID.reset(token)

    async def bootstrap_fast(
        self,
        property_id: str,
        *,
        days: int | None = None,
        inner_concurrency: int = _DEFAULT_INNER_CONCURRENCY,
        mine_patterns_inline: bool = False,
        dry_run: bool = False,
        job_id: str | None = None,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> BootstrapPropertyReport:
        """Pull and persist a property's last ``days`` of data in parallel.

        The cold-start path used by V1 onboarding when the operator
        must see a freshly-picked property as ``ready_for_live``
        within seconds.  Three accelerators stack on top of the
        legacy :meth:`bootstrap_one` semantics:

        * a tighter default look-back window (30 days) shrinks the
          input set 4–6x compared to the 180-day legacy default;
        * the per-conversation work (episode split, case extraction,
          sandbox drafts) fans out concurrently via
          :func:`asyncio.gather` with an :class:`asyncio.Semaphore`
          cap so Azure OpenAI rate limits stay respected;
        * profile harvesting (rate plans + reviews) runs in parallel
          with the conversation pipeline rather than strictly after
          it;
        * pattern mining defaults to ``mine_patterns_inline=False``
          and is scheduled as a fire-and-forget background task so
          the request does not wait on the heaviest LLM step.

        Args:
            property_id: Brain Engine property identifier.  Required;
                empty / whitespace-only values raise ``ValueError``.
            days: Look-back window in days; clamped into ``[1, 730]``.
                Defaults to 30 for cold-start; pass a larger value
                when reseeding the historical record.
            inner_concurrency: Per-property fan-out cap for the
                conversation worker pool.  Clamped to ``>= 1``.
                Default ``8`` keeps the case extractor's Azure-backed
                embedder inside the dev tenant's TPM budget; raise
                this only when the deployment has measured headroom.
            mine_patterns_inline: When ``True``, the pattern miner
                runs inside the request and the response carries
                ``rules_emitted``.  Default ``False`` so cold-start
                stays in seconds; mining continues in the background.
            dry_run: Run the pipeline without persisting cases,
                profile snapshots, or sandbox drafts.

        Returns:
            :class:`BootstrapPropertyReport` summarising what landed
            synchronously.  ``rules_emitted`` is zero unless mining
            was forced inline; the background mining task updates the
            rule store on its own schedule and is not reflected here.

        Raises:
            ValueError: When ``property_id`` is empty or whitespace.
        """
        if not property_id or not property_id.strip():
            raise ValueError("property_id is required")
        inner_cap = max(1, int(inner_concurrency))
        # ``None`` ⇒ entire archive; fast path falls back to its
        # historical 30-day cold-start default only when the caller
        # *explicitly* asks for it via the wire (still supported), but
        # for omitted values the operator gets the full window.
        effective_days = _resolve_days(days)
        now = _now()
        since, until = self._window(days=effective_days, now=now)
        resolved_job_id = job_id or _generate_job_id()
        token = _CURRENT_JOB_ID.set(resolved_job_id)
        try:
            await self._emit(
                property_id=property_id,
                kind=EventKind.JOB_STARTED,
                payload={
                    "mode": "bootstrap_fast",
                    "days": effective_days,
                    "inner_concurrency": inner_cap,
                    "mine_patterns_inline": bool(mine_patterns_inline),
                    "dry_run": dry_run,
                },
            )
            try:
                report = await self._bootstrap_property_fast(
                    property_id=property_id,
                    since=since,
                    until=until,
                    inner_concurrency=inner_cap,
                    dry_run=dry_run,
                    mine_patterns_inline=mine_patterns_inline,
                    customer_id_override=customer_id_override,
                    org_id_override=org_id_override,
                    provider_type_override=provider_type_override,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - re-raise after emit
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.JOB_FAILED,
                    payload={"error": str(exc) or exc.__class__.__name__},
                )
                raise
            await self._emit(
                property_id=property_id,
                kind=EventKind.JOB_DONE,
                payload={
                    "conversations_loaded": report.conversations_loaded,
                    "cases_extracted": report.cases_extracted,
                    "cases_skipped": report.cases_skipped,
                    "rules_emitted": report.rules_emitted,
                    "error": report.error,
                },
            )
            return report
        finally:
            _CURRENT_JOB_ID.reset(token)

    async def bootstrap(
        self,
        request: BootstrapRequest,
        *,
        job_id: str | None = None,
    ) -> BootstrapReport:
        """Run the full pipeline and return an aggregate report."""
        started = time.monotonic()
        effective_days = _resolve_days(request.days)
        effective_limit = _clamp_limit(
            request.limit_per_property
            if request.limit_per_property is not None
            else _MAX_LIMIT_PER_PROPERTY,
        )
        now = _now()
        since, until = self._window(days=effective_days, now=now)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        mine_patterns = request.mine_patterns and self._miner is not None
        resolved_job_id = job_id or _generate_job_id()
        token = _CURRENT_JOB_ID.set(resolved_job_id)
        try:
            for pid in request.property_ids:
                await self._emit(
                    property_id=pid,
                    kind=EventKind.JOB_STARTED,
                    payload={
                        "mode": "bootstrap",
                        "days": effective_days,
                        "limit_per_property": effective_limit,
                        "dry_run": request.dry_run,
                        "mine_patterns": bool(mine_patterns),
                    },
                )

            async def _run(property_id: str) -> BootstrapPropertyReport:
                async with semaphore:
                    return await self._bootstrap_property(
                        property_id=property_id,
                        since=since,
                        until=until,
                        limit=effective_limit,
                        dry_run=request.dry_run,
                        mine_patterns=mine_patterns,
                    )

            reports = await asyncio.gather(
                *(_run(pid) for pid in request.property_ids)
            )
            for report in reports:
                kind = (
                    EventKind.JOB_FAILED
                    if report.error
                    else EventKind.JOB_DONE
                )
                payload: dict[str, Any] = {
                    "conversations_loaded": report.conversations_loaded,
                    "cases_extracted": report.cases_extracted,
                    "cases_skipped": report.cases_skipped,
                    "rules_emitted": report.rules_emitted,
                }
                if report.error:
                    payload["error"] = report.error
                await self._emit(
                    property_id=report.property_id,
                    kind=kind,
                    payload=payload,
                )
        finally:
            _CURRENT_JOB_ID.reset(token)

        errors = tuple(
            f"{r.property_id}: {r.error}" for r in reports if r.error
        )
        return BootstrapReport(
            property_reports=tuple(reports),
            total_conversations=sum(r.conversations_loaded for r in reports),
            total_episodes=sum(r.episodes_emitted for r in reports),
            total_cases=sum(r.cases_extracted for r in reports),
            total_skipped=sum(r.cases_skipped for r in reports),
            total_rules=sum(r.rules_emitted for r in reports),
            duration_seconds=round(time.monotonic() - started, 3),
            dry_run=request.dry_run,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Per-property loop
    # ------------------------------------------------------------------

    async def _bootstrap_property(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int,
        dry_run: bool,
        mine_patterns: bool,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> BootstrapPropertyReport:
        conversations_loaded = 0
        conversations_with_dates = 0
        episodes_emitted = 0
        cases_skipped = 0
        unanswered_threads = 0
        last_conversation_at: datetime | None = None
        extracted_cases: list[DecisionCase] = []
        aggregate_stats = EpisodeStats()
        if (
            not dry_run
            and self._sandbox_generator is not None
            and self._sandbox_store is not None
        ):
            try:
                await self._sandbox_store.clear_property(property_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                self._log.exception(
                    "bootstrap.sandbox_clear_failed",
                    property_id=property_id,
                )
        try:
            iterator = self._loader.load(
                property_id=property_id,
                since=since,
                until=until,
                limit=limit,
                **_loader_tenant_kwargs(
                    customer_id_override=customer_id_override,
                    org_id_override=org_id_override,
                    provider_type_override=provider_type_override,
                ),
            )
            async for conversation in iterator:
                conversations_loaded += 1
                has_dates = (
                    conversation.arrival_date is not None
                    or conversation.departure_date is not None
                )
                if has_dates:
                    conversations_with_dates += 1
                if _is_unanswered(conversation):
                    unanswered_threads += 1
                    if not dry_run:
                        await self._emit_sandbox_reply(
                            property_id=property_id,
                            conversation=conversation,
                        )
                last_conversation_at = _max_ended_at(
                    last_conversation_at,
                    conversation,
                )
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.CONVERSATION_LOADED,
                    payload={
                        "conversation_id": conversation.conversation_id,
                        "message_count": len(conversation.messages),
                        "has_dates": has_dates,
                    },
                )
                episodes, stats = self._episodes.split(conversation)
                aggregate_stats = _merge_stats(aggregate_stats, stats)
                if not episodes:
                    cases_skipped += 1
                    skip_reason = _diagnose_empty_thread_reason(conversation)
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.CONVERSATION_SKIPPED,
                        payload={
                            "conversation_id": conversation.conversation_id,
                            "reason": skip_reason.value,
                            "guest_messages": sum(
                                1
                                for m in conversation.messages
                                if m.sender is MessageSender.GUEST
                            ),
                            "pm_messages": sum(
                                1
                                for m in conversation.messages
                                if m.sender is MessageSender.PM
                            ),
                            "total_messages": len(conversation.messages),
                        },
                    )
                    continue
                episodes_emitted += len(episodes)
                for episode in episodes:
                    outcome = await self._extract_one_with_reason(
                        property_id=property_id,
                        episode=episode,
                    )
                    if outcome.case is None:
                        cases_skipped += 1
                        await self._emit(
                            property_id=property_id,
                            kind=EventKind.CASE_SKIPPED,
                            payload={
                                "conversation_id": (
                                    conversation.conversation_id
                                ),
                                "reason": (
                                    outcome.skip_reason.value
                                    if outcome.skip_reason is not None
                                    else SkipReason.OTHER.value
                                ),
                            },
                        )
                        continue
                    case = outcome.case
                    if not dry_run:
                        await self._case_store.store(case)
                        await self._record_episode(
                            property_id=property_id, case=case,
                        )
                    extracted_cases.append(case)
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.CASE_EXTRACTED,
                        payload={
                            "conversation_id": conversation.conversation_id,
                            "scenario": case.scenario.value,
                            "decision_type": case.decision.action_type.value,
                            "stage": case.stage.value,
                            "is_learnable": case.is_learnable,
                        },
                    )
        except ConversationArchiveError as exc:
            self._log.error(
                "bootstrap.archive_failed",
                property_id=property_id,
                loader=exc.loader,
                reason=str(exc),
            )
            return BootstrapPropertyReport(
                property_id=property_id,
                conversations_loaded=conversations_loaded,
                episodes_emitted=episodes_emitted,
                cases_extracted=len(extracted_cases),
                cases_skipped=cases_skipped,
                rules_emitted=0,
                error=str(exc),
                episode_stats=aggregate_stats,
                unanswered_thread_count=unanswered_threads,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained per property
            self._log.exception(
                "bootstrap.unexpected_failure",
                property_id=property_id,
            )
            return BootstrapPropertyReport(
                property_id=property_id,
                conversations_loaded=conversations_loaded,
                episodes_emitted=episodes_emitted,
                cases_extracted=len(extracted_cases),
                cases_skipped=cases_skipped,
                rules_emitted=0,
                error=str(exc) or exc.__class__.__name__,
                episode_stats=aggregate_stats,
                unanswered_thread_count=unanswered_threads,
            )

        rules_emitted = 0
        mining_report = PatternMiningReport()
        if mine_patterns and extracted_cases:
            rules_emitted, mining_report = await self._mine_and_store(
                property_id=property_id,
                cases=extracted_cases,
                dry_run=dry_run,
            )
            # Mümin round-4 follow-up: also run the API-grade
            # extractor so whole-group rules the miner under-mines
            # (support 9+ on access_code_release in the 323133
            # archive) land in the rule store.  No-op when the
            # extractor isn't wired or rule_store is missing.
            extractor_emitted = (
                await self._extract_per_scenario_and_store(
                    property_id=property_id,
                    owner_id=(
                        extracted_cases[0].owner_id
                        if extracted_cases else ""
                    ),
                    cases=extracted_cases,
                    dry_run=dry_run,
                )
            )
            rules_emitted += extractor_emitted

        profile_built = False
        rate_plans_seen = 0
        reviews_seen = 0
        if self._profile_harvester is not None and not dry_run:
            (
                profile_built,
                rate_plans_seen,
                reviews_seen,
            ) = await self._harvest_profile(
                property_id=property_id,
                conversations_loaded=conversations_loaded,
                unanswered_threads=unanswered_threads,
                last_conversation_at=last_conversation_at,
            )
        stage_distribution = _stage_distribution(extracted_cases)
        loader_truncated = conversations_loaded >= limit
        if loader_truncated:
            await self._emit(
                property_id=property_id,
                kind=EventKind.LOADER_TRUNCATED,
                payload={
                    "limit": int(limit),
                    "conversations_loaded": int(conversations_loaded),
                    "hint": (
                        "Loader hit the caller-supplied limit; "
                        "request a higher value to ingest the tail."
                    ),
                },
            )
        return BootstrapPropertyReport(
            property_id=property_id,
            conversations_loaded=conversations_loaded,
            conversations_with_dates=conversations_with_dates,
            episodes_emitted=episodes_emitted,
            cases_extracted=len(extracted_cases),
            cases_skipped=cases_skipped,
            rules_emitted=rules_emitted,
            episode_stats=aggregate_stats,
            mining_report=mining_report,
            profile_built=profile_built,
            unanswered_thread_count=unanswered_threads,
            rate_plans_seen=rate_plans_seen,
            reviews_seen=reviews_seen,
            stage_distribution=stage_distribution,
            loader_truncated=loader_truncated,
            loader_limit=int(limit),
        )

    # ------------------------------------------------------------------
    # Fast cold-start path
    # ------------------------------------------------------------------

    async def _bootstrap_property_fast(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        inner_concurrency: int,
        dry_run: bool,
        mine_patterns_inline: bool,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> BootstrapPropertyReport:
        """Parallel inner-loop variant of :meth:`_bootstrap_property`.

        The legacy :meth:`_bootstrap_property` walks the loader async
        iterator one conversation at a time and runs harvesting only
        after the conversation pass completes.  For cold-start that
        produces a serial chain of LLM round-trips that easily takes
        minutes.  This path drains the loader, then fans out the
        per-conversation work and the profile harvest concurrently,
        and finally either runs pattern mining inline or schedules it
        as a background task so the response returns in seconds.

        Per-conversation isolation is preserved:
        :meth:`_process_one_conversation` traps its own failures and
        returns a populated :class:`_ConversationOutcome` so a single
        bad thread never poisons the rest of the cold-start.
        """
        if (
            not dry_run
            and self._sandbox_generator is not None
            and self._sandbox_store is not None
        ):
            try:
                await self._sandbox_store.clear_property(property_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                self._log.exception(
                    "bootstrap.sandbox_clear_failed",
                    property_id=property_id,
                )

        try:
            conversations = await self._drain_loader(
                property_id=property_id,
                since=since,
                until=until,
                limit=_FAST_LOADER_LIMIT,
                customer_id_override=customer_id_override,
                org_id_override=org_id_override,
                provider_type_override=provider_type_override,
            )
        except ConversationArchiveError as exc:
            self._log.error(
                "bootstrap.archive_failed",
                property_id=property_id,
                loader=exc.loader,
                reason=str(exc),
            )
            return BootstrapPropertyReport(
                property_id=property_id,
                error=str(exc),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained per property
            self._log.exception(
                "bootstrap.fast_drain_failed",
                property_id=property_id,
            )
            return BootstrapPropertyReport(
                property_id=property_id,
                error=str(exc) or exc.__class__.__name__,
            )

        unanswered_total = sum(
            1 for c in conversations if _is_unanswered(c)
        )
        last_conversation_at = max(
            (c.ended_at for c in conversations),
            default=None,
        )

        async def _conv_stage() -> _FanOutResult:
            return await self._fan_out_conversations(
                property_id=property_id,
                conversations=conversations,
                dry_run=dry_run,
                inner_concurrency=inner_concurrency,
            )

        async def _harvest_stage() -> tuple[bool, int, int]:
            if self._profile_harvester is None or dry_run:
                return False, 0, 0
            try:
                return await self._harvest_profile(
                    property_id=property_id,
                    conversations_loaded=len(conversations),
                    unanswered_threads=unanswered_total,
                    last_conversation_at=last_conversation_at,
                )
            except Exception:  # noqa: BLE001 - profile is best-effort
                self._log.exception(
                    "bootstrap.profile_harvest_failed",
                    property_id=property_id,
                )
                return False, 0, 0

        fan_result, harvest_result = await asyncio.gather(
            _conv_stage(),
            _harvest_stage(),
        )
        profile_built, rate_plans_seen, reviews_seen = harvest_result

        rules_emitted = 0
        mining_report = PatternMiningReport()
        if fan_result.cases and self._miner is not None:
            if mine_patterns_inline:
                rules_emitted, mining_report = await self._mine_and_store(
                    property_id=property_id,
                    cases=fan_result.cases,
                    dry_run=dry_run,
                )
            else:
                # Fire-and-forget: mining continues after the request
                # returns.  The rule store update will surface in the
                # next bootstrap report or in the recall path.
                asyncio.create_task(
                    self._background_mine(
                        property_id=property_id,
                        cases=list(fan_result.cases),
                        dry_run=dry_run,
                    ),
                    name=f"bootstrap-fast-mine-{property_id}",
                )

        conversations_with_dates = sum(
            1
            for c in conversations
            if c.arrival_date is not None or c.departure_date is not None
        )
        stage_distribution = _stage_distribution(fan_result.cases)
        loader_truncated = (
            fan_result.conversations_loaded >= _FAST_LOADER_LIMIT
        )
        if loader_truncated:
            await self._emit(
                property_id=property_id,
                kind=EventKind.LOADER_TRUNCATED,
                payload={
                    "limit": int(_FAST_LOADER_LIMIT),
                    "conversations_loaded": int(
                        fan_result.conversations_loaded,
                    ),
                    "hint": (
                        "bootstrap_fast hit its 10k internal cap; "
                        "fall back to bootstrap_one for the tail."
                    ),
                },
            )
        return BootstrapPropertyReport(
            property_id=property_id,
            conversations_loaded=fan_result.conversations_loaded,
            conversations_with_dates=conversations_with_dates,
            episodes_emitted=fan_result.episodes_emitted,
            cases_extracted=len(fan_result.cases),
            cases_skipped=fan_result.cases_skipped,
            rules_emitted=rules_emitted,
            episode_stats=fan_result.stats,
            mining_report=mining_report,
            profile_built=profile_built,
            unanswered_thread_count=fan_result.unanswered,
            rate_plans_seen=rate_plans_seen,
            reviews_seen=reviews_seen,
            stage_distribution=stage_distribution,
            loader_truncated=loader_truncated,
            loader_limit=int(_FAST_LOADER_LIMIT),
        )

    async def _drain_loader(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> list[ArchivedConversation]:
        """Drain the archive loader async iterator into a list."""
        iterator = self._loader.load(
            property_id=property_id,
            since=since,
            until=until,
            limit=limit,
            **_loader_tenant_kwargs(
                customer_id_override=customer_id_override,
                org_id_override=org_id_override,
                provider_type_override=provider_type_override,
            ),
        )
        drained: list[ArchivedConversation] = []
        async for conversation in iterator:
            drained.append(conversation)
        return drained

    async def _fan_out_conversations(
        self,
        *,
        property_id: str,
        conversations: list[ArchivedConversation],
        dry_run: bool,
        inner_concurrency: int,
    ) -> _FanOutResult:
        """Process conversations concurrently with bounded fan-out."""
        if not conversations:
            return _FanOutResult(
                conversations_loaded=0,
                episodes_emitted=0,
                cases_skipped=0,
                unanswered=0,
                cases=[],
                stats=EpisodeStats(),
                last_ended_at=None,
            )

        semaphore = asyncio.Semaphore(inner_concurrency)

        async def _worker(
            conversation: ArchivedConversation,
        ) -> _ConversationOutcome:
            async with semaphore:
                return await self._process_one_conversation(
                    property_id=property_id,
                    conversation=conversation,
                    dry_run=dry_run,
                )

        outcomes = await asyncio.gather(
            *(_worker(conv) for conv in conversations),
        )

        cases: list[DecisionCase] = []
        stats = EpisodeStats()
        last_ended: datetime | None = None
        episodes_emitted = 0
        cases_skipped = 0
        unanswered = 0
        for outcome in outcomes:
            episodes_emitted += outcome.episodes_emitted
            cases_skipped += outcome.cases_skipped
            if outcome.unanswered:
                unanswered += 1
            cases.extend(outcome.extracted_cases)
            stats = _merge_stats(stats, outcome.stats)
            last_ended = _max_optional_dt(last_ended, outcome.last_ended_at)
        return _FanOutResult(
            conversations_loaded=len(conversations),
            episodes_emitted=episodes_emitted,
            cases_skipped=cases_skipped,
            unanswered=unanswered,
            cases=cases,
            stats=stats,
            last_ended_at=last_ended,
        )

    async def _process_one_conversation(
        self,
        *,
        property_id: str,
        conversation: ArchivedConversation,
        dry_run: bool,
    ) -> _ConversationOutcome:
        """Run the per-conversation pipeline and return its outcome.

        Mirrors the inline body of the legacy
        :meth:`_bootstrap_property` async-for loop, but returns a
        :class:`_ConversationOutcome` so a parallel
        :func:`asyncio.gather` only needs to fold counters at the
        end.  All side-effects (sandbox drafts, case-store writes)
        fire eagerly inside this coroutine.

        Per-worker errors are captured into ``outcome.error`` rather
        than propagated so a single bad thread does not poison the
        rest of a cold-start run.  ``CancelledError`` is re-raised so
        task cancellation continues to unwind cleanly.
        """
        outcome = _ConversationOutcome(
            unanswered=_is_unanswered(conversation),
            last_ended_at=conversation.ended_at,
        )
        try:
            if outcome.unanswered and not dry_run:
                await self._emit_sandbox_reply(
                    property_id=property_id,
                    conversation=conversation,
                )
            await self._emit(
                property_id=property_id,
                kind=EventKind.CONVERSATION_LOADED,
                payload={
                    "conversation_id": conversation.conversation_id,
                    "message_count": len(conversation.messages),
                    "has_dates": (
                        conversation.arrival_date is not None
                        or conversation.departure_date is not None
                    ),
                },
            )
            episodes, stats = self._episodes.split(conversation)
            outcome.stats = stats
            if not episodes:
                outcome.cases_skipped += 1
                skip_reason = _diagnose_empty_thread_reason(conversation)
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.CONVERSATION_SKIPPED,
                    payload={
                        "conversation_id": conversation.conversation_id,
                        "reason": skip_reason.value,
                        "guest_messages": sum(
                            1
                            for m in conversation.messages
                            if m.sender is MessageSender.GUEST
                        ),
                        "pm_messages": sum(
                            1
                            for m in conversation.messages
                            if m.sender is MessageSender.PM
                        ),
                        "total_messages": len(conversation.messages),
                    },
                )
                return outcome
            outcome.episodes_emitted = len(episodes)
            cases: list[DecisionCase] = []
            for episode in episodes:
                extraction = await self._extract_one_with_reason(
                    property_id=property_id,
                    episode=episode,
                )
                if extraction.case is None:
                    outcome.cases_skipped += 1
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.CASE_SKIPPED,
                        payload={
                            "conversation_id": conversation.conversation_id,
                            "reason": (
                                extraction.skip_reason.value
                                if extraction.skip_reason is not None
                                else SkipReason.OTHER.value
                            ),
                        },
                    )
                    continue
                case = extraction.case
                if not dry_run:
                    await self._case_store.store(case)
                    await self._record_episode(
                        property_id=property_id, case=case,
                    )
                cases.append(case)
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.CASE_EXTRACTED,
                    payload={
                        "conversation_id": conversation.conversation_id,
                        "scenario": case.scenario.value,
                        "decision_type": case.decision.action_type.value,
                        "stage": case.stage.value,
                        "is_learnable": case.is_learnable,
                    },
                )
            outcome.extracted_cases = tuple(cases)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - per-conversation isolation
            self._log.exception(
                "bootstrap.conversation_failed",
                property_id=property_id,
                conversation_id=conversation.conversation_id,
            )
            outcome.error = str(exc) or exc.__class__.__name__
        return outcome

    async def _background_mine(
        self,
        *,
        property_id: str,
        cases: list[DecisionCase],
        dry_run: bool,
    ) -> None:
        """Run pattern mining off the request path; never raise.

        Scheduled as a fire-and-forget task by :meth:`bootstrap_fast`
        when ``mine_patterns_inline`` is ``False``.  Failures are
        logged but never propagated — by the time this task runs the
        cold-start HTTP response has already been sent.
        """
        try:
            await self._mine_and_store(
                property_id=property_id,
                cases=cases,
                dry_run=dry_run,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - background, best-effort
            self._log.exception(
                "bootstrap.background_mine_failed",
                property_id=property_id,
            )

    async def _emit_sandbox_reply(
        self,
        *,
        property_id: str,
        conversation: ArchivedConversation,
    ) -> None:
        """Generate + persist one example reply; swallow any failure."""
        if self._sandbox_generator is None or self._sandbox_store is None:
            return
        last_guest = _last_guest_message(conversation)
        if last_guest is None:
            return
        try:
            reply = await self._sandbox_generator.generate(
                property_id=property_id,
                guest_message=last_guest.text,
                language=last_guest.language,
            )
        except Exception:  # noqa: BLE001 - sandbox is best-effort
            self._log.exception(
                "bootstrap.sandbox_generate_failed",
                property_id=property_id,
                conversation_id=conversation.conversation_id,
            )
            return
        if not reply.strip():
            self._log.warning(
                "bootstrap.sandbox_empty_reply",
                property_id=property_id,
                conversation_id=conversation.conversation_id,
            )
            return
        thread = UnansweredThread(
            conversation_id=conversation.conversation_id,
            property_id=property_id,
            last_guest_message=last_guest.text,
            last_guest_sent_at=last_guest.sent_at,
            example_reply=reply,
            generated_by=self._sandbox_generator.name,
            language=last_guest.language,
            needs_review_reason=classify_review_need(reply),
        )
        try:
            await self._sandbox_store.put(thread)
        except Exception:  # noqa: BLE001 - sandbox is best-effort
            self._log.exception(
                "bootstrap.sandbox_store_failed",
                property_id=property_id,
                conversation_id=conversation.conversation_id,
            )

    async def _harvest_profile(
        self,
        *,
        property_id: str,
        conversations_loaded: int,
        unanswered_threads: int,
        last_conversation_at: datetime | None,
    ) -> tuple[bool, int, int]:
        """Call the profile harvester and project its counters.

        Returns:
            ``(profile_built, rate_plans_seen, reviews_seen)``.
        """
        assert self._profile_harvester is not None  # type guard
        # Phase 3: prefer a middleware-bound TenantContext over the
        # constructor-baked env defaults so Sandbox UI requests
        # against a non-default tenant hit the harvester with the
        # right ``customer_id`` / ``org_id`` / ``provider_type``.
        from brain_engine.tenants import current_tenant

        tenant_ctx = current_tenant()
        if tenant_ctx is not None and tenant_ctx.customer_id:
            harvest_customer_id = tenant_ctx.customer_id
            harvest_org_id = (
                tenant_ctx.org_id
                if tenant_ctx.org_id is not None
                else self._profile_org_id
            )
            harvest_provider_type = (
                tenant_ctx.provider_type or self._profile_provider_type
            )
        else:
            harvest_customer_id = self._profile_customer_id
            harvest_org_id = self._profile_org_id
            harvest_provider_type = self._profile_provider_type
        try:
            result = await self._profile_harvester.harvest(
                property_channel_id=property_id,
                customer_id=harvest_customer_id,
                org_id=harvest_org_id,
                provider_type=harvest_provider_type,
                counts=HarvestCounts(
                    reservation_count=0,
                    conversation_count=conversations_loaded,
                    last_conversation_at=last_conversation_at,
                    unanswered_thread_count=unanswered_threads,
                ),
            )
        except Exception:  # noqa: BLE001 - profile is best-effort
            self._log.exception(
                "bootstrap.profile_harvest_failed",
                property_id=property_id,
            )
            return False, 0, 0
        if result is None:
            return False, 0, 0
        await self._emit(
            property_id=property_id,
            kind=EventKind.PROFILE_BUILT,
            payload={
                "rate_plans_seen": len(result.rate_plans),
                "reviews_seen": len(result.reviews),
                "conversations_loaded": conversations_loaded,
                "unanswered_threads": unanswered_threads,
            },
        )
        return (
            True,
            len(result.rate_plans),
            len(result.reviews),
        )

    async def _extract_one(
        self,
        *,
        property_id: str,
        episode: object,
    ) -> DecisionCase | None:
        """Call the extractor, logging + swallowing extraction errors."""
        outcome = await self._extract_one_with_reason(
            property_id=property_id, episode=episode,
        )
        return outcome.case

    async def _extract_one_with_reason(
        self,
        *,
        property_id: str,
        episode: object,
    ) -> ExtractionOutcome:
        """Extract one case + propagate the structured skip reason.

        Wraps :meth:`HistoricalCaseExtractor.extract_with_reason` so a
        :class:`HistoricalExtractionError` collapses to a
        :class:`SkipReason.CLASSIFIER_FAILED` outcome rather than a
        propagated exception.  The bus consumer can then surface the
        failure mode in the audit log without us having to re-route
        exceptions through the fan-out.
        """
        try:
            return await self._extractor.extract_with_reason(episode)  # type: ignore[arg-type]
        except HistoricalExtractionError as exc:
            self._log.warning(
                "bootstrap.extract_failed",
                property_id=property_id,
                conversation_id=exc.conversation_id,
                reason=str(exc),
            )
            return ExtractionOutcome(
                case=None, skip_reason=SkipReason.CLASSIFIER_FAILED,
            )

    async def _mine_and_store(
        self,
        *,
        property_id: str,
        cases: Iterable[DecisionCase],
        dry_run: bool,
    ) -> tuple[int, PatternMiningReport]:
        """Run the miner, validate each rule, and persist the keepers.

        Returns the count of rules that passed validation (i.e. would
        be — or were — persisted), not the raw miner output, so the
        same number is comparable across ``dry_run`` and live runs and
        matches the contract used by ``/patterns/extract``.

        Sprint-1 bi-temporal step: after validation, each new rule is
        compared against the existing active rules in the same
        ``(scope, scope_id, scenario)`` bucket via
        :func:`_resolve_pattern_rule_contradictions`.  Older rules
        whose ``action_type`` differs and whose conditions overlap are
        re-emitted with ``invalid_at`` (T-scale: when the world
        shifted = ``new.valid_from``) and ``deactivated_at`` (T'-scale:
        ``utc_now()``); both go through the same UPSERT path so the
        registry retains the historical row for audit.
        """
        assert self._miner is not None  # type guard
        rules, report = self._miner.mine(cases)

        valid_rules: list[PatternRule] = []
        for rule in rules:
            validation = self._validator.validate(rule)
            if not validation.valid:
                self._log.warning(
                    "bootstrap.rule_rejected",
                    property_id=property_id,
                    pattern_id=rule.pattern_id,
                    scenario=rule.scenario.value,
                    reasons=list(validation.reasons),
                )
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.RULE_BLOCKED,
                    payload={
                        "source": "miner",
                        "pattern_id": rule.pattern_id,
                        "scenario": rule.scenario.value,
                        "action": rule.action.action_type.value,
                        "confidence": round(rule.confidence, 3),
                        "support_count": rule.support_count,
                        "reasons": list(validation.reasons),
                        "reason": _rule_block_primary_reason(
                            validation.reasons,
                        ),
                    },
                )
                continue
            valid_rules.append(rule)

        invalidated_rules: list[PatternRule] = []
        if self._rule_store is not None and valid_rules:
            invalidated_rules = await self._collect_invalidations(
                property_id=property_id,
                new_rules=valid_rules,
            )

        if not dry_run and self._rule_store is not None:
            for rule in valid_rules:
                try:
                    await self._rule_store.store(rule)
                except Exception:  # noqa: BLE001 - logged, not fatal
                    self._log.exception(
                        "bootstrap.rule_store_failed",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                    )
                    continue
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.RULE_EMITTED,
                    payload={
                        "source": "miner",
                        "pattern_id": rule.pattern_id,
                        "scenario": rule.scenario.value,
                        "action": rule.action.action_type.value,
                        "confidence": round(rule.confidence, 3),
                        "support_count": rule.support_count,
                        "scope": rule.scope.value,
                        "scope_id": rule.scope_id,
                        "dry_run": False,
                    },
                )
        elif dry_run:
            for rule in valid_rules:
                await self._emit(
                    property_id=property_id,
                    kind=EventKind.RULE_EMITTED,
                    payload={
                        "source": "miner",
                        "pattern_id": rule.pattern_id,
                        "scenario": rule.scenario.value,
                        "action": rule.action.action_type.value,
                        "confidence": round(rule.confidence, 3),
                        "support_count": rule.support_count,
                        "scope": rule.scope.value,
                        "scope_id": rule.scope_id,
                        "dry_run": True,
                    },
                )

        if not dry_run and self._rule_store is not None:
            for invalidated in invalidated_rules:
                try:
                    await self._rule_store.store(invalidated)
                    self._log.info(
                        "bootstrap.rule_invalidated",
                        property_id=property_id,
                        pattern_id=invalidated.pattern_id,
                        scenario=invalidated.scenario.value,
                        invalid_at=invalidated.invalid_at.isoformat()
                        if invalidated.invalid_at is not None
                        else None,
                    )
                    _emit_pattern_rule_invalidated(
                        scenario=invalidated.scenario.value,
                        scope=invalidated.scope.value,
                    )
                except Exception:  # noqa: BLE001 - logged, not fatal
                    self._log.exception(
                        "bootstrap.rule_invalidation_store_failed",
                        property_id=property_id,
                        pattern_id=invalidated.pattern_id,
                    )

            # Mümin 2026-05-08 round-4 #5a: per-batch
            # ``_merge_subsumed_rules`` only collapses rules emitted by
            # *this* run.  When a previous bootstrap left a narrower
            # sibling rule in the active set that the current run's
            # broader sibling now subsumes, the narrower one persists in
            # ``GET /patterns/rules``.  Sweep the now-active set per
            # touched ``(scope, scope_id, scenario)`` bucket and
            # deactivate any rule a sibling already covers.
            await self._sweep_subsumed_actives(
                property_id=property_id,
                buckets={
                    (rule.scope, rule.scope_id, rule.scenario)
                    for rule in valid_rules
                },
            )
        return len(valid_rules), report

    async def _extract_per_scenario_and_store(
        self,
        *,
        property_id: str,
        owner_id: str,
        cases: Iterable[DecisionCase],
        dry_run: bool,
    ) -> int:
        """Run the API-grade ``PatternExtractor`` for every scenario.

        Closes the bootstrap path's "rules_emitted=0 even though
        /extract finds a rule" gap Mümin reported on 323133.  The
        legacy bootstrap pipeline used :class:`PatternMiner` alone,
        which synthesises rules at support=2-3.  These get rejected
        by :class:`PatternValidator` (``MIN_SUPPORT_AUTO = 5``), so
        the bootstrap report's ``rules_emitted`` was structurally
        zero even when /patterns/extract on the same data produced
        a valid rule.

        This helper closes the gap: after the miner finishes, the
        :class:`PatternExtractor` is invoked once per distinct
        scenario (skipping :attr:`Scenario.GENERAL`, which the
        extractor itself filters out).  Each returned rule goes
        through the same validator + store path the miner already
        uses, so the audit trail and contradiction handling stay
        consistent.

        Returns the count of extractor-produced rules persisted
        after validation.  When the extractor is not wired (or
        rule store / store missing) the helper is a no-op and
        returns ``0``.
        """
        if self._pattern_extractor is None:
            return 0
        if self._rule_store is None:
            return 0
        scenarios_seen: set[Scenario] = set()
        for case in cases:
            if case.scenario is Scenario.GENERAL:
                continue
            scenarios_seen.add(case.scenario)
        if not scenarios_seen:
            return 0
        emitted = 0
        for scenario in sorted(scenarios_seen, key=lambda s: s.value):
            try:
                result = (
                    await self._pattern_extractor.extract_patterns(
                        scenario=scenario,
                        property_id=property_id,
                        owner_id=owner_id,
                    )
                )
            except Exception:  # noqa: BLE001 - logged, not fatal
                self._log.exception(
                    "bootstrap.extractor_failed",
                    property_id=property_id,
                    scenario=scenario.value,
                )
                continue
            for rule in result.rules:
                validation = self._validator.validate(rule)
                if not validation.valid:
                    self._log.debug(
                        "bootstrap.extractor_rule_rejected",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                        scenario=rule.scenario.value,
                        reasons=list(validation.reasons),
                    )
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.RULE_BLOCKED,
                        payload={
                            "source": "extractor",
                            "pattern_id": rule.pattern_id,
                            "scenario": rule.scenario.value,
                            "action": rule.action.action_type.value,
                            "confidence": round(rule.confidence, 3),
                            "support_count": rule.support_count,
                            "reasons": list(validation.reasons),
                            "reason": _rule_block_primary_reason(
                                validation.reasons,
                            ),
                        },
                    )
                    continue
                if dry_run:
                    emitted += 1
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.RULE_EMITTED,
                        payload={
                            "source": "extractor",
                            "pattern_id": rule.pattern_id,
                            "scenario": rule.scenario.value,
                            "action": rule.action.action_type.value,
                            "confidence": round(rule.confidence, 3),
                            "support_count": rule.support_count,
                            "scope": rule.scope.value,
                            "scope_id": rule.scope_id,
                            "dry_run": True,
                        },
                    )
                    continue
                try:
                    await self._rule_store.store(rule)
                    emitted += 1
                    self._log.info(
                        "bootstrap.extractor_rule_persisted",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                        scenario=rule.scenario.value,
                        action=rule.action.action_type.value,
                        confidence=round(rule.confidence, 3),
                        support_count=rule.support_count,
                    )
                    await self._emit(
                        property_id=property_id,
                        kind=EventKind.RULE_EMITTED,
                        payload={
                            "source": "extractor",
                            "pattern_id": rule.pattern_id,
                            "scenario": rule.scenario.value,
                            "action": rule.action.action_type.value,
                            "confidence": round(rule.confidence, 3),
                            "support_count": rule.support_count,
                            "scope": rule.scope.value,
                            "scope_id": rule.scope_id,
                            "dry_run": False,
                        },
                    )
                except Exception:  # noqa: BLE001 - logged, not fatal
                    self._log.exception(
                        "bootstrap.extractor_rule_store_failed",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                    )
        return emitted

    async def _sweep_subsumed_actives(
        self,
        *,
        property_id: str,
        buckets: set[tuple[PatternScope, str, Scenario]],
    ) -> None:
        """Deactivate rules a sibling subsumes across the active set.

        Runs after :meth:`_mine_and_store` has persisted the freshly
        mined rules + the contradictions resolver has closed any
        cross-action older rules.  Per-batch
        :func:`_merge_subsumed_rules` only sees the current run's
        output, so rules left over from earlier runs that are now
        strictly covered by a refreshed broader sibling stay active.
        This sweep walks each touched bucket once, fetches the active
        rules from the store, applies the same subsumption logic
        across the union, and calls
        :meth:`PatternRuleStore.deactivate` on every rule that did not
        survive.

        Per :func:`_merge_subsumed_rules`, sibling identity is
        ``(scope, scope_id, scenario, action_type)`` so the sweep is
        safe to run across the bucket regardless of action type — the
        helper internally splits by action.

        Errors are logged and never raised: subsumption is a tidiness
        operation, not a correctness gate; the caller must remain
        idempotent if the sweep cannot proceed (e.g. transient store
        outage).
        """
        if self._rule_store is None:
            return
        for scope, scope_id, scenario in buckets:
            try:
                active_rules = await self._rule_store.get_active_rules(
                    scenario=scenario,
                    scope=scope,
                    scope_id=scope_id,
                )
            except Exception:  # noqa: BLE001 - logged, never fatal
                self._log.exception(
                    "bootstrap.subsumption_fetch_failed",
                    property_id=property_id,
                    scope=scope.value,
                    scope_id=scope_id,
                    scenario=scenario.value,
                )
                continue
            kept_rules = _merge_subsumed_rules(active_rules)
            kept_ids = {rule.pattern_id for rule in kept_rules}
            for rule in active_rules:
                if rule.pattern_id in kept_ids:
                    continue
                try:
                    await self._rule_store.deactivate(rule.pattern_id)
                    self._log.info(
                        "bootstrap.rule_subsumed",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                        scenario=rule.scenario.value,
                    )
                except Exception:  # noqa: BLE001 - logged, never fatal
                    self._log.exception(
                        "bootstrap.subsumption_deactivate_failed",
                        property_id=property_id,
                        pattern_id=rule.pattern_id,
                    )

    async def _collect_invalidations(
        self,
        *,
        property_id: str,
        new_rules: list[PatternRule],
    ) -> list[PatternRule]:
        """Compare new rules with existing active ones and resolve contradictions.

        Fetches the existing active rules per
        ``(scope, scope_id, scenario)`` bucket once per bucket (not
        per new rule) so the function is O(buckets) round-trips, not
        O(rules).  Returns the modified candidates ready for UPSERT.
        Pure deterministic — no LLM.
        """
        assert self._rule_store is not None  # type guard
        # Rules emitted by *this* bootstrap run are refreshes, not
        # contradictions — feeding their prior copies back into
        # :func:`_resolve_pattern_rule_contradictions` would mis-classify
        # cross-action pairs (e.g. approve + deny over overlapping
        # slices) as world-shifts and the UPSERT in the caller would
        # clobber the freshly-stored row with ``active=False``.  Exclude
        # self-refresh candidates per bucket so genuine cross-call
        # invalidations still fire.
        new_pattern_ids = {rule.pattern_id for rule in new_rules}

        invalidated: list[PatternRule] = []
        seen_buckets: dict[
            tuple[PatternScope, str, Scenario], list[PatternRule]
        ] = {}
        for new_rule in new_rules:
            key = (new_rule.scope, new_rule.scope_id, new_rule.scenario)
            existing = seen_buckets.get(key)
            if existing is None:
                try:
                    fetched = await self._rule_store.get_active_rules(
                        scenario=new_rule.scenario,
                        scope=new_rule.scope,
                        scope_id=new_rule.scope_id,
                    )
                except Exception:  # noqa: BLE001 - logged, not fatal
                    self._log.exception(
                        "bootstrap.rule_invalidation_fetch_failed",
                        property_id=property_id,
                        scope=new_rule.scope.value,
                        scope_id=new_rule.scope_id,
                        scenario=new_rule.scenario.value,
                    )
                    seen_buckets[key] = []
                    continue
                existing = [
                    rule for rule in fetched
                    if rule.pattern_id not in new_pattern_ids
                ]
                seen_buckets[key] = existing
            invalidated.extend(
                _resolve_pattern_rule_contradictions(new_rule, existing),
            )
        return invalidated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _window(*, days: int, now: datetime) -> tuple[datetime, datetime]:
        """Clamp ``days`` and derive ``(since, until)``."""
        clamped = max(_MIN_DAYS, min(_MAX_DAYS, days))
        return now - timedelta(days=clamped), now


def _resolve_days(days: int | None) -> int:
    """Resolve the look-back window in days.

    ``None`` is the operator's shorthand for "ingest the entire
    archive"; we resolve it to :data:`_MAX_DAYS` (10 years) which is
    the practical upper bound the GraphQL adapter honours.  Explicit
    values are clamped into ``[_MIN_DAYS, _MAX_DAYS]`` so a
    misconfigured request (negative or out-of-range) is rounded to
    a safe boundary rather than rejected silently.
    """
    if days is None:
        return _MAX_DAYS
    return max(_MIN_DAYS, min(_MAX_DAYS, int(days)))


def _clamp_limit(limit: int) -> int:
    """Clamp the per-property ingestion cap into ``[1, _MAX_LIMIT_PER_PROPERTY]``.

    Mümin 2026-05-12 (PR #B): the loader is dimensioned to stream
    100k conversations per property without exhausting memory, but
    the caller can still pass a non-positive value if a request
    body is malformed; clamping guarantees the loader is never
    asked to "yield zero or fewer rows" and the audit log records
    a meaningful ``loader_limit``.
    """
    return max(1, min(_MAX_LIMIT_PER_PROPERTY, int(limit)))


_RULE_BLOCK_REASON_MAP: Final[dict[str, SkipReason]] = {
    "never_auto_scenario": SkipReason.NEVER_AUTO_LEARN,
    "never_auto_learn": SkipReason.NEVER_AUTO_LEARN,
    "support_below_min": SkipReason.INSUFFICIENT_SUPPORT,
    "insufficient_support": SkipReason.INSUFFICIENT_SUPPORT,
    "confidence_below_min": SkipReason.LOW_CONFIDENCE,
    "low_confidence": SkipReason.LOW_CONFIDENCE,
    "too_many_counterexamples": SkipReason.TOO_MANY_COUNTEREXAMPLES,
    "counterexamples_above_max": SkipReason.TOO_MANY_COUNTEREXAMPLES,
    "empty_conditions": SkipReason.NO_CONDITIONS,
    "no_conditions": SkipReason.NO_CONDITIONS,
}


def _rule_block_primary_reason(
    reasons: Iterable[str],
) -> str:
    """Translate the validator's free-form reasons into a stable enum value.

    Each :class:`PatternValidator` rejection carries one or more
    short reason strings.  The audit bus exposes a single
    ``reason`` field per :class:`SkipReason` so operators can filter
    on "show me everything blocked for low confidence".  This helper
    picks the first known reason; unknown reasons collapse to
    :attr:`SkipReason.OTHER`.
    """
    for reason in reasons:
        key = str(reason).strip().lower()
        mapped = _RULE_BLOCK_REASON_MAP.get(key)
        if mapped is not None:
            return mapped.value
    return SkipReason.OTHER.value


def _emit_pattern_rule_invalidated(*, scenario: str, scope: str) -> None:
    """Best-effort Prometheus emit for soft-invalidated rules.

    Wraps the exporter behind a try/except so a broken metrics
    registry can never block bootstrap on the happy path.
    """
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        exporter = build_default_exporter()
        exporter.record_pattern_rule_invalidated(
            scenario=scenario, scope=scope,
        )
    except Exception:  # noqa: BLE001 - metrics are best-effort
        return


def _merge_stats(left: EpisodeStats, right: EpisodeStats) -> EpisodeStats:
    """Sum two :class:`EpisodeStats` counters."""
    return EpisodeStats(
        total_messages=left.total_messages + right.total_messages,
        emitted_episodes=left.emitted_episodes + right.emitted_episodes,
        skipped_leading=left.skipped_leading + right.skipped_leading,
        skipped_trailing=left.skipped_trailing + right.skipped_trailing,
    )


def _stage_distribution(
    cases: Iterable[DecisionCase],
) -> dict[str, int]:
    """Histogram cases by :class:`BookingStage` value.

    Surfaced on :class:`BootstrapPropertyReport` so V1 onboarding can
    verify the date-aware classifier is actually splitting historical
    threads across the lifecycle ladder rather than collapsing every
    one to ``in_stay``.  Sorted by descending count, then by stage name
    for stable serialisation.
    """
    counter: dict[str, int] = {}
    for case in cases:
        key = case.stage.value
        counter[key] = counter.get(key, 0) + 1
    return dict(
        sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])),
    )


def _last_guest_message(
    conversation: ArchivedConversation,
) -> "_GuestMessage | None":
    """Return the most recent non-empty guest message, or ``None``."""
    for message in reversed(conversation.messages):
        if message.sender is not MessageSender.GUEST:
            continue
        if not message.text.strip():
            continue
        return _GuestMessage(
            text=message.text,
            sent_at=message.sent_at,
            language=message.language,
        )
    return None


@dataclass(frozen=True, slots=True)
class _GuestMessage:
    """Narrow projection of :class:`ArchivedMessage` used by the sandbox."""

    text: str
    sent_at: datetime
    language: str


def _diagnose_empty_thread_reason(
    conversation: ArchivedConversation,
) -> SkipReason:
    """Classify *why* :meth:`EpisodeBuilder.split` rejected a thread.

    Mümin 2026-05-12 follow-up: the audit log was originally
    emitting :attr:`SkipReason.EMPTY_THREAD` for every rejected
    conversation, which masked three very different upstream
    realities:

    * The thread carries only PM-side messages (booking
      confirmation, automated welcome) — there is no guest
      question, so dropping it is *correct* and there is nothing
      we could learn from it (``NO_GUEST_MESSAGE``).
    * The guest spoke first but the PM never replied — these are
      genuine unanswered threads that should surface in the
      sandbox example-reply pipeline, not the case extractor
      (``NO_PM_RESPONSE_AFTER_GUEST``).
    * A guest + PM exchange exists but the
      :class:`EpisodeBuilder._scan` gap heuristic dropped it for
      other reasons (large message gaps, ordering anomalies) —
      this is the residual ``EMPTY_THREAD`` bucket and is the
      only remaining mystery the operator must investigate.

    Separating these three cases turns Mümin's "147 conversations
    skipped" number into actionable buckets per cause.
    """
    has_guest = False
    has_pm_after_guest = False
    for message in conversation.messages:
        if message.sender is MessageSender.GUEST:
            has_guest = True
            continue
        if message.sender in (MessageSender.PM,) and has_guest:
            has_pm_after_guest = True
    if not has_guest:
        return SkipReason.NO_GUEST_MESSAGE
    if not has_pm_after_guest:
        return SkipReason.NO_PM_RESPONSE_AFTER_GUEST
    return SkipReason.EMPTY_THREAD


def _is_unanswered(conversation: ArchivedConversation) -> bool:
    """Return ``True`` when the thread's last message is from the guest.

    Mirrors Mümin's onboarding step 12: the PM's sandbox view shows
    the example reply the engine would send on threads that still
    await a host reply.  System / bot messages are ignored when
    deciding who spoke last.
    """
    for message in reversed(conversation.messages):
        if message.sender is MessageSender.SYSTEM:
            continue
        return message.sender is MessageSender.GUEST
    return False


def _max_ended_at(
    current: datetime | None,
    conversation: ArchivedConversation,
) -> datetime:
    """Keep a running max of conversation ``ended_at`` timestamps."""
    ended = conversation.ended_at
    if current is None or ended > current:
        return ended
    return current


def _max_optional_dt(
    left: datetime | None,
    right: datetime | None,
) -> datetime | None:
    """Return the later of two optional datetimes; ``None`` is treated as -inf.

    Used by the fast-bootstrap fan-out to fold per-worker
    ``last_ended_at`` values into a single property-level maximum
    without special-casing ``None`` at every call site.
    """
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
