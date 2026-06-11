"""Cendra Knowledge Gap service layer (CEN-15 Part B, CEN-28).

Tenant-scoped facade between the service_api gap read surface and the
kernel gap registry (:mod:`core.brain.abstention.gap_registry` over
``brain_gap``).  Storage is per-event (ruling §E2); this layer applies
the read-API behaviors: status filtering, optional dedup-at-read into
one card per ``missing_predicate``, and the lifecycle transitions
(``open`` → ``answered`` / ``dismissed``).

Kernel-neutrality note: the kernel speaks ``subject_ref`` (opaque,
vertical-defined); the published wire contract's ``property_id`` is
the hospitality pack's mapping onto it, applied by the controller
serializer — never here, never in the kernel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import sessionmaker

from core.brain.abstention.gap_registry import (
    GapStatus,
    aggregate_gaps,
    serialize_gap,
)
from core.brain.abstention.sa_gap_store import SQLAlchemyGapStore
from extensions.ext_database import db

__all__ = ["BrainKnowledgeGapService"]


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


class BrainKnowledgeGapService:
    """Tenant-scoped read/lifecycle facade over the gap registry."""

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._store = SQLAlchemyGapStore(session_maker=_session_maker(), tenant_id=tenant_id)

    def list_gaps(
        self,
        subject_ref: str,
        *,
        status: str = "open",
        dedup: bool = True,
    ) -> dict[str, Any]:
        """Return the gap registry view for one subject.

        ``status`` is ``open`` (default) / ``answered`` / ``dismissed``
        / ``all``; unknown values raise :class:`ValueError` (controller
        maps to 400).  ``dedup=True`` aggregates per-event rows into one
        card per ``missing_predicate``; ``dedup=False`` returns the raw
        per-event history.
        """
        gap_status = self._parse_status(status)
        records = self._store.list_for(subject_ref, status=gap_status)
        if dedup:
            gaps = aggregate_gaps(records)
        else:
            gaps = [serialize_gap(record) for record in records]
        return {
            "subject_ref": subject_ref,
            "as_of_now": datetime.now(UTC).isoformat(),
            "gaps": gaps,
        }

    def mark_answered(self, subject_ref: str, missing_predicate: str) -> int:
        """Close the loop: a later document covers the predicate."""
        return self._store.mark_status(
            subject_ref=subject_ref,
            missing_predicate=missing_predicate,
            status=GapStatus.ANSWERED,
        )

    def mark_dismissed(self, subject_ref: str, missing_predicate: str) -> int:
        """Operator ruled the gap not worth filling."""
        return self._store.mark_status(
            subject_ref=subject_ref,
            missing_predicate=missing_predicate,
            status=GapStatus.DISMISSED,
        )

    @staticmethod
    def _parse_status(status: str) -> GapStatus | None:
        if status == "all":
            return None
        try:
            return GapStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown status {status!r}; expected open|answered|dismissed|all") from exc
