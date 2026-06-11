"""SQLAlchemy models for per-workflow autonomy (Cendra brain kernel, Batch 2).

Two tables:

- ``brain_workflow_autonomy`` — persistent backend for
  :class:`core.brain.autonomy.models.WorkflowAutonomy`: one row per
  (tenant, property, workflow) carrying the OBSERVE / SEMI_AUTO /
  AUTOPILOT state, the five reliability metrics, and the transition
  audit trail (``changed_at`` / ``changed_by`` / ``reason``).
- ``brain_workflow_kinds`` — the per-tenant workflow-kind registry
  mandated by PORTING_MAP for Batch 2 (the reference's 12-member
  ``WorkflowKind`` enum was hospitality vocabulary; kinds are now
  tenant rows seeded from vertical packs, with their ``event_type``
  aliases for the metrics collector's resolver).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, StringUUID


class BrainWorkflowAutonomy(Base, DefaultFieldsMixin):
    __tablename__ = "brain_workflow_autonomy"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "workflow",
            name="brain_workflow_autonomy_scope_uq",
        ),
        sa.Index("brain_workflow_autonomy_tenant_idx", "tenant_id"),
        sa.Index("brain_workflow_autonomy_property_idx", "tenant_id", "property_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    property_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="observe")
    sample_size: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    success_rate: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    override_rate: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    incidents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mean_latency_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    hold_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=60)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="initialized")


class BrainWorkflowKind(Base, DefaultFieldsMixin):
    __tablename__ = "brain_workflow_kinds"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "kind", name="brain_workflow_kinds_tenant_kind_uq"),
        sa.Index("brain_workflow_kinds_tenant_idx", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    event_aliases: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    # Operator-facing display name (CEN-50); nullable — readers fall back
    # to ``kind`` so the wire value never leaks an empty label.
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
