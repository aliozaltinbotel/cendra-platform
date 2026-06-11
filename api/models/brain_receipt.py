"""SQLAlchemy model for durable Art. 12 receipt rows (CEN-80).

This is the restart-safe persistence backend behind receipt emission:
one append-only row per emitted :class:`core.brain.compliance.art12_decision.Art12Decision`,
scoped by tenant and stitched into a single linear digest chain.

Current scope is the unsigned Art. 12 decision record itself.  Later
receipt work can extend this table with signing / verification metadata
without changing the append or idempotency semantics introduced here.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, LongText, StringUUID


class BrainArt12Receipt(Base, DefaultFieldsMixin):
    __tablename__ = "brain_art12_receipts"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "decision_id", name="brain_art12_receipts_tenant_decision_uq"),
        # Exactly one successor may point at a given digest, which keeps the
        # tenant chain linear even when two writers race the same tail.
        sa.UniqueConstraint("tenant_id", "prev_digest", name="brain_art12_receipts_tenant_prev_digest_uq"),
        sa.Index("brain_art12_receipts_tenant_idx", "tenant_id"),
        sa.Index("brain_art12_receipts_occurred_idx", "tenant_id", "occurred_at"),
        sa.Index("brain_art12_receipts_digest_idx", "tenant_id", "record_digest"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    decision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    property_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_solver: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(LongText, nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    autonomy_tier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planner_style: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extra: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    prev_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(64), nullable=False)
