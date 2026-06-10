"""SQLAlchemy models for the epistemic layer (Cendra brain kernel, Batch 2).

Persistent backend for :mod:`core.brain.epistemic` (Moat #7).  The
reference ships only the storage Protocols + in-memory variants (its
docstring promised an asyncpg impl that never landed); these tables are
written fresh per porting rule 7 — Dify SQLAlchemy models, tenant-scoped,
satisfying the ported ``ObservationStore`` / ``BeliefStore`` Protocols.

- ``brain_observations`` is **append-only** immutable evidence: rows are
  never updated or deleted (supersession happens via follow-up
  observations).  The BLAKE2B integrity hash from the kernel travels in
  ``integrity_hex`` so tampering stays detectable at rest.
- ``brain_beliefs`` holds the *current* inferred state per subject —
  one row per (tenant, subject), overwritten on each promotion.  History
  lives in the observation log; the audit pack chains belief snapshots.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, StringUUID


class BrainObservation(Base, DefaultFieldsMixin):
    __tablename__ = "brain_observations"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "observation_id", name="brain_observations_tenant_obs_uq"),
        sa.Index("brain_observations_tenant_idx", "tenant_id"),
        sa.Index("brain_observations_subject_idx", "tenant_id", "subject"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    observation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # JSON-wrapped as {"value": <payload>} so scalars, bools and None
    # round-trip unambiguously through the JSON column
    value: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    provenance_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance_source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance_correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    integrity_hex: Mapped[str] = mapped_column(String(64), nullable=False)


class BrainBelief(Base, DefaultFieldsMixin):
    __tablename__ = "brain_beliefs"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "subject", name="brain_beliefs_tenant_subject_uq"),
        sa.Index("brain_beliefs_tenant_idx", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    belief_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    promoted_value: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    wilson_lb: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    sample_size: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    supporting_observation_ids: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    promoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    promoted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    extra: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
