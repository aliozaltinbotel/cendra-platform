"""Reusable assembly of the V2 ``OnboardingBootstrapPipeline``.

The pipeline is composed from a handful of already-built
dependencies plus an event bus and a job store derived from the
Redis client.  This assembly used to live inline in the FastAPI
lifespan, which meant the out-of-process Stage 2 bootstrap worker
could not build the same pipeline without copy-pasting the block.
Extracting it here gives the server lifespan and the worker one
shared constructor — and keeps ``server.py`` smaller.

The function is a pure assembly: every heavyweight dependency
(archive loader, stores, harvester, generator, foundation
orchestrator, memory fan-out) is built by the caller and injected,
so this module stays free of backend-selection branching and is
trivially unit-testable with stand-ins.  The only choice it makes
itself is the Redis-vs-in-memory event bus / job store, mirroring
the original lifespan logic exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brain_engine.onboarding import (
    EpisodeBuilder,
    HistoricalCaseExtractor,
    OnboardingBootstrapPipeline,
)
from brain_engine.onboarding.event_bus import (
    InMemoryBootstrapEventBus,
    RedisBootstrapEventBus,
)
from brain_engine.onboarding.job_store import (
    BootstrapJobStore,
    InMemoryBootstrapJobStore,
    RedisBootstrapJobStore,
)
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.extractor import PatternExtractor
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.pattern_miner import PatternMiner

if TYPE_CHECKING:
    from brain_engine.analysis.orchestrator import (
        FoundationAnalysisOrchestrator,
    )
    from brain_engine.onboarding.conversation_archive import (
        ConversationArchiveLoader,
    )
    from brain_engine.patterns.store import (
        DecisionCaseStore,
        PatternRuleStore,
    )
    from brain_engine.profiles.harvester import PropertyProfileHarvester
    from brain_engine.sandbox.generator import ExampleReplyGenerator
    from brain_engine.sandbox.store import UnansweredThreadStore

__all__ = ["build_bootstrap_pipeline"]


def build_bootstrap_pipeline(
    *,
    archive_loader: ConversationArchiveLoader,
    case_store: DecisionCaseStore,
    rule_store: PatternRuleStore | None,
    profile_harvester: PropertyProfileHarvester | None,
    sandbox_generator: ExampleReplyGenerator | None,
    sandbox_store: UnansweredThreadStore | None,
    foundation_orchestrator: FoundationAnalysisOrchestrator | None,
    memory_fanout: Any,
    profile_customer_id: str,
    profile_org_id: str,
    profile_provider_type: str,
    redis_client: Any | None,
) -> tuple[OnboardingBootstrapPipeline, BootstrapJobStore]:
    """Assemble the bootstrap pipeline + its job store from injected deps.

    Returns the pipeline and the cross-replica job store (Redis when
    a client is supplied, in-memory otherwise) so the caller can
    publish both through ``configure_onboarding_deps``.  Mirrors the
    original lifespan assembly one-for-one — no behaviour change.

    Args:
        archive_loader: The conversation archive loader (GraphQL in
            production).
        case_store: Persistence for extracted decision cases.
        rule_store: Persistence for mined rules, or ``None``.  When
            ``None`` the miner + extractor are disabled, matching the
            pipeline's own contract.
        profile_harvester: Property profile harvester, or ``None``.
        sandbox_generator: Example-reply generator, or ``None``.
        sandbox_store: Unanswered-thread store, or ``None``.
        foundation_orchestrator: Foundation analysis orchestrator
            threaded into the case extractor, or ``None``.
        memory_fanout: Shared memory fan-out (typed loosely to match
            the lifespan, which keeps it as ``Any``).
        profile_customer_id: Pod-default Cendra customer id.
        profile_org_id: Pod-default org id.
        profile_provider_type: Pod-default provider type.
        redis_client: Redis client for the cross-replica event bus +
            job store; ``None`` selects the in-memory variants.
    """

    event_bus = (
        RedisBootstrapEventBus(redis_client)
        if redis_client is not None
        else InMemoryBootstrapEventBus()
    )
    job_store: BootstrapJobStore = (
        RedisBootstrapJobStore(redis_client)
        if redis_client is not None
        else InMemoryBootstrapJobStore()
    )
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=archive_loader,
        episode_builder=EpisodeBuilder(),
        case_extractor=HistoricalCaseExtractor(
            case_builder=CaseBuilder(FeatureBuilder()),
            classifier=DecisionClassifier(),
            foundation_orchestrator=foundation_orchestrator,
        ),
        case_store=case_store,
        pattern_miner=PatternMiner() if rule_store is not None else None,
        pattern_extractor=(
            PatternExtractor(case_store) if rule_store is not None else None
        ),
        rule_store=rule_store,
        profile_harvester=profile_harvester,
        profile_customer_id=profile_customer_id,
        profile_org_id=profile_org_id,
        profile_provider_type=profile_provider_type,
        sandbox_generator=sandbox_generator,
        sandbox_store=sandbox_store,
        # The concrete event-bus classes satisfy BootstrapEventBus at
        # runtime but trip mypy's union→Protocol check — a pre-existing
        # gap (same error lived at the old server.py call site).
        event_bus=event_bus,  # type: ignore[arg-type]
        memory_fanout=memory_fanout,
    )
    return pipeline, job_store
