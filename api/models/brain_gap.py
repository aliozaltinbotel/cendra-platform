"""SQLAlchemy model for the Knowledge Gap registry (CEN-15 Part B).

Persistent backend for :mod:`core.brain.abstention.gap_registry`.
``brain_gap`` is **per-event and append-only** (adjudicated ruling
§E2): one row per abstention, never deleted; the only mutable column
is the ``status`` lifecycle (``open`` → ``answered`` / ``dismissed``).
Deduplication into operator-facing cards happens at the read API, not
in storage.

Kernel-neutral by construction (CEN-37 directive): rows are keyed on
``tenant_id + subject_ref`` — an opaque vertical-defined subject — so
no hospitality semantics leak into the schema.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import StringUUID


class BrainGapRecord(Base, DefaultFieldsMixin):
    __tablename__ = "brain_gap"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "gap_id", name="brain_gap_tenant_gap_uq"),
        sa.Index("brain_gap_subject_idx", "tenant_id", "subject_ref"),
        sa.Index("brain_gap_predicate_idx", "tenant_id", "subject_ref", "missing_predicate"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    gap_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    missing_predicate: Mapped[str] = mapped_column(sa.Text, nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    threshold: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    wilson_lb: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kg_snapshot_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
