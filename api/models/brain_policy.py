"""SQLAlchemy model for owner-policy documents (Batch 5).

Persistent registry for the owner-policy DSL (core/brain/policy):
one active document per (tenant, owner); the raw DSL text travels with
its compiled JSON projection for the console UI and the Z3 verifier.
"""

import sqlalchemy as sa
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import AdjustedJSON, LongText, StringUUID


class BrainOwnerPolicy(Base, DefaultFieldsMixin):
    __tablename__ = "brain_owner_policies"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "owner_id", name="brain_owner_policies_tenant_owner_uq"),
        sa.Index("brain_owner_policies_tenant_idx", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    document_text: Mapped[str] = mapped_column(LongText, nullable=False)
    compiled: Mapped[dict] = mapped_column(AdjustedJSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
