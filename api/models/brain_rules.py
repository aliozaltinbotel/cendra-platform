"""SQLAlchemy model for learned PatternRules (Cendra brain kernel, Batch 2).

Persistent backend for :class:`core.brain.patterns.models.PatternRule` —
schema mirrors the reference's ``pattern_rules`` table (asyncpg,
``deploy/postgres-migrations.yaml`` migration ``001_init.sql``) with two
Dify-convention changes: every row is tenant-scoped via ``tenant_id``,
and a surrogate uuidv7 ``id`` primary key replaces the bare
``pattern_id`` key (``pattern_id`` stays unique per tenant — it is the
extractor's deterministic identity hash, so repeated mining UPSERTs one
row instead of producing orphans).

The bi-temporal lifecycle columns travel verbatim from the reference
(Moat #1 substrate): ``valid_from`` / ``valid_to`` (application-time
validity window), ``invalid_at`` (when the world made the rule wrong),
``deactivated_at`` (when the system learned it was wrong),
``last_seen_at`` (most recent supporting evidence).  Datetimes are
stored naive-UTC per Dify convention; the store layer
(:mod:`core.brain.patterns.rule_store`) converts to/from the kernel's
tz-aware contract.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from libs.datetime_utils import naive_utc_now

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, StringUUID


class BrainPatternRule(Base, DefaultFieldsMixin):
    __tablename__ = "brain_pattern_rules"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "pattern_id", name="brain_pattern_rules_tenant_pattern_uq"),
        sa.Index("brain_pattern_rules_tenant_idx", "tenant_id"),
        sa.Index(
            "brain_pattern_rules_active_scope_idx",
            "tenant_id",
            "active",
            "scenario",
            "scope",
            "scope_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    pattern_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    conditions: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    action: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    blocker_types: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    support_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    counterexample_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="ask")
    valid_from: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=naive_utc_now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invalid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=naive_utc_now)
    source_case_ids: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    foundation_scenario_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    origin: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
