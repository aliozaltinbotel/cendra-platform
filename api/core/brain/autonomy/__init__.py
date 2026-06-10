"""Per-workflow autonomy: OBSERVE → SEMI_AUTO → AUTOPILOT progression.

Each repeatable workflow has its own three-state machine, promoted and
demoted by the five-metric :class:`PromotionGate` (sample size, success
rate, override rate, incidents, latency) — conservative promotion, one
breach demotes.  :class:`TrustMeterService` projects the state for the
console UI; :class:`MetricsCollector` folds interaction streams into
:class:`WorkflowMetrics`.

Batch 2 port notes: workflow kinds are opaque per-tenant registry
strings (see :mod:`core.brain.autonomy.workflow_kinds`); the
reference's ``calendar_gate.py`` is not in the Batch 2 list and stays
in the reference for now (PORTING_MAP).
"""

from __future__ import annotations

from core.brain.autonomy.engine import (
    AutonomyEngine,
    AutonomyStore,
    InMemoryAutonomyStore,
)
from core.brain.autonomy.gate import (
    PromotionGate,
    PromotionThresholds,
)
from core.brain.autonomy.metrics_collector import (
    InteractionSource,
    KpiSnapshot,
    MetricsCollector,
    WindowAggregate,
)
from core.brain.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
    state_rank,
)
from core.brain.autonomy.sa_store import (
    SQLAlchemyAutonomyStore,
    SQLAlchemyWorkflowKindRegistry,
)
from core.brain.autonomy.trust_meter import (
    Condition,
    CriteriaProgress,
    TrustMeterBand,
    TrustMeterService,
    TrustMeterView,
)
from core.brain.autonomy.workflow_kinds import (
    EXPLICIT_ATTRIBUTE_RESOLVER,
    InMemoryWorkflowKindRegistry,
    WorkflowKindRegistry,
    WorkflowResolver,
    make_event_resolver,
)

__all__ = [
    "EXPLICIT_ATTRIBUTE_RESOLVER",
    "AutonomyEngine",
    "AutonomyState",
    "AutonomyStore",
    "Condition",
    "CriteriaProgress",
    "InMemoryAutonomyStore",
    "InMemoryWorkflowKindRegistry",
    "InteractionSource",
    "KpiSnapshot",
    "MetricsCollector",
    "PromotionGate",
    "PromotionThresholds",
    "SQLAlchemyAutonomyStore",
    "SQLAlchemyWorkflowKindRegistry",
    "TrustMeterBand",
    "TrustMeterService",
    "TrustMeterView",
    "WindowAggregate",
    "WorkflowAutonomy",
    "WorkflowKindRegistry",
    "WorkflowMetrics",
    "WorkflowResolver",
    "make_event_resolver",
    "state_rank",
]
