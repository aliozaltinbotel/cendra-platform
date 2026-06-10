"""SQLAlchemy-backed blocker persistence (tenant-scoped).

Production implementation of the :class:`BlockerStore` Protocol in
:mod:`core.brain.patterns.blockers` — the sync, Dify-convention rewrite
of the reference's asyncpg ``PgBlockerStore``
(``blockers/postgres_store.py`` @a761e29).  ``save`` UPSERTs on
``blocker_id`` (resolution rewrites the row via :meth:`update`);
``get_active`` returns unresolved blockers for a property (strict
reservation match when the filter is supplied — reference parity),
newest first per the reference's pg ordering.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.blockers import Blocker, BlockerSeverity
from libs.datetime_utils import naive_utc_now
from models.brain_blockers import BrainBlocker

__all__ = ["SQLAlchemyBlockerStore"]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _row_to_blocker(row: BrainBlocker) -> Blocker:
    created_at = _to_aware(row.created_at)
    if created_at is None:
        raise ValueError(f"blocker row {row.blocker_id!r} has NULL created_at")
    return Blocker(
        blocker_id=row.blocker_id,
        blocker_type=row.blocker_type,
        severity=BlockerSeverity(row.severity),
        property_id=row.property_id,
        reservation_id=row.reservation_id,
        description=row.description or "",
        blocks_actions=tuple(row.blocks_actions or ()),
        metadata=dict(row.meta or {}),
        created_at=created_at,
        resolved_at=_to_aware(row.resolved_at),
        resolved_by=row.resolved_by,
    )


def _apply(row: BrainBlocker, blocker: Blocker) -> None:
    row.blocker_type = blocker.blocker_type
    row.severity = blocker.severity.value
    row.property_id = blocker.property_id
    row.reservation_id = blocker.reservation_id
    row.description = blocker.description
    row.blocks_actions = list(blocker.blocks_actions)
    row.meta = dict(blocker.metadata)
    row.resolved_at = _to_naive(blocker.resolved_at)
    row.resolved_by = blocker.resolved_by


class SQLAlchemyBlockerStore:
    """Tenant-scoped :class:`BlockerStore` over Dify's SQLAlchemy stack."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def _get_row(self, session, blocker_id: str) -> BrainBlocker | None:
        return session.execute(
            select(BrainBlocker).where(
                BrainBlocker.tenant_id == self._tenant_id,
                BrainBlocker.blocker_id == blocker_id,
            )
        ).scalar_one_or_none()

    def save(self, blocker: Blocker) -> str:
        with self._session_maker() as session:
            row = self._get_row(session, blocker.blocker_id)
            if row is None:
                row = BrainBlocker(
                    tenant_id=self._tenant_id,
                    blocker_id=blocker.blocker_id,
                    blocker_type=blocker.blocker_type,
                    property_id=blocker.property_id,
                )
                row.created_at = _to_naive(blocker.created_at) or naive_utc_now()
                session.add(row)
            _apply(row, blocker)
            session.commit()
        return blocker.blocker_id

    def get(self, blocker_id: str) -> Blocker | None:
        with self._session_maker() as session:
            row = self._get_row(session, blocker_id)
            return None if row is None else _row_to_blocker(row)

    def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        with self._session_maker() as session:
            stmt = select(BrainBlocker).where(
                BrainBlocker.tenant_id == self._tenant_id,
                BrainBlocker.property_id == property_id,
                BrainBlocker.resolved_at.is_(None),
            )
            if reservation_id is not None:
                stmt = stmt.where(BrainBlocker.reservation_id == reservation_id)
            stmt = stmt.order_by(BrainBlocker.created_at.desc())
            rows = session.execute(stmt).scalars().all()
            return [_row_to_blocker(row) for row in rows]

    def update(self, blocker: Blocker) -> None:
        self.save(blocker)
