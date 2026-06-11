"""Durable Art. 12 audit backends for receipt emission.

The immutable receipt payload lives in :mod:`art12_decision`; this
module owns the persistence contract the runtime/emitter will call.
Two invariants are pinned here:

1. The chain is tenant-scoped and linear: every new row must point at
   the current tail digest.
2. Retries are idempotent by ``decision_id``: re-appending the exact
   same record is a no-op that returns the stored digest, while a
   conflicting payload for the same ``decision_id`` is rejected.

The SQLAlchemy implementation recovers the chain head from durable rows
on each process start, so receipt emission can resume safely after a
worker restart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, sessionmaker

from core.brain.compliance.art12_decision import (
    ART12_GENESIS_DIGEST,
    Art12Decision,
    HandlerSolver,
    canonical_record,
)
from models.brain_receipt import BrainArt12Receipt

if TYPE_CHECKING:
    from core.brain.certificates.receipt import ReceiptEnvelope

__all__ = [
    "Art12AuditLogger",
    "InMemoryArt12AuditLogger",
    "SQLAlchemyArt12AuditLogger",
]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime) -> datetime:
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _same_record(left: Art12Decision, right: Art12Decision) -> bool:
    return canonical_record(left) == canonical_record(right)


def _row_to_record(row: BrainArt12Receipt) -> Art12Decision:
    return Art12Decision(
        decision_id=row.decision_id,
        occurred_at=_to_aware(row.occurred_at),
        property_id=row.property_id,
        owner_id=row.owner_id,
        action_kind=row.action_kind,
        handler_solver=HandlerSolver(row.handler_solver),
        rationale=row.rationale,
        provenance_digest=row.provenance_digest,
        autonomy_tier=row.autonomy_tier,
        planner_style=row.planner_style,
        prev_digest=row.prev_digest,
        extra=dict(row.extra or {}),
    )


def _row_to_envelope(row: BrainArt12Receipt) -> ReceiptEnvelope:
    # Local import: certificates/__init__ imports receipt.py, which imports
    # this package — a module-level import would deadlock package init.
    from core.brain.certificates.receipt import ReceiptEnvelope

    return ReceiptEnvelope(
        record=_row_to_record(row),
        record_digest=row.record_digest,
        signed=row.signed,
        key_id=row.key_id,
        algorithm=row.algorithm,
        signature_hex=row.signature_hex,
    )


class Art12AuditLogger(Protocol):
    """Append-only backend for tenant-scoped :class:`Art12Decision` rows.

    Receipt emission (CEN-81) extended the contract beyond bare record
    appends: an envelope-aware append that persists the signature
    metadata alongside the record, an envelope getter for by-
    ``decision_id`` idempotent replay, and the T7 outcome stitch.  The
    chain semantics are unchanged — signature and outcome columns live
    *outside* the canonical record, so digests stay stable.
    """

    def append(self, record: Art12Decision) -> str:
        """Persist one record and return its chained digest."""
        ...

    def append_envelope(self, envelope: ReceiptEnvelope) -> str:
        """Persist one sealed envelope; same chain/idempotency rules as append."""
        ...

    def get_envelope(self, decision_id: str) -> ReceiptEnvelope | None:
        """Return the stored envelope for ``decision_id`` (``None`` when absent)."""
        ...

    def stitch_outcome(self, decision_id: str, *, case_id: str, outcome_status: str) -> bool:
        """Join the T7 outcome back onto the emitted record (first write wins).

        Returns ``True`` when the outcome is recorded (or an identical
        stitch replays), ``False`` when the record is unknown or already
        stitched with a conflicting outcome.
        """
        ...

    def last_digest(self) -> str:
        """Return the current tenant tail digest (or genesis when empty)."""
        ...


class InMemoryArt12AuditLogger:
    """Reference implementation for tests and emitter-only unit seams."""

    def __init__(self, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._records: dict[str, Art12Decision] = {}
        self._digests: dict[str, str] = {}
        self._envelopes: dict[str, ReceiptEnvelope] = {}
        self._outcomes: dict[str, tuple[str, str]] = {}
        self._tail = ART12_GENESIS_DIGEST

    def append(self, record: Art12Decision) -> str:
        existing = self._records.get(record.decision_id)
        if existing is not None:
            if not _same_record(existing, record):
                raise ValueError("decision_id already recorded with different payload")
            return self._digests[record.decision_id]
        if record.prev_digest != self._tail:
            raise ValueError("art12 audit chain break — prev_digest mismatch")
        digest = record.chained_digest()
        self._records[record.decision_id] = record
        self._digests[record.decision_id] = digest
        self._tail = digest
        logger.debug(
            "art12_audit_appended tenant=%s decision=%s",
            self._tenant_id,
            record.decision_id[:8],
        )
        return digest

    def append_envelope(self, envelope: ReceiptEnvelope) -> str:
        digest = self.append(envelope.record)
        self._envelopes.setdefault(envelope.record.decision_id, envelope)
        return digest

    def get_envelope(self, decision_id: str) -> ReceiptEnvelope | None:
        return self._envelopes.get(decision_id)

    def stitch_outcome(self, decision_id: str, *, case_id: str, outcome_status: str) -> bool:
        if decision_id not in self._records:
            return False
        existing = self._outcomes.get(decision_id)
        if existing is not None:
            return existing == (case_id, outcome_status)
        self._outcomes[decision_id] = (case_id, outcome_status)
        return True

    def outcome_of(self, decision_id: str) -> tuple[str, str] | None:
        """Test helper: the stitched ``(case_id, outcome_status)`` pair."""
        return self._outcomes.get(decision_id)

    def last_digest(self) -> str:
        return self._tail


class SQLAlchemyArt12AuditLogger:
    """Tenant-scoped durable backend over Dify's sync SQLAlchemy stack."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def append(self, record: Art12Decision) -> str:
        return self._append(record, envelope=None)

    def append_envelope(self, envelope: ReceiptEnvelope) -> str:
        return self._append(envelope.record, envelope=envelope)

    def get_envelope(self, decision_id: str) -> ReceiptEnvelope | None:
        with self._session_maker() as session:
            row = self._get_row(session, decision_id)
            if row is None:
                return None
            return _row_to_envelope(row)

    def stitch_outcome(self, decision_id: str, *, case_id: str, outcome_status: str) -> bool:
        if not decision_id:
            return False
        with self._session_maker() as session:
            row = self._get_row(session, decision_id)
            if row is None:
                return False
            if row.outcome_status is not None:
                return row.outcome_status == outcome_status and row.case_id == case_id
            row.case_id = case_id
            row.outcome_status = outcome_status
            row.outcome_recorded_at = _to_naive(datetime.now(UTC))
            session.commit()
            return True

    def _append(self, record: Art12Decision, *, envelope: ReceiptEnvelope | None) -> str:
        with self._session_maker() as session:
            existing = self._get_row(session, record.decision_id)
            if existing is not None:
                return self._replay(existing=existing, record=record)

            tail = self._tail_digest(session)
            if record.prev_digest != tail:
                raise ValueError("art12 audit chain break — prev_digest mismatch")

            digest = record.chained_digest()
            row = BrainArt12Receipt(
                tenant_id=self._tenant_id,
                decision_id=record.decision_id,
                occurred_at=_to_naive(record.occurred_at),
                property_id=record.property_id,
                owner_id=record.owner_id,
                action_kind=record.action_kind,
                handler_solver=record.handler_solver.value,
                rationale=record.rationale,
                provenance_digest=record.provenance_digest,
                autonomy_tier=record.autonomy_tier,
                planner_style=record.planner_style,
                extra=dict(record.extra),
                prev_digest=record.prev_digest,
                record_digest=digest,
                signed=envelope.signed if envelope is not None else False,
                key_id=envelope.key_id if envelope is not None else None,
                algorithm=envelope.algorithm if envelope is not None else None,
                signature_hex=envelope.signature_hex if envelope is not None else None,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = self._get_row(session, record.decision_id)
                if existing is not None:
                    return self._replay(existing=existing, record=record)
                raise ValueError("art12 audit chain advanced — refresh last_digest and retry") from exc

        logger.debug(
            "art12_audit_appended tenant=%s decision=%s",
            self._tenant_id,
            record.decision_id[:8],
        )
        return digest

    def last_digest(self) -> str:
        with self._session_maker() as session:
            return self._tail_digest(session)

    def _get_row(self, session, decision_id: str) -> BrainArt12Receipt | None:
        return session.execute(
            select(BrainArt12Receipt).where(
                BrainArt12Receipt.tenant_id == self._tenant_id,
                BrainArt12Receipt.decision_id == decision_id,
            )
        ).scalar_one_or_none()

    def _replay(self, *, existing: BrainArt12Receipt, record: Art12Decision) -> str:
        stored = _row_to_record(existing)
        if not _same_record(stored, record):
            raise ValueError("decision_id already recorded with different payload")
        return existing.record_digest

    def _tail_digest(self, session) -> str:
        successor = aliased(BrainArt12Receipt)
        # The tail is the one row whose digest is not referenced as any
        # later row's ``prev_digest``. The unique (tenant, prev_digest)
        # constraint guarantees at most one successor per node.
        tails = (
            session.execute(
                select(BrainArt12Receipt.record_digest)
                .outerjoin(
                    successor,
                    and_(
                        successor.tenant_id == BrainArt12Receipt.tenant_id,
                        successor.prev_digest == BrainArt12Receipt.record_digest,
                    ),
                )
                .where(
                    BrainArt12Receipt.tenant_id == self._tenant_id,
                    successor.id.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if not tails:
            return ART12_GENESIS_DIGEST
        if len(tails) > 1:
            raise ValueError("art12 audit chain corrupted — multiple tails")
        return tails[0]
