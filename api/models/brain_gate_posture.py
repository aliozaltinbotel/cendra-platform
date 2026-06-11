"""SQLAlchemy models for per-tenant gate posture overrides and audit (CEN-31).

Two tables back the observe-only activation surface:

- ``brain_tenant_gate_postures`` stores the tenant's explicit requested
  posture (``off`` / ``observe``) when operations choose to manage that
  tenant directly instead of relying on the legacy env+allowlist config.
- ``brain_tenant_gate_posture_audits`` is append-only and records every
  supported posture write with actor identity, free-text reason, the
  requested posture transition, and the effective posture before/after
  resolution against the current process config.

``enforce`` is intentionally absent from both write models. The runtime
still reports enforce when process config selects it, but this issue's
surface never persists or transitions a tenant into enforce.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import StringUUID


class BrainTenantGatePosture(Base, DefaultFieldsMixin):
    __tablename__ = "brain_tenant_gate_postures"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", name="brain_tenant_gate_postures_tenant_uq"),
        sa.Index("brain_tenant_gate_postures_tenant_idx", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    posture: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BrainTenantGatePostureAudit(Base, DefaultFieldsMixin):
    __tablename__ = "brain_tenant_gate_posture_audits"
    __table_args__ = (
        sa.Index(
            "brain_tenant_gate_posture_audits_keyset_idx",
            "tenant_id",
            "occurred_at",
            "id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    prior_posture: Mapped[str] = mapped_column(String(16), nullable=False)
    new_posture: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_effective_posture: Mapped[str] = mapped_column(String(16), nullable=False)
    new_effective_posture: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
