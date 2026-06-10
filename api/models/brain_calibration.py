"""SQLAlchemy model for abstention calibration samples (Batch 5).

Persistent backend for the kernel's CalibrationStore Protocol
(core/brain/abstention/protocols.py) — the reference promised a
Postgres impl that never shipped.  Tenant-scoped sliding windows: the
store reads the most recent N rows per (tenant, tool); older rows are
pruned opportunistically on write.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import StringUUID


class BrainCalibrationSample(Base, DefaultFieldsMixin):
    __tablename__ = "brain_calibration_samples"
    __table_args__ = (sa.Index("brain_calibration_tenant_tool_idx", "tenant_id", "tool_id", "recorded_at"),)

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    tool_id: Mapped[str] = mapped_column(String(255), nullable=False)
    predicted_confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    actual_success: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
