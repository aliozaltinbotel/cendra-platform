"""SQLAlchemy-backed CalibrationStore (tenant-scoped, Batch 5).

Persistent sliding-window implementation of the kernel's
CalibrationStore Protocol so abstention evidence survives pod
restarts (the Batch 4 gateway used per-process memory). Reads return
the window oldest -> newest; writes prune rows beyond the window
opportunistically.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.protocols import DEFAULT_WINDOW_SIZE
from models.brain_calibration import BrainCalibrationSample

__all__ = ["SQLAlchemyCalibrationStore"]

logger = logging.getLogger(__name__)


def _to_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


class SQLAlchemyCalibrationStore:
    """Tenant-scoped persistent CalibrationStore."""

    def __init__(
        self,
        *,
        session_maker: sessionmaker,
        tenant_id: str,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        self._session_maker = session_maker
        self._tenant_id = tenant_id
        self._window_size = window_size

    def record(self, sample: CalibrationSample) -> None:
        with self._session_maker() as session:
            session.add(
                BrainCalibrationSample(
                    tenant_id=self._tenant_id,
                    tool_id=sample.tool_id,
                    predicted_confidence=sample.predicted_confidence,
                    actual_success=sample.actual_success,
                    recorded_at=_to_naive(sample.recorded_at),
                )
            )
            session.commit()
            # opportunistic prune beyond the window
            ids = (
                session.execute(
                    select(BrainCalibrationSample.id)
                    .where(
                        BrainCalibrationSample.tenant_id == self._tenant_id,
                        BrainCalibrationSample.tool_id == sample.tool_id,
                    )
                    .order_by(BrainCalibrationSample.recorded_at.desc(), BrainCalibrationSample.id.desc())
                    .offset(self._window_size)
                )
                .scalars()
                .all()
            )
            if ids:
                session.execute(delete(BrainCalibrationSample).where(BrainCalibrationSample.id.in_(ids)))
                session.commit()

    def samples_for(self, tool_id: str) -> Sequence[CalibrationSample]:
        with self._session_maker() as session:
            rows = (
                session.execute(
                    select(BrainCalibrationSample)
                    .where(
                        BrainCalibrationSample.tenant_id == self._tenant_id,
                        BrainCalibrationSample.tool_id == tool_id,
                    )
                    .order_by(BrainCalibrationSample.recorded_at.desc(), BrainCalibrationSample.id.desc())
                    .limit(self._window_size)
                )
                .scalars()
                .all()
            )
            return tuple(
                CalibrationSample(
                    tool_id=row.tool_id,
                    predicted_confidence=float(row.predicted_confidence),
                    actual_success=bool(row.actual_success),
                    recorded_at=row.recorded_at.replace(tzinfo=UTC),
                )
                for row in reversed(rows)
            )

    def clear(self, tool_id: str | None = None) -> None:
        with self._session_maker() as session:
            stmt = delete(BrainCalibrationSample).where(BrainCalibrationSample.tenant_id == self._tenant_id)
            if tool_id is not None:
                stmt = stmt.where(BrainCalibrationSample.tool_id == tool_id)
            session.execute(stmt)
            session.commit()
