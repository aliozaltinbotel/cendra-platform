"""Runtime engine for per-workflow autonomy.

Holds current :class:`WorkflowAutonomy` state in a Protocol-based
store, runs the :class:`PromotionGate` when new metrics arrive, and
emits transition events via ``structlog``.

This engine does **not** execute workflows.  It only answers
"given (property, workflow), what state is it in right now?"  The
dispatcher consults the engine *before* acting.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import structlog

from brain_engine.autonomy.gate import PromotionGate
from brain_engine.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
    state_rank,
)

logger = structlog.get_logger(__name__)


@runtime_checkable
class AutonomyStore(Protocol):
    """Persistence surface for :class:`WorkflowAutonomy` records."""

    async def get(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy | None:
        """Return the stored record, or ``None`` when absent."""
        ...

    async def put(self, record: WorkflowAutonomy) -> None:
        """Persist (upsert) a record keyed by property+workflow."""
        ...

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        """Return every workflow record for a property."""
        ...


class InMemoryAutonomyStore:
    """Dev / test implementation of :class:`AutonomyStore`."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], WorkflowAutonomy] = {}

    async def get(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy | None:
        return self._data.get((property_id, workflow))

    async def put(self, record: WorkflowAutonomy) -> None:
        self._data[(record.property_id, record.workflow)] = record

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        return [
            r for (pid, _), r in self._data.items()
            if pid == property_id
        ]


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
        self._log = logger.bind(component="autonomy_engine")

    async def state_for(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> AutonomyState:
        """Return the active state (defaults to ``OBSERVE``)."""
        record = await self._store.get(
            property_id=property_id, workflow=workflow,
        )
        return record.state if record else AutonomyState.OBSERVE

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[WorkflowAutonomy]:
        """Return every stored record for a property (read-through)."""
        return await self._store.list_for_property(property_id)

    async def record(
        self,
        *,
        property_id: str,
        workflow: str,
    ) -> WorkflowAutonomy:
        """Return the stored record, initializing one on first touch."""
        existing = await self._store.get(
            property_id=property_id, workflow=workflow,
        )
        if existing is not None:
            return existing
        fresh = WorkflowAutonomy(
            property_id=property_id,
            workflow=workflow,
        )
        await self._store.put(fresh)
        return fresh

    async def update_metrics(
        self,
        *,
        property_id: str,
        workflow: str,
        metrics: WorkflowMetrics,
        actor: str = "system",
    ) -> WorkflowAutonomy:
        """Persist new metrics and run the promotion gate."""
        current = await self.record(
            property_id=property_id, workflow=workflow,
        )
        target = self._gate.evaluate(
            current=current.state, metrics=metrics,
        )
        updated = replace(
            current,
            metrics=metrics,
            state=target,
            changed_at=datetime.now(timezone.utc),
            changed_by=actor,
            reason=self._reason(current.state, target),
        )
        await self._store.put(updated)
        if target is not current.state:
            self._log.info(
                "autonomy.transition",
                property_id=property_id,
                workflow=workflow,
                from_state=current.state.value,
                to_state=target.value,
                sample=metrics.sample_size,
                success=round(metrics.success_rate, 3),
            )
        return updated

    async def force_state(
        self,
        *,
        property_id: str,
        workflow: str,
        state: AutonomyState,
        actor: str,
        reason: str,
    ) -> WorkflowAutonomy:
        """PM override — bypass the gate (audit-logged)."""
        current = await self.record(
            property_id=property_id, workflow=workflow,
        )
        updated = replace(
            current,
            state=state,
            changed_at=datetime.now(timezone.utc),
            changed_by=actor,
            reason=reason,
        )
        await self._store.put(updated)
        self._log.warning(
            "autonomy.forced",
            property_id=property_id,
            workflow=workflow,
            to_state=state.value,
            actor=actor,
            reason=reason,
        )
        return updated

    @staticmethod
    def _reason(
        from_state: AutonomyState,
        to_state: AutonomyState,
    ) -> str:
        if to_state is from_state:
            return "metrics_unchanged"
        return (
            "promoted"
            if state_rank(to_state) > state_rank(from_state)
            else "demoted"
        )
