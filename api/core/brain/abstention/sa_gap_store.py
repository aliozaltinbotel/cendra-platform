"""SQLAlchemy-backed Knowledge Gap store (tenant-scoped).

Persistent implementation of the :class:`GapStore` Protocol in
:mod:`core.brain.abstention.gap_registry` over the ``brain_gap`` table
(per-event, append-only; ``status`` is the only mutable column).
Follows the kernel SA-store idiom (injected ``session_maker`` +
``tenant_id``, naive-UTC at rest, aware-UTC in the kernel) so it tests
against SQLite like the other stores.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from core.brain.abstention.gap_registry import GapRecord, GapStatus
from models.brain_gap import BrainGapRecord

__all__ = ["SQLAlchemyGapStore"]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime) -> datetime:
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _row_to_record(row: BrainGapRecord) -> GapRecord:
    return GapRecord(
        gap_id=row.gap_id,
        subject_ref=row.subject_ref,
        run_id=row.run_id,
        query=row.query or "",
        missing_predicate=row.missing_predicate,
        confidence=float(row.confidence),
        threshold=float(row.threshold) if row.threshold is not None else None,
        wilson_lb=float(row.wilson_lb),
        as_of=_to_aware(row.as_of),
        dispatched_at=_to_aware(row.dispatched_at),
        kg_snapshot_ref=row.kg_snapshot_ref,
        status=GapStatus(row.status),
    )


class SQLAlchemyGapStore:
    """Tenant-scoped :class:`GapStore` over ``brain_gap``."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._sessions = session_maker
        self._tenant_id = tenant_id

    def record(self, gap: GapRecord) -> None:
        with self._sessions() as session:
            session.add(
                BrainGapRecord(
                    tenant_id=self._tenant_id,
                    gap_id=gap.gap_id,
                    subject_ref=gap.subject_ref,
                    run_id=gap.run_id,
                    query=gap.query,
                    missing_predicate=gap.missing_predicate,
                    confidence=gap.confidence,
                    threshold=gap.threshold,
                    wilson_lb=gap.wilson_lb,
                    as_of=_to_naive(gap.as_of),
                    dispatched_at=_to_naive(gap.dispatched_at),
                    kg_snapshot_ref=gap.kg_snapshot_ref,
                    status=gap.status.value,
                )
            )
            session.commit()

    def list_for(
        self,
        subject_ref: str,
        *,
        status: GapStatus | None = None,
        limit: int = 500,
    ) -> Sequence[GapRecord]:
        stmt = (
            select(BrainGapRecord)
            .where(
                BrainGapRecord.tenant_id == self._tenant_id,
                BrainGapRecord.subject_ref == subject_ref,
            )
            .order_by(BrainGapRecord.as_of.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(BrainGapRecord.status == status.value)
        with self._sessions() as session:
            rows = session.scalars(stmt).all()
        return tuple(_row_to_record(row) for row in rows)

    def mark_status(
        self,
        *,
        subject_ref: str,
        missing_predicate: str,
        status: GapStatus,
    ) -> int:
        stmt = (
            update(BrainGapRecord)
            .where(
                BrainGapRecord.tenant_id == self._tenant_id,
                BrainGapRecord.subject_ref == subject_ref,
                BrainGapRecord.missing_predicate == missing_predicate,
                BrainGapRecord.status != status.value,
            )
            .values(status=status.value)
        )
        with self._sessions() as session:
            result = session.execute(stmt)
            session.commit()
        return int(result.rowcount or 0)
