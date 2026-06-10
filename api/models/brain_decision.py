"""SQLAlchemy model for DecisionCases (Cendra brain kernel, Batch 2).

Persistent backend for :class:`core.brain.patterns.models.DecisionCase`
— schema mirrors the reference's ``decision_cases`` table (asyncpg,
``infra/postgres-init`` migrations 001/015/028) with Dify conventions:
tenant scope, surrogate uuidv7 ``id``, naive-UTC datetimes,
AdjustedJSON payloads.

Cases are append-only episodic evidence (insert is idempotent on
``case_id``, never updated) with one exception: the Sprint-4 soft
archive flips ``archived_at`` exactly once when the nightly archiver
moves a case out of the hot mining window.  ``decision_at`` is *not*
persisted — matching the reference, where it only anchors the temporal
knowledge graph in-flow (Batch 3).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, LongText, StringUUID


class BrainDecisionCase(Base, DefaultFieldsMixin):
    __tablename__ = "brain_decision_cases"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "case_id", name="brain_decision_cases_tenant_case_uq"),
        sa.Index("brain_decision_cases_tenant_idx", "tenant_id"),
        sa.Index(
            "brain_decision_cases_search_idx",
            "tenant_id",
            "scenario",
            "property_id",
            "owner_id",
            "stage",
        ),
        sa.Index("brain_decision_cases_reservation_idx", "tenant_id", "reservation_id"),
        sa.Index("brain_decision_cases_created_idx", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario: Mapped[str] = mapped_column(String(255), nullable=False)
    # denormalised from decision.action_type for cheap per-scenario stats
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    property_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reservation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_text: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    message_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    response_text: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    extracted_entities: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    pms_snapshot: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    calendar_snapshot: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    ops_snapshot: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    guest_snapshot: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    decision: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    executed_actions: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    outcome: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    evidence_source_ids: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    orchestrator_verdict: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    foundation_scenario_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    origin: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
