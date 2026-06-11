"""SQLAlchemy-backed autonomy stores (tenant-scoped).

Persistent implementations of the :class:`AutonomyStore` Protocol in
:mod:`core.brain.autonomy.engine` and the :class:`WorkflowKindRegistry`
Protocol in :mod:`core.brain.autonomy.workflow_kinds` — the sync,
Dify-convention rewrite of the reference's asyncpg autonomy
``postgres_store.py`` @a761e29.  ``put`` UPSERTs on
(tenant, property, workflow); the registry reads enabled kind rows
seeded from vertical packs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.autonomy.models import (
    AutonomyState,
    WorkflowAutonomy,
    WorkflowMetrics,
)
from libs.datetime_utils import naive_utc_now
from models.brain_autonomy import BrainWorkflowAutonomy, BrainWorkflowKind

__all__ = [
    "SQLAlchemyAutonomyStore",
    "SQLAlchemyWorkflowKindRegistry",
]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime) -> datetime:
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _row_to_record(row: BrainWorkflowAutonomy) -> WorkflowAutonomy:
    return WorkflowAutonomy(
        property_id=row.property_id,
        workflow=row.workflow,
        state=AutonomyState(row.state),
        metrics=WorkflowMetrics(
            sample_size=int(row.sample_size),
            success_rate=float(row.success_rate),
            override_rate=float(row.override_rate),
            incidents=int(row.incidents),
            mean_latency_seconds=float(row.mean_latency_seconds),
        ),
        hold_seconds=int(row.hold_seconds),
        changed_at=_to_aware(row.changed_at),
        changed_by=row.changed_by,
        reason=row.reason,
    )


class SQLAlchemyAutonomyStore:
    """Tenant-scoped :class:`AutonomyStore` over Dify's SQLAlchemy stack."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def _get_row(self, session, property_id: str, workflow: str) -> BrainWorkflowAutonomy | None:
        return session.execute(
            select(BrainWorkflowAutonomy).where(
                BrainWorkflowAutonomy.tenant_id == self._tenant_id,
                BrainWorkflowAutonomy.property_id == property_id,
                BrainWorkflowAutonomy.workflow == workflow,
            )
        ).scalar_one_or_none()

    def get(self, *, property_id: str, workflow: str) -> WorkflowAutonomy | None:
        with self._session_maker() as session:
            row = self._get_row(session, property_id, workflow)
            return None if row is None else _row_to_record(row)

    def put(self, record: WorkflowAutonomy) -> None:
        with self._session_maker() as session:
            row = self._get_row(session, record.property_id, record.workflow)
            if row is None:
                row = BrainWorkflowAutonomy(
                    tenant_id=self._tenant_id,
                    property_id=record.property_id,
                    workflow=record.workflow,
                    changed_at=_to_naive(record.changed_at),
                )
                session.add(row)
            row.state = record.state.value
            row.sample_size = record.metrics.sample_size
            row.success_rate = record.metrics.success_rate
            row.override_rate = record.metrics.override_rate
            row.incidents = record.metrics.incidents
            row.mean_latency_seconds = record.metrics.mean_latency_seconds
            row.hold_seconds = record.hold_seconds
            row.changed_at = _to_naive(record.changed_at) if record.changed_at else naive_utc_now()
            row.changed_by = record.changed_by
            row.reason = record.reason
            session.commit()

    def list_for_property(self, property_id: str) -> list[WorkflowAutonomy]:
        with self._session_maker() as session:
            rows = (
                session.execute(
                    select(BrainWorkflowAutonomy)
                    .where(
                        BrainWorkflowAutonomy.tenant_id == self._tenant_id,
                        BrainWorkflowAutonomy.property_id == property_id,
                    )
                    .order_by(BrainWorkflowAutonomy.workflow.asc())
                )
                .scalars()
                .all()
            )
            return [_row_to_record(row) for row in rows]


class SQLAlchemyWorkflowKindRegistry:
    """Tenant-scoped :class:`WorkflowKindRegistry` over registry rows."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def _rows(self) -> list[BrainWorkflowKind]:
        with self._session_maker() as session:
            return list(
                session.execute(
                    select(BrainWorkflowKind)
                    .where(
                        BrainWorkflowKind.tenant_id == self._tenant_id,
                        BrainWorkflowKind.enabled.is_(True),
                    )
                    .order_by(BrainWorkflowKind.kind.asc())
                )
                .scalars()
                .all()
            )

    def kinds(self) -> tuple[str, ...]:
        return tuple(row.kind for row in self._rows())

    def labels(self) -> dict[str, str]:
        return {row.kind: (row.label or row.kind) for row in self._rows()}

    def resolve_event(self, event_type: str) -> str | None:
        if not event_type:
            return None
        needle = event_type.lower()
        for row in self._rows():
            if row.kind.lower() == needle:
                return row.kind
            for alias in row.event_aliases or ():
                if str(alias).lower() == needle:
                    return row.kind
        return None
