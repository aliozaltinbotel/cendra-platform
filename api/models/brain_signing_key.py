"""SQLAlchemy model for published verification keys (CEN-84).

This table stores only the metadata needed to publish historical receipt
verification keys. Private signing material never lands here: rows carry
the tenant, purpose, immutable public ``key_id``, the published public
key bytes, and the KMS locator that the custody layer uses to resolve the
active signer.

The publication contract is intentionally history-preserving:

- ``key_id`` is globally unique and remains fetchable after rotation.
- at most one row may be ``active`` for a given ``(tenant_id, purpose)``.
- retired rows stay readable by ``key_id`` so third-party receipt
  verification can resolve the exact historical key from the exported
  receipt alone.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import StringUUID


class BrainSigningKey(Base, DefaultFieldsMixin):
    __tablename__ = "brain_signing_keys"
    __table_args__ = (
        sa.CheckConstraint("status IN ('active', 'retired')", name="brain_signing_keys_status_ck"),
        sa.UniqueConstraint("key_id", name="brain_signing_keys_key_id_uq"),
        sa.Index("brain_signing_keys_tenant_idx", "tenant_id"),
        sa.Index(
            "brain_signing_keys_tenant_status_activated_idx",
            "tenant_id",
            "status",
            "activated_at",
        ),
        sa.Index(
            "brain_signing_keys_active_tenant_purpose_uq",
            "tenant_id",
            "purpose",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
            sqlite_where=sa.text("status = 'active'"),
        ),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    public_key_base64url: Mapped[str] = mapped_column(String(255), nullable=False)
    kms_key_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    activated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
