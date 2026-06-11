"""Knowledge Gap registry — abstention-emitted gap records (CEN-15 Part B).

A "missing-info list" is cloneable; the defensible version is a gap
card **emitted by the calibrated-abstention gate (Moat #4)** at the
moment it refuses, persisted in the epistemic store (Moat #5) with
decision-time provenance.  The record is literally "what the system
did not know when it abstained" — it cannot be reproduced without the
operator's own abstention history.

Kernel-neutrality (binding CEN-37 directive): the registry is keyed on
``tenant + subject_ref`` — an opaque vertical-defined subject string —
never on hospitality semantics.  The hospitality pack maps "property"
onto ``subject_ref``; the service_api serializer exposes the pack's
field names, the kernel does not know them.

Adjudicated semantics baked in (CEN-15 §E rulings):

- **§E1 decision-time**: ``as_of`` is the run's inbound-event
  timestamp; the dispatch wall-clock travels separately as
  ``dispatched_at``.
- **§E2 granularity**: storage is **per-event** — one record per
  abstention, append-only.  Deduplication happens only at read time via
  :func:`aggregate_gaps`, keyed on ``missing_predicate``, with
  ``occurrences`` / ``first_seen_at`` / ``last_seen_at`` aggregates.

Emission fires only on :attr:`AbstentionVerdict.ABSTAIN` — an
``INSUFFICIENT_DATA`` verdict is a calibration shortfall, not a
knowledge gap (:func:`build_gap_record` returns ``None`` for it).  The
hook lives in the gate-chain adapter (:mod:`core.brain.gates`): no new
gate slot, no change to the chain order.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from core.brain.abstention.models import AbstentionDecision, AbstentionVerdict
from core.brain.epistemic.as_of import kg_snapshot_ref

__all__ = [
    "AGGREGATE_RUN_ID_CAP",
    "GapRecord",
    "GapStatus",
    "GapStore",
    "InMemoryGapStore",
    "aggregate_gaps",
    "build_gap_record",
    "serialize_gap",
]


AGGREGATE_RUN_ID_CAP = 20
"""Max contributing run ids carried per aggregated gap (latest first)."""


class GapStatus(StrEnum):
    """Lifecycle of a knowledge gap.

    - ``OPEN`` — emitted, not yet covered by later knowledge.
    - ``ANSWERED`` — a later document covers the missing predicate
      (the gap the operator fills becomes belief).
    - ``DISMISSED`` — operator ruled the gap not worth filling.
    """

    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class GapRecord:
    """One abstention-emitted knowledge gap (immutable evidence).

    Attributes:
        gap_id: Stable opaque identifier.
        subject_ref: Opaque vertical-defined subject the gap concerns
            (kernel-neutral; the pack maps its own notion onto it).
        run_id: Run / decision the abstention happened in.
        query: The query / intent the system could not satisfy.
        missing_predicate: What it did not know — structured where
            available, else the abstention rationale.  Read-time dedup
            keys on this.
        confidence: Model confidence at the abstention.
        threshold: Conformal threshold in force (``None`` when no
            failed calibration samples existed yet).
        wilson_lb: Wilson lower bound at the abstention — kept so the
            card can explain *why* #4 refused.
        as_of: Decision-time (inbound-event timestamp, §E1).
        dispatched_at: Dispatch wall-clock (§E1's second timeline).
        kg_snapshot_ref: Pointer to what Moat #5 *did* know at
            ``as_of`` (links gap ↔ belief).
        status: Lifecycle state; new records are ``OPEN``.
    """

    gap_id: str
    subject_ref: str
    run_id: str
    query: str
    missing_predicate: str
    confidence: float
    threshold: float | None
    wilson_lb: float
    as_of: datetime
    dispatched_at: datetime
    kg_snapshot_ref: str
    status: GapStatus = GapStatus.OPEN

    def __post_init__(self) -> None:
        for name in ("gap_id", "subject_ref", "run_id", "missing_predicate", "kg_snapshot_ref"):
            if not getattr(self, name):
                raise ValueError(f"{name} required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be tz-aware")
        if self.dispatched_at.tzinfo is None:
            raise ValueError("dispatched_at must be tz-aware")


def build_gap_record(
    decision: AbstentionDecision,
    *,
    subject_ref: str,
    run_id: str,
    query: str,
    as_of: datetime,
    dispatched_at: datetime,
    missing_predicate: str | None = None,
    gap_id: str | None = None,
) -> GapRecord | None:
    """Build the gap record for an abstention decision, or ``None``.

    Returns ``None`` unless ``decision.verdict`` is ``ABSTAIN`` —
    ``PROCEED`` emits nothing and ``INSUFFICIENT_DATA`` is a
    calibration shortfall, not missing knowledge.  ``missing_predicate``
    falls back to the gate's rationale when the caller has no
    structured predicate.
    """
    if decision.verdict is not AbstentionVerdict.ABSTAIN:
        return None
    return GapRecord(
        gap_id=gap_id or str(uuid4()),
        subject_ref=subject_ref,
        run_id=run_id,
        query=query,
        missing_predicate=missing_predicate or decision.rationale,
        confidence=decision.model_confidence,
        threshold=decision.conformal_threshold,
        wilson_lb=decision.wilson_lb,
        as_of=as_of,
        dispatched_at=dispatched_at,
        kg_snapshot_ref=kg_snapshot_ref(subject_ref, as_of),
    )


class GapStore(Protocol):
    """Per-event store for :class:`GapRecord` rows (append + lifecycle).

    Records are immutable evidence except for the ``status`` lifecycle
    column; nothing else is ever updated and nothing is deleted.
    """

    def record(self, gap: GapRecord) -> None:
        """Append one gap record."""
        ...

    def list_for(
        self,
        subject_ref: str,
        *,
        status: GapStatus | None = None,
        limit: int = 500,
    ) -> Sequence[GapRecord]:
        """Return gap records for ``subject_ref``, newest ``as_of`` first.

        ``status=None`` returns every lifecycle state.
        """
        ...

    def mark_status(
        self,
        *,
        subject_ref: str,
        missing_predicate: str,
        status: GapStatus,
    ) -> int:
        """Transition every record of one predicate; return rows changed.

        Lifecycle is predicate-grained on purpose: when a later document
        covers the missing predicate, *all* of its per-event history is
        answered, not just the latest occurrence.
        """
        ...


class InMemoryGapStore:
    """Per-process :class:`GapStore` for tests and OBSERVE-mode wiring."""

    def __init__(self) -> None:
        self._records: list[GapRecord] = []

    def record(self, gap: GapRecord) -> None:
        self._records.append(gap)

    def list_for(
        self,
        subject_ref: str,
        *,
        status: GapStatus | None = None,
        limit: int = 500,
    ) -> Sequence[GapRecord]:
        rows = [
            record
            for record in self._records
            if record.subject_ref == subject_ref and (status is None or record.status is status)
        ]
        rows.sort(key=lambda record: record.as_of, reverse=True)
        return tuple(rows[:limit])

    def mark_status(
        self,
        *,
        subject_ref: str,
        missing_predicate: str,
        status: GapStatus,
    ) -> int:
        changed = 0
        for index, record in enumerate(self._records):
            if (
                record.subject_ref == subject_ref
                and record.missing_predicate == missing_predicate
                and record.status is not status
            ):
                self._records[index] = replace(record, status=status)
                changed += 1
        return changed


def aggregate_gaps(records: Iterable[GapRecord]) -> list[dict[str, Any]]:
    """Dedup per-event records into one card per ``missing_predicate``.

    Read-API aggregation per ruling §E2: scalar fields (query,
    confidence, threshold, as_of, dispatched_at, kg_snapshot_ref,
    status) come from the **latest** occurrence; ``occurrences`` /
    ``first_seen_at`` / ``last_seen_at`` summarise the history;
    ``run_ids`` is a latest-first sample capped at
    :data:`AGGREGATE_RUN_ID_CAP`.  Output is ordered newest
    ``last_seen_at`` first.
    """
    groups: dict[str, list[GapRecord]] = {}
    for record in records:
        groups.setdefault(record.missing_predicate, []).append(record)
    cards: list[dict[str, Any]] = []
    for rows in groups.values():
        rows.sort(key=lambda record: record.as_of, reverse=True)
        latest = rows[0]
        cards.append(
            {
                **serialize_gap(latest),
                "occurrences": len(rows),
                "first_seen_at": rows[-1].as_of.isoformat(),
                "last_seen_at": latest.as_of.isoformat(),
                "run_ids": [record.run_id for record in rows[:AGGREGATE_RUN_ID_CAP]],
            }
        )
    cards.sort(key=operator.itemgetter("last_seen_at"), reverse=True)
    return cards


def serialize_gap(record: GapRecord) -> dict[str, Any]:
    """Wire shape of one gap record (kernel-neutral field names)."""
    return {
        "gap_id": record.gap_id,
        "subject_ref": record.subject_ref,
        "run_id": record.run_id,
        "query": record.query,
        "missing_predicate": record.missing_predicate,
        "confidence": record.confidence,
        "threshold": record.threshold,
        "wilson_lb": record.wilson_lb,
        "as_of": record.as_of.isoformat(),
        "dispatched_at": record.dispatched_at.isoformat(),
        "kg_snapshot_ref": record.kg_snapshot_ref,
        "status": record.status.value,
    }
