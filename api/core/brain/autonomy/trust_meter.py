"""Trust Meter view over per-workflow autonomy state.

The V2 wireframe band ("OBSERVE | SEMI_AUTO | AUTOPILOT" with a
`criteria_progress` hint) is rendered for every workflow on a property,
including workflows that have never run yet (those collapse into a
default ``OBSERVE`` band so the UI can show the full ladder rather than
hiding undiscovered work).

The service is read-only and pure with respect to its inputs — it does
not mutate :class:`WorkflowAutonomy` records.  Promotion / demotion
remains the sole responsibility of :class:`AutonomyEngine`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.gate import (
    PromotionGate,
    PromotionThresholds,
)
from core.brain.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
)

__all__ = [
    "Condition",
    "CriteriaProgress",
    "TrustMeterBand",
    "TrustMeterService",
    "TrustMeterView",
]


_GTE: Final[str] = "gte"
_LTE: Final[str] = "lte"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """One promotion condition rendered for the UI.

    Attributes:
        name: Metric name (matches :pyattr:`PromotionGate.required_metrics`).
        current: Currently observed value.
        target: Threshold the metric must reach / stay under.
        comparator: ``"gte"`` (current ≥ target) or ``"lte"``.
        satisfied: Whether the condition currently passes.
    """

    name: str
    current: float
    target: float
    comparator: str
    satisfied: bool


@dataclass(frozen=True, slots=True)
class CriteriaProgress:
    """Per-band progress toward the next autonomy state.

    Attributes:
        target_state: State this band is trying to reach, or ``None``
            for ``AUTOPILOT`` (terminal).
        conditions: Ordered, immutable list of per-metric conditions.
        satisfied_count: How many conditions currently pass.
    """

    target_state: AutonomyState | None
    conditions: tuple[Condition, ...]
    satisfied_count: int

    @property
    def total(self) -> int:
        """Total number of evaluated conditions."""
        return len(self.conditions)

    @property
    def all_satisfied(self) -> bool:
        """Whether every condition currently passes."""
        return self.satisfied_count == self.total and self.total > 0


@dataclass(frozen=True, slots=True)
class TrustMeterBand:
    """Per-workflow band shown in the V2 Trust Meter strip."""

    workflow: str
    state: AutonomyState
    sample_size: int
    success_rate: float
    override_rate: float
    incidents: int
    mean_latency_seconds: float
    hold_seconds: int
    changed_at: datetime
    changed_by: str
    reason: str
    progress: CriteriaProgress


@dataclass(frozen=True, slots=True)
class TrustMeterView:
    """Top-level response value for a property's Trust Meter."""

    property_id: str
    generated_at: datetime
    bands: tuple[TrustMeterBand, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TrustMeterService:
    """Read-only projection of :class:`AutonomyEngine` state for the UI.

    The service depends on the engine — not the store — so callers get
    the engine's defaults (``OBSERVE`` for unknown workflows) without
    having to duplicate them here.
    """

    def __init__(
        self,
        *,
        engine: AutonomyEngine,
        gate: PromotionGate | None = None,
        workflows: Iterable[str] = (),
    ) -> None:
        # Workflow kinds are vertical/tenant vocabulary — supplied by the
        # per-tenant WorkflowKindRegistry (see workflow_kinds.py), never
        # hardcoded in the kernel (golden rule 4; the reference defaulted
        # to its 12-member WorkflowKind enum here).
        self._engine = engine
        self._gate = gate or PromotionGate()
        self._workflows: tuple[str, ...] = tuple(workflows)

    def for_property(self, property_id: str) -> TrustMeterView:
        """Build the Trust Meter view for one property.

        Every workflow in :attr:`_workflows` is represented exactly once
        — workflows with no stored record collapse into a default
        ``OBSERVE`` band so the UI can render the full ladder.
        """
        stored = self._engine.list_for_property(property_id)
        by_workflow = {r.workflow: r for r in stored}
        bands = tuple(self._band_for(property_id, kind, by_workflow.get(kind)) for kind in self._workflows)
        return TrustMeterView(
            property_id=property_id,
            generated_at=datetime.now(UTC),
            bands=bands,
        )

    # ── Helpers ──────────────────────────────────────────── #

    def _band_for(
        self,
        property_id: str,
        kind: str,
        record: WorkflowAutonomy | None,
    ) -> TrustMeterBand:
        if record is None:
            record = WorkflowAutonomy(
                property_id=property_id,
                workflow=kind,
            )
        return TrustMeterBand(
            workflow=record.workflow,
            state=record.state,
            sample_size=record.metrics.sample_size,
            success_rate=record.metrics.success_rate,
            override_rate=record.metrics.override_rate,
            incidents=record.metrics.incidents,
            mean_latency_seconds=record.metrics.mean_latency_seconds,
            hold_seconds=record.hold_seconds,
            changed_at=record.changed_at,
            changed_by=record.changed_by,
            reason=record.reason,
            progress=self._progress_for(record.state, record.metrics),
        )

    def _progress_for(
        self,
        state: AutonomyState,
        metrics: WorkflowMetrics,
    ) -> CriteriaProgress:
        target = _next_state(state)
        if target is None:
            return CriteriaProgress(
                target_state=None,
                conditions=(),
                satisfied_count=0,
            )
        thresholds = self._gate.thresholds_for(target)
        # ``thresholds_for`` only returns ``None`` for OBSERVE, which
        # ``_next_state`` never produces — so this branch is dead in
        # practice, but kept defensive at the AutonomyEngine boundary.
        if thresholds is None:  # pragma: no cover
            return CriteriaProgress(
                target_state=target,
                conditions=(),
                satisfied_count=0,
            )
        conditions = _conditions(metrics, thresholds)
        return CriteriaProgress(
            target_state=target,
            conditions=conditions,
            satisfied_count=sum(1 for c in conditions if c.satisfied),
        )


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _next_state(state: AutonomyState) -> AutonomyState | None:
    """Return the next state in the OBSERVE → SEMI_AUTO → AUTOPILOT chain."""
    if state is AutonomyState.OBSERVE:
        return AutonomyState.SEMI_AUTO
    if state is AutonomyState.SEMI_AUTO:
        return AutonomyState.AUTOPILOT
    return None


def _conditions(
    metrics: WorkflowMetrics,
    thresholds: PromotionThresholds,
) -> tuple[Condition, ...]:
    """Build the five per-metric conditions for one promotion gate.

    Ordering mirrors :pyattr:`PromotionGate.required_metrics` so the UI
    can render them in a stable column layout.
    """
    rows: Sequence[tuple[str, float, float, str, bool]] = (
        (
            "sample_size",
            float(metrics.sample_size),
            float(thresholds.min_sample_size),
            _GTE,
            metrics.sample_size >= thresholds.min_sample_size,
        ),
        (
            "success_rate",
            metrics.success_rate,
            thresholds.min_success_rate,
            _GTE,
            metrics.success_rate >= thresholds.min_success_rate,
        ),
        (
            "override_rate",
            metrics.override_rate,
            thresholds.max_override_rate,
            _LTE,
            metrics.override_rate <= thresholds.max_override_rate,
        ),
        (
            "incidents",
            float(metrics.incidents),
            float(thresholds.max_incidents),
            _LTE,
            metrics.incidents <= thresholds.max_incidents,
        ),
        (
            "mean_latency_seconds",
            metrics.mean_latency_seconds,
            thresholds.max_mean_latency_seconds,
            _LTE,
            (metrics.mean_latency_seconds <= thresholds.max_mean_latency_seconds),
        ),
    )
    return tuple(
        Condition(
            name=name,
            current=current,
            target=target,
            comparator=comparator,
            satisfied=satisfied,
        )
        for (name, current, target, comparator, satisfied) in rows
    )
