"""SQLAlchemy model for Blockers (Cendra brain kernel, Batch 2).

Persistent backend for :class:`core.brain.patterns.blockers.Blocker` —
mirrors the reference's ``blockers`` table (asyncpg,
``blockers/postgres_store.py`` @a761e29) with Dify conventions: tenant
scope, surrogate uuidv7 ``id``, naive-UTC datetimes, AdjustedJSON
payloads.  ``blocker_type`` and the ``blocks_actions`` entries are
opaque vertical-defined strings (vocabulary lives in
``packs/hospitality/blockers.yaml``).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, LongText, StringUUID


class BrainBlocker(Base, DefaultFieldsMixin):
    __tablename__ = "brain_blockers"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "blocker_id", name="brain_blockers_tenant_blocker_uq"),
        sa.Index("brain_blockers_tenant_idx", "tenant_id"),
        sa.Index(
            "brain_blockers_active_idx",
            "tenant_id",
            "property_id",
            "reservation_id",
            "resolved_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    blocker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    blocker_type: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="hard")
    property_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reservation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    blocks_actions: Mapped[list] = mapped_column(AdjustedJSON, nullable=False, default=list)
    meta: Mapped[dict] = mapped_column("metadata_json", AdjustedJSON, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
