"""Memory Factory — Wires all memory components together.

Creates and connects all memory tier instances with shared Redis/Qdrant backends.
Use create_memory_system() to get a fully connected CognitiveController.
Use create_full_system() to get all components including reasoning and learning.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

from brain_engine.api.ops_session import OpsSessionManager
from brain_engine.continual_learning.adaptive_autonomy import (
    AdaptiveAutonomyManager,
)
from brain_engine.continual_learning.grader import APMGrader
from brain_engine.continual_learning.interaction_recorder import (
    InteractionRecorder,
)
from brain_engine.continual_learning.monthly_evaluator import MonthlyEvaluator
from brain_engine.continual_learning.nightly_consolidator import (
    NightlyConsolidator,
)
from brain_engine.continual_learning.skill_evolution import (
    SkillEvolutionEngine,
)
from brain_engine.continual_learning.sop_parser import SOPParser
from brain_engine.durability.checkpointer import PipelineCheckpointer
from brain_engine.durability.interrupt import InterruptResume
from brain_engine.durability.pipeline import DurablePipeline
from brain_engine.durability.retry import LLM_RETRY
from brain_engine.durability.task_queue import TaskQueue
from brain_engine.evaluation.golden_cases_runner import (
    GoldenCasesRunner,
    InMemoryEvaluationResultStore,
)
from brain_engine.evaluation.llm_judge import LLMJudge
from brain_engine.guardrails.pipeline import GuardrailPipeline
from brain_engine.memory.active_process_store import ActiveProcessStore
from brain_engine.memory.cognitive_controller import CognitiveController
from brain_engine.memory.contradiction_detector import ContradictionDetector
from brain_engine.memory.episodic_dedup import EpisodicDedupConsolidator
from brain_engine.memory.episodic_memory import EpisodicMemory, RedisBackend
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.fact_store import FactStore
from brain_engine.memory.guest_history import GuestHistoryStore
from brain_engine.memory.kg_deterministic_sync import DeterministicKGSync
from brain_engine.memory.knowledge_graph import TemporalKnowledgeGraph
from brain_engine.memory.mem0_extractor import Mem0ExtractorService
from brain_engine.memory.memory_consolidator import MemoryConsolidator
from brain_engine.memory.procedural_memory import ProceduralMemory
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.memory.surprise_detector import SurpriseDetector
from brain_engine.memory.working_memory import WorkingMemory
from brain_engine.patterns.store import (
    DecisionCaseStore,
    PatternRuleStore,
)
from brain_engine.reasoning.business_classifier import BusinessFlagClassifier
from brain_engine.reasoning.complexity_router import ComplexityRouter
from brain_engine.reasoning.llm_router import LLMRouter
from brain_engine.reasoning.stakeholder_model import StakeholderModel

# Mem0 batch fact extraction feeds NightlyConsolidator step 1.  Off
# by default — mem0ai is a heavy dep (Postgres + Qdrant write paths,
# OpenAI key required) and we never want a CI run or a fresh dev
# clone to fail just because the package or the LLM credentials are
# missing.  Flip BRAIN_MEM0_EXTRACTOR_ENABLED=1 in the deploy yaml
# to opt in.  ``Mem0ExtractorService.is_available()`` is the
# secondary guard inside the runner — even with the flag on the
# nightly path stays a graceful no-op when the lib or Qdrant cannot
# be reached.
_MEM0_EXTRACTOR_ENV: Final[str] = "BRAIN_MEM0_EXTRACTOR_ENABLED"
_MEM0_FACTS_COLLECTION: Final[str] = "mem0_facts"


def _mem0_extractor_enabled() -> bool:
    """Whether create_full_system constructs a real Mem0ExtractorService.

    Read once during system construction. Default off keeps the
    Mem0-side path dormant until a deploy explicitly opts in.
    """
    raw = os.environ.get(_MEM0_EXTRACTOR_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")
from brain_engine.context.manager import ContextManager
from brain_engine.durability.worker_pool import WorkerPool
from brain_engine.memory.guest_memory_store import GuestMemoryStore
from brain_engine.memory.versioned_procedural import (
    VersionedProceduralMemory,
)
from brain_engine.memory.versioned_procedural_defaults import (
    BrainZFSSnapshotStore,
    InMemoryEvolutionTracker,
    InMemorySkillStore,
    InMemorySuccessSignalSource,
)
from brain_engine.smart_engine.automation_rules import AutomationEngine
from brain_engine.smart_engine.checkin_guide import CheckinGuideGenerator
from brain_engine.smart_engine.iot_processor import IoTProcessor
from brain_engine.smart_engine.report_store import ReportStore
from brain_engine.smart_engine.task_lifecycle import TaskLifecycleManager
from brain_engine.zfs.brain_zfs import BrainZFS

logger = logging.getLogger(__name__)


class MemorySystem:
    """Container holding all memory components.

    Provides easy access to individual components and a unified
    shutdown method.
    """

    def __init__(
        self,
        working: WorkingMemory,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        knowledge_graph: TemporalKnowledgeGraph,
        guest_history: GuestHistoryStore,
        surprise_detector: SurpriseDetector,
        procedural: ProceduralMemory,
        consolidator: MemoryConsolidator,
        event_recorder: EventRecorder,
        cognitive: CognitiveController,
        active_process_store: ActiveProcessStore | None = None,
    ) -> None:
        self.working = working
        self.episodic = episodic
        self.semantic = semantic
        self.knowledge_graph = knowledge_graph
        self.guest_history = guest_history
        self.surprise_detector = surprise_detector
        self.procedural = procedural
        self.consolidator = consolidator
        self.event_recorder = event_recorder
        self.cognitive = cognitive
        self.active_process_store = active_process_store

    async def initialize(self) -> None:
        """Run any startup tasks (e.g., seeding default procedures)."""
        await self.procedural.seed_default_procedures()
        logger.info("Memory system initialized with default procedures")

    async def shutdown(self) -> None:
        """Gracefully close all connections."""
        await self.knowledge_graph.close()
        await self.surprise_detector.close()
        await self.procedural.close()
        await self.guest_history.close()
        await self.semantic.close()
        logger.info("Memory system shut down")


def create_memory_system(
    redis_url: str = "redis://localhost:6379",
    qdrant_url: str = "http://localhost:6333",
    session_id: str = "",
    llm_model: str = "gpt-4o-mini",
    workspace_id: str = "",
) -> MemorySystem:
    """Create a fully wired memory system.

    All components share the same Redis and Qdrant connections.

    Args:
        redis_url: Redis connection URL.
        qdrant_url: Qdrant server URL.
        session_id: Current session identifier.
        llm_model: LLM model for consolidation and entity extraction.

    Returns:
        A MemorySystem with all components connected.
    """
    # Core tiers
    working = WorkingMemory(session_id=session_id)
    episodic = EpisodicMemory(
        backend=RedisBackend(redis_url=redis_url),
        session_id=session_id,
    )
    semantic = SemanticMemory(qdrant_url=qdrant_url)

    # Advanced systems (workspace-scoped for multi-tenancy)
    knowledge_graph = TemporalKnowledgeGraph(
        redis_url=redis_url, workspace_id=workspace_id,
    )
    surprise_detector = SurpriseDetector(
        redis_url=redis_url, workspace_id=workspace_id,
    )
    procedural = ProceduralMemory(
        redis_url=redis_url, workspace_id=workspace_id,
    )
    guest_history = GuestHistoryStore(
        redis_url=redis_url, workspace_id=workspace_id,
    )

    # Consolidator (orchestrates tier migration)
    consolidator = MemoryConsolidator(
        episodic=episodic,
        semantic=semantic,
        knowledge_graph=knowledge_graph,
        surprise_detector=surprise_detector,
        model=llm_model,
    )

    # Event recorder (dual-write to episodic + guest history)
    event_recorder = EventRecorder(
        history=guest_history,
        episodic=episodic,
    )

    # Active process store (7th memory tier, workspace-scoped)
    active_process_store = ActiveProcessStore(
        redis_url=redis_url, workspace_id=workspace_id,
    )

    # Cognitive controller (CoALA brain)
    cognitive = CognitiveController(
        working=working,
        episodic=episodic,
        semantic=semantic,
        knowledge_graph=knowledge_graph,
        guest_history=guest_history,
        surprise_detector=surprise_detector,
        procedural=procedural,
        consolidator=consolidator,
        active_process_store=active_process_store,
    )

    logger.info(
        "Created memory system: redis=%s, qdrant=%s, session=%s",
        redis_url, qdrant_url, session_id,
    )

    return MemorySystem(
        working=working,
        episodic=episodic,
        semantic=semantic,
        knowledge_graph=knowledge_graph,
        guest_history=guest_history,
        surprise_detector=surprise_detector,
        procedural=procedural,
        consolidator=consolidator,
        event_recorder=event_recorder,
        cognitive=cognitive,
        active_process_store=active_process_store,
    )


class FullSystem:
    """Container for ALL Brain Engine components.

    Includes memory system + reasoning + continual learning + API deps.

    Attributes:
        memory: The core memory system.
        complexity_router: CogRouter L1-L4.
        llm_router: LLM model selector.
        stakeholder: Zero-trust stakeholder model.
        skill_engine: Skill evolution engine.
        interaction_recorder: Interaction recorder.
        grader: APM quality grader.
        nightly_consolidator: Nightly consolidation runner.
        monthly_evaluator: Monthly evaluation runner.
        adaptive_autonomy: Autonomy level manager.
        guardrails: Guardrail pipeline.
    """

    def __init__(
        self,
        memory: MemorySystem,
        complexity_router: ComplexityRouter,
        llm_router: LLMRouter,
        stakeholder: StakeholderModel,
        skill_engine: SkillEvolutionEngine,
        interaction_recorder: InteractionRecorder,
        grader: APMGrader,
        nightly_consolidator: NightlyConsolidator,
        monthly_evaluator: MonthlyEvaluator,
        adaptive_autonomy: AdaptiveAutonomyManager,
        guardrails: GuardrailPipeline,
    ) -> None:
        self.memory = memory
        self.complexity_router = complexity_router
        self.llm_router = llm_router
        self.stakeholder = stakeholder
        self.skill_engine = skill_engine
        self.interaction_recorder = interaction_recorder
        self.grader = grader
        self.nightly_consolidator = nightly_consolidator
        self.monthly_evaluator = monthly_evaluator
        self.adaptive_autonomy = adaptive_autonomy
        self.guardrails = guardrails
        self.business_classifier: BusinessFlagClassifier | None = None
        self.ops_session_manager: OpsSessionManager | None = None
        self.durable_pipeline: DurablePipeline | None = None
        self.task_queue: TaskQueue | None = None
        self.worker_pool: WorkerPool | None = None
        self.guest_memory_store: GuestMemoryStore | None = None
        self.automation_engine: AutomationEngine | None = None
        self.iot_processor: IoTProcessor | None = None
        self.redis_client: Any | None = None

    async def initialize(self) -> None:
        """Initialize all subsystems and start workers."""
        await self.memory.initialize()

        if self.worker_pool:
            await self.worker_pool.start()
            logger.info(
                "WorkerPool started: %d workers",
                self.worker_pool.stats["concurrency"],
            )

        logger.info("Full system initialized — all components alive")

    async def shutdown(self) -> None:
        """Shut down all subsystems gracefully."""
        if self.worker_pool:
            await self.worker_pool.stop()
            logger.info("WorkerPool stopped")

        await self.memory.shutdown()
        logger.info("Full system shut down")


def create_full_system(
    redis_url: str = "redis://localhost:6379",
    qdrant_url: str = "http://localhost:6333",
    session_id: str = "",
    llm_model: str = "gpt-4o-mini",
    api_key: str = "",
    workspace_id: str = "",
    case_store: DecisionCaseStore | None = None,
    rule_store: PatternRuleStore | None = None,
) -> FullSystem:
    """Create the complete Brain Engine system with all components.

    Wires together memory, reasoning, continual learning, and guardrails.
    LLM access routes exclusively through the tenant's Azure OpenAI
    deployment — public ``api.openai.com`` is never called.

    Args:
        redis_url: Redis connection URL.
        qdrant_url: Qdrant server URL.
        session_id: Current session identifier.
        llm_model: Primary LLM model identifier.
        api_key: API key for endpoint authentication.

    Returns:
        A FullSystem with everything connected.
    """
    import redis.asyncio as aioredis

    # 1. Memory system (existing, workspace-scoped)
    memory = create_memory_system(
        redis_url, qdrant_url, session_id, llm_model, workspace_id,
    )

    # 2. Redis client for new components
    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    # 3. Reasoning layer
    complexity_router = ComplexityRouter()
    llm_router = LLMRouter()
    stakeholder = StakeholderModel()

    # 4. Guardrails (Azure-only — no public OpenAI fallback)
    guardrails = GuardrailPipeline()

    # 5. Continual learning
    interaction_recorder = InteractionRecorder(redis_client=redis_client)
    grader = APMGrader()
    skill_engine = SkillEvolutionEngine(
        procedural_memory=memory.procedural,
        guardrails=guardrails,
        llm_model=llm_model,
    )
    adaptive_autonomy = AdaptiveAutonomyManager(redis_client=redis_client)

    # 6. Consolidation + evaluation
    # FactStore is always constructed — the underlying Qdrant client
    # is lazy and short-circuits when the cluster is unreachable, so
    # cheap to keep wired even on dev pods without Qdrant.
    fact_store = FactStore(qdrant_url=qdrant_url)
    mem0_extractor: Mem0ExtractorService | None = None
    if _mem0_extractor_enabled():
        mem0_extractor = Mem0ExtractorService(
            qdrant_url=qdrant_url,
            qdrant_collection=_MEM0_FACTS_COLLECTION,
            redis_url=redis_url,
            llm_model=llm_model,
        )
        logger.info(
            "Mem0 batch extraction wired (qdrant_collection=%s, "
            "available=%s).",
            _MEM0_FACTS_COLLECTION,
            mem0_extractor.is_available(),
        )
    else:
        logger.info(
            "Mem0 batch extraction disabled — set "
            "BRAIN_MEM0_EXTRACTOR_ENABLED=1 to enable.",
        )

    # Factory wiring follow-up (2026-05-08) — instantiate the four
    # NightlyConsolidator collaborators wired into __init__ by PR
    # #179 / #181 / #182 / #192 but never injected by the factory.
    # Without this block the components stay None at runtime and
    # every step that guards on them short-circuits — so the wiring
    # PRs were effectively no-ops in production until now.  Each
    # instance is constructed unconditionally; behavioural activation
    # remains gated by the per-step env flags (default off).
    golden_cases_runner: GoldenCasesRunner | None = None
    if case_store is not None:
        golden_cases_runner = GoldenCasesRunner(
            case_store=case_store,
            judge=LLMJudge(llm_model=llm_model),
            result_store=InMemoryEvaluationResultStore(),
            judge_model=llm_model,
        )

    dedup_consolidator = EpisodicDedupConsolidator()

    contradiction_detector = ContradictionDetector(fact_store=fact_store)

    deterministic_kg_sync: DeterministicKGSync | None = None
    if memory.knowledge_graph is not None:
        deterministic_kg_sync = DeterministicKGSync(
            kg=memory.knowledge_graph,
        )

    nightly_consolidator = NightlyConsolidator(
        memory=memory,
        skills=skill_engine,
        recorder=interaction_recorder,
        grader=grader,
        fact_store=fact_store,
        mem0_extractor=mem0_extractor,
        case_store=case_store,
        rule_store=rule_store,
        golden_cases_runner=golden_cases_runner,
        dedup_consolidator=dedup_consolidator,
        contradiction_detector=contradiction_detector,
        deterministic_kg_sync=deterministic_kg_sync,
    )
    monthly_evaluator = MonthlyEvaluator(
        recorder=interaction_recorder,
        skill_engine=skill_engine,
        procedural_memory=memory.procedural,
    )

    # 7. Business classifier + Ops session manager
    # Phase 5 — wire the optional IntelligentClassifier (lingua +
    # fastembed + LiteLLM pick over the 469-scenario foundation
    # registry) so non-English messages and scenarios outside the
    # BusinessFlags taxonomy still produce a decision_type hint.
    # ``build_intelligent_classifier`` returns ``None`` when the
    # foundation document is missing, so the legacy flag-only path
    # remains the default until the document is on disk.
    from brain_engine.patterns.intelligent_classifier_factory import (
        build_intelligent_classifier,
    )

    try:
        intelligent_classifier = build_intelligent_classifier()
    except Exception:
        logger.exception(
            "intelligent_classifier_factory failed; "
            "BusinessFlagClassifier will run without enrichment",
        )
        intelligent_classifier = None
    business_classifier = BusinessFlagClassifier(
        model=llm_model,
        intelligent_classifier=intelligent_classifier,
    )
    ops_session_manager = OpsSessionManager(
        redis_client=redis_client,
    )

    # 8. Durability: checkpointer + pipeline + retry
    checkpointer = PipelineCheckpointer(redis_client)
    interrupt_mgr = InterruptResume(checkpointer)
    durable_pipeline = DurablePipeline(
        checkpointer, interrupt_mgr, default_retry=LLM_RETRY,
    )

    # 9. Task queue + Worker pool (replaces multi-agent)
    task_queue = TaskQueue(redis_client)
    worker_pool = WorkerPool(task_queue, concurrency=5, poll_interval=1.0)
    _register_task_handlers(worker_pool)

    # 10. Guest memory (PostgreSQL-backed, uses FakeDB for now)
    guest_memory_store = _create_guest_memory_store()

    # 11. Automation + IoT (singleton with state)
    automation_engine = AutomationEngine()
    iot_processor = IoTProcessor(automation_engine=automation_engine)

    # 12. SOP parser — converts /knowledge/sync uploaded SOP
    # documents into protected procedural rules (source="sop").
    # Sits on top of the wired ProceduralMemory so a single
    # MemorySystem.procedural read path serves both manual and
    # SOP-derived rules — priority order is preserved (immutable >
    # manual > sop > learned, see module docstring).
    sop_parser = SOPParser(
        procedural_memory=memory.procedural,
        llm_model=llm_model,
    )

    # 13. BrainZFS + ContextManager (ADR-0002 chain).
    brain_zfs = BrainZFS()
    context_manager = ContextManager(zfs=brain_zfs)

    # 14. Versioned procedural memory (advisory §7.2 — snapshot
    # before evolve + auto-rollback when post-evolution success
    # falls below threshold).  The skill / tracker / signals
    # collaborators ship as in-memory defaults so the slot is
    # functional out of the box; production swaps each one for a
    # durable backend without touching call-sites.  The snapshot
    # store rides on the same BrainZFS pool the context manager
    # already uses, so versions land in one COW backend.
    versioned_skill_store = InMemorySkillStore()
    versioned_procedural = VersionedProceduralMemory(
        skills=versioned_skill_store,
        snapshots=BrainZFSSnapshotStore(
            zfs=brain_zfs,
            skill_store=versioned_skill_store,
        ),
        tracker=InMemoryEvolutionTracker(),
        signals=InMemorySuccessSignalSource(),
    )

    # 15. Service report store (cleaning + vendor + inspection +
    # maintenance reports keyed by property / booking / contact in
    # Redis).  Constructor only opens a lazy redis-py client so the
    # slot is wired unconditionally — the same Redis cluster the
    # rest of the system already uses, just under the
    # ``brain:report:*`` keyspace.
    report_store = ReportStore(redis_url=redis_url)

    # 16. Check-in guide generator (multilingual personalised guides
    # built from PropertyAccess + booking metadata).  Constructor
    # takes optional KB / memory handles; we feed the freshly built
    # MemorySystem so the generator can read guest preferences when
    # the eventual caller asks for them.  No I/O in the constructor
    # so the slot is wired unconditionally.
    checkin_guide_generator = CheckinGuideGenerator(memory=memory)

    # 17. Task lifecycle (stateless state-machine for the Cendra
    # task inbox: PENDING → WAITING → MONITOR → DONE).  Cheap to
    # construct unconditionally — there is no I/O in the
    # constructor, so leaving the slot wired keeps the workflow
    # available to any future task-creation path without forcing
    # callers to plumb the dep themselves.
    task_lifecycle = TaskLifecycleManager()

    logger.info(
        "Created full system: redis=%s, qdrant=%s, model=%s",
        redis_url, qdrant_url, llm_model,
    )

    system = FullSystem(
        memory=memory,
        complexity_router=complexity_router,
        llm_router=llm_router,
        stakeholder=stakeholder,
        skill_engine=skill_engine,
        interaction_recorder=interaction_recorder,
        grader=grader,
        nightly_consolidator=nightly_consolidator,
        monthly_evaluator=monthly_evaluator,
        adaptive_autonomy=adaptive_autonomy,
        guardrails=guardrails,
    )
    system.business_classifier = business_classifier
    system.ops_session_manager = ops_session_manager
    system.durable_pipeline = durable_pipeline
    system.task_queue = task_queue
    system.worker_pool = worker_pool
    system.guest_memory_store = guest_memory_store
    system.automation_engine = automation_engine
    system.iot_processor = iot_processor
    system.sop_parser = sop_parser
    system.brain_zfs = brain_zfs
    system.context_manager = context_manager
    system.versioned_procedural = versioned_procedural
    system.report_store = report_store
    system.checkin_guide_generator = checkin_guide_generator
    system.task_lifecycle = task_lifecycle
    system.redis_client = redis_client
    return system


def _register_task_handlers(pool: WorkerPool) -> None:
    """Register background task handlers in the worker pool.

    These handlers process tasks enqueued by API endpoints.

    Args:
        pool: WorkerPool to register handlers on.
    """
    from brain_engine.durability.task_queue import Task

    async def handle_send_welcome(task: Task) -> dict[str, Any]:
        logger.info("Sending welcome for %s", task.payload.get("reservation_id"))
        return {"sent": True, "type": "welcome"}

    async def handle_create_access_code(task: Task) -> dict[str, Any]:
        logger.info("Creating access code for %s", task.payload.get("property_id"))
        return {"created": True, "type": "access_code"}

    async def handle_schedule_cleaning(task: Task) -> dict[str, Any]:
        logger.info("Scheduling cleaning for %s", task.payload.get("property_id"))
        return {"scheduled": True, "type": "cleaning"}

    async def handle_send_upsell(task: Task) -> dict[str, Any]:
        logger.info("Sending upsell offer for %s", task.payload.get("reservation_id"))
        return {"sent": True, "type": "upsell"}

    async def handle_notify_pm(task: Task) -> dict[str, Any]:
        logger.info("Notifying PM: %s", task.payload.get("message", ""))
        return {"notified": True, "type": "pm_alert"}

    pool.register("send_welcome", handle_send_welcome)
    pool.register("create_access_code", handle_create_access_code)
    pool.register("schedule_cleaning", handle_schedule_cleaning)
    pool.register("send_upsell", handle_send_upsell)
    pool.register("notify_pm", handle_notify_pm)

    logger.info("Registered %d task handlers", pool.stats["handlers"])


def _create_guest_memory_store() -> GuestMemoryStore:
    """Create GuestMemoryStore with available database.

    Uses PostgreSQL (asyncpg) if DATABASE_URL is set,
    otherwise falls back to in-memory store for development.

    Returns:
        Configured GuestMemoryStore.
    """
    import os

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        logger.info("GuestMemoryStore: PostgreSQL (%s)", db_url[:30])
        from brain_engine.memory.pg_adapter import AsyncPGAdapter
        return GuestMemoryStore(AsyncPGAdapter(db_url))

    logger.warning("GuestMemoryStore: in-memory (no DATABASE_URL)")
    return GuestMemoryStore(_InMemoryGuestDB())


class _InMemoryGuestDB:
    """In-memory fallback for GuestMemoryStore when PostgreSQL not available."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO guest_memories" in query and args:
            self._rows[args[0]] = {
                "guest_id": args[0], "total_stays": args[1],
                "total_interactions": args[2], "language": args[3],
                "communication_style": args[4],
                "satisfaction_scores": args[5],
                "avg_satisfaction": args[6], "preferences": args[7],
                "common_requests": args[8], "incidents": args[9],
                "risk_flags": args[10], "patterns": args[11],
                "property_history": args[12], "first_seen": args[13],
                "last_seen": args[14], "notes": args[15],
            }

    async def fetchone(self, query: str, *args: Any) -> dict[str, Any] | None:
        if args:
            return self._rows.get(args[0])
        return None

    async def fetchall(self, query: str, *args: Any) -> list[dict[str, Any]]:
        import json
        results = []
        if "property_history" in query and args:
            prop_list = json.loads(args[0])
            for row in self._rows.values():
                history = json.loads(row.get("property_history", "[]"))
                if any(p in history for p in prop_list):
                    results.append(row)
        elif "risk_flags" in query:
            for row in self._rows.values():
                flags = json.loads(row.get("risk_flags", "[]"))
                if flags:
                    results.append(row)
        return results
