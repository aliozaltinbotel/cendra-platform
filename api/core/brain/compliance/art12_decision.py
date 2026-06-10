"""EU AI Act Art. 12 record-keeping decision schema.

Article 12 of the EU AI Act (Reg 2024/1689) requires a high-risk
AI system to "automatically record events ('logs') over the
lifetime" sufficient for "the traceability of the system's
functioning" and "the post-market monitoring system".  This
module defines the decision-record shape Brain Engine writes for
every action that crosses a guardrail / autonomy gate, suitable
for batch export to the regulator.

The record is built on top of :mod:`core.brain.evidence.audit_pack`'s
chained BLAKE2B integrity primitives — each Art. 12 record carries
a digest of the previous record so backdated insertion breaks the
chain.

Fields that the record promises:

- ``decision_id`` — opaque unique identifier.
- ``occurred_at`` — tz-aware UTC instant.
- ``property_id`` / ``owner_id`` — scope.
- ``action_kind`` — canonical
  :class:`core.brain.cards.action_kinds.CardActionKind` value.
- ``autonomy_tier`` — the
  :class:`core.brain.certificates.AutonomyTier` the action ran
  at (Moat #3).  ``None`` if certificates are not yet wired.
- ``planner_style`` — the
  :class:`core.brain.planner.PlannerStyleId` that shaped the
  decision (Moat #4).  ``None`` if planner not consulted.
- ``handler_solver`` — which solver actually produced the action
  ("llm" / "utility" / "smt" / "deterministic" / "hitl") — see
  GR00T P2 in roadmap-with-groot.md.
- ``rationale`` — one-line plain-English explanation.
- ``provenance_digest`` — BLAKE2B hex digest of the canonical
  evidence bundle (rules / cases / blockers / facts that drove
  the decision).
- ``prev_digest`` — chain link.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

__all__ = [
    "ART12_GENESIS_DIGEST",
    "Art12Decision",
    "HandlerSolver",
    "canonical_record",
    "chained_digest",
]


ART12_GENESIS_DIGEST: Final[str] = "0" * 64


class HandlerSolver(StrEnum):
    """Per-component solver that produced the action.

    Maps to GR00T P2 decoupled WBC (roadmap-with-groot.md): each
    sub-action delegates to its preferred solver, and the audit
    log records *which* method ran so the regulator can verify
    the action class went through the right solver.
    """

    LLM = "llm"
    UTILITY = "utility"
    SMT = "smt"
    DETERMINISTIC = "deterministic"
    HITL = "hitl"


@dataclass(frozen=True, slots=True)
class Art12Decision:
    """One Art. 12 audit-log record.

    Construction validates that ``occurred_at`` is tz-aware so a
    regulator's timeline is unambiguous.  Mutation surprises are
    impossible — the record is a frozen dataclass.
    """

    decision_id: str
    occurred_at: datetime
    property_id: str
    owner_id: str
    action_kind: str
    handler_solver: HandlerSolver
    rationale: str
    provenance_digest: str
    autonomy_tier: str | None = None
    planner_style: str | None = None
    prev_digest: str = ART12_GENESIS_DIGEST
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fail-fast validation."""
        if not self.decision_id:
            raise ValueError("decision_id required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be tz-aware")
        if not self.property_id:
            raise ValueError("property_id required")
        if not self.owner_id:
            raise ValueError("owner_id required")
        if not self.rationale:
            raise ValueError("rationale required")
        if not self.provenance_digest:
            raise ValueError("provenance_digest required")
        if len(self.prev_digest) != 64:
            raise ValueError("prev_digest must be 64 hex chars (BLAKE2B-256)")

    def chained_digest(self) -> str:
        """Return the BLAKE2B-256 digest of this record + predecessor."""
        return chained_digest(self)


def canonical_record(decision: Art12Decision) -> bytes:
    """Return the canonical JSON bytes a digest signs over.

    Sorted keys + comma-colon separators give a deterministic
    encoding identical across Python versions and dict insertion
    orders.
    """
    payload: dict[str, object] = asdict(decision)
    payload["occurred_at"] = decision.occurred_at.isoformat()
    payload["action_kind"] = decision.action_kind
    payload["handler_solver"] = decision.handler_solver.value
    payload["extra"] = dict(decision.extra)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def chained_digest(decision: Art12Decision) -> str:
    """Return the 64-char hex BLAKE2B-256 digest of ``decision``."""
    return hashlib.blake2b(
        canonical_record(decision),
        digest_size=32,
    ).hexdigest()
