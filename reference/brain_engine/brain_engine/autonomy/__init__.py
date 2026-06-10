"""Per-workflow autonomy — OBSERVE / SEMI_AUTO / AUTOPILOT progression.

Public surface:

- :class:`AutonomyState` — three-state progression enum.
- :class:`WorkflowAutonomy` — state record per (property, workflow).
- :class:`WorkflowMetrics` — five-metric reliability snapshot.
- :class:`PromotionGate` / :class:`PromotionThresholds` — policy.
- :class:`AutonomyEngine` — runtime lifecycle + transition audit.
- :class:`AutonomyStore` / :class:`InMemoryAutonomyStore` —
  Protocol-based persistence.
- :class:`WorkflowKind` / :data:`DEFAULT_WORKFLOW_RESOLVER` —
  canonical workflow taxonomy and event-type fallback resolver.
- :class:`MetricsCollector` / :class:`InteractionSource` /
  :class:`KpiSnapshot` / :class:`WindowAggregate` — pull-mode roll-up
  from interaction history into the gate plus the eight CEO V2 KPIs.
- :class:`PgAutonomyStore` — Postgres-backed :class:`AutonomyStore`
  implementation for cross-restart durability.
- :class:`TrustMeterService` — read-only projection of the engine into
  the V2 wireframe Trust Meter band, including per-band
  :class:`CriteriaProgress` toward the next state.
- :class:`CalendarAutonomyGate` — re-validates earned engine state
  against current calendar reality for calendar-dependent workflows
  (orphan-night exceptions, early checkin, late checkout) so a stale
  promotion never authorises an action the calendar no longer allows.
"""

from __future__ import annotations

from brain_engine.autonomy.calendar_gate import (
    CalendarAutonomyGate,
    CalendarGateDecision,
    CalendarSignal,
    CalendarVerdict,
)
from brain_engine.autonomy.engine import (
    AutonomyEngine,
    AutonomyStore,
    InMemoryAutonomyStore,
)
from brain_engine.autonomy.postgres_store import (
    PgAutonomyStore,
    create_autonomy_pool,
)
from brain_engine.autonomy.trust_meter import (
    Condition,
    CriteriaProgress,
    TrustMeterBand,
    TrustMeterService,
    TrustMeterView,
)
from brain_engine.autonomy.gate import (
    PromotionGate,
    PromotionThresholds,
)
from brain_engine.autonomy.metrics_collector import (
    InteractionSource,
    KpiSnapshot,
    MetricsCollector,
    WindowAggregate,
)
from brain_engine.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
    state_rank,
)
from brain_engine.autonomy.workflow_kinds import (
    DEFAULT_WORKFLOW_RESOLVER,
    WorkflowKind,
    WorkflowResolver,
    default_workflow_for_event,
)

__all__ = [
    "DEFAULT_WORKFLOW_RESOLVER",
    "AutonomyEngine",
    "AutonomyState",
    "AutonomyStore",
    "CalendarAutonomyGate",
    "CalendarGateDecision",
    "CalendarSignal",
    "CalendarVerdict",
    "Condition",
    "CriteriaProgress",
    "InMemoryAutonomyStore",
    "InteractionSource",
    "KpiSnapshot",
    "MetricsCollector",
    "PgAutonomyStore",
    "PromotionGate",
    "PromotionThresholds",
    "TrustMeterBand",
    "TrustMeterService",
    "TrustMeterView",
    "WindowAggregate",
    "WorkflowAutonomy",
    "WorkflowKind",
    "WorkflowMetrics",
    "WorkflowResolver",
    "create_autonomy_pool",
    "default_workflow_for_event",
    "state_rank",
]
