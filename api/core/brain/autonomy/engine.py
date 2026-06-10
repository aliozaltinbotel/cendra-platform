"""Runtime engine for per-workflow autonomy.

Holds current :class:`WorkflowAutonomy` state in a Protocol-based
store, runs the :class:`PromotionGate` when new metrics arrive, and
emits transition events via stdlib logging.

This engine does **not** execute workflows.  It only answers
"given (property, workflow), what state is it in right now?"  The
dispatcher consults the engine *before* acting.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from core.brain.autonomy.gate import PromotionGate
from core.brain.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
    state_rank,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class AutonomyStore(Protocol):
    """Persistence surface for :class:`WorkflowAutonomy` records."""

    def get(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy | None:
        """Return the stored record, or ``None`` when absent."""
        ...

    def put(self, record: WorkflowAutonomy) -> None:
        """Persist (upsert) a record keyed by property+workflow."""
        ...

    def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        """Return every workflow record for a property."""
        ...


class InMemoryAutonomyStore:
    """Dev / test implementation of :class:`AutonomyStore`."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], WorkflowAutonomy] = {}

    def get(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy | None:
        return self._data.get((property_id, workflow))

    def put(self, record: WorkflowAutonomy) -> None:
        self._data[(record.property_id, record.workflow)] = record

    def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        return [r for (pid, _), r in self._data.items() if pid == property_id]


class AutonomyEngine:
    """Lifecycle manager for per-workflow autonomy state."""

    def __init__(
        self,
        *,
        store: AutonomyStore,
        gate: PromotionGate | None = None,
    ) -> None:
        self._store = store
        self._gate = gate or PromotionGate()

    def state_for(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> AutonomyState:
        """Return the active state (defaults to ``OBSERVE``)."""
        record = self._store.get(
            property_id=property_id,
            workflow=workflow,
        )
        return record.state if record else AutonomyState.OBSERVE

    def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        """Return every stored record for a property (read-through)."""
        return self._store.list_for_property(property_id)

    def record(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy:
        """Return the stored record, initializing one on first touch."""
        existing = self._store.get(
            property_id=property_id,
            workflow=workflow,
        )
        if existing is not None:
            return existing
        fresh = WorkflowAutonomy(
            property_id=property_id,
            workflow=workflow,
        )
        self._store.put(fresh)
        return fresh

    def update_metrics(
        self,
        *,
        property_id: str,
        workflow: str,
        metrics: WorkflowMetrics,
        actor: str = "system",
    ) -> WorkflowAutonomy:
        """Persist new metrics and run the promotion gate."""
        current = self.record(
            property_id=property_id,
            workflow=workflow,
        )
        target = self._gate.evaluate(
            current=current.state,
            metrics=metrics,
        )
        updated = replace(
            current,
            metrics=metrics,
            state=target,
            changed_at=datetime.now(UTC),
            changed_by=actor,
            reason=self._reason(current.state, target),
        )
        self._store.put(updated)
        if target is not current.state:
            logger.info(
                "autonomy.transition property_id=%s workflow=%s from=%s to=%s sample=%s success=%s",
                property_id,
                workflow,
                current.state.value,
                target.value,
                metrics.sample_size,
                round(metrics.success_rate, 3),
            )
        return updated

    def force_state(
        self,
        *,
        property_id: str,
        workflow: str,
        state: AutonomyState,
        actor: str,
        reason: str,
    ) -> WorkflowAutonomy:
        """PM override — bypass the gate (audit-logged)."""
        current = self.record(
            property_id=property_id,
            workflow=workflow,
        )
        updated = replace(
            current,
            state=state,
            changed_at=datetime.now(UTC),
            changed_by=actor,
            reason=reason,
        )
        self._store.put(updated)
        logger.warning(
            "autonomy.forced property_id=%s workflow=%s to=%s actor=%s reason=%s",
            property_id,
            workflow,
            state.value,
            actor,
            reason,
        )
        return updated

    @staticmethod
    def _reason(
        from_state: AutonomyState,
        to_state: AutonomyState,
    ) -> str:
        if to_state is from_state:
            return "metrics_unchanged"
        return "promoted" if state_rank(to_state) > state_rank(from_state) else "demoted"
