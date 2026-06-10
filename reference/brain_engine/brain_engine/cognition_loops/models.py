"""Value objects for the ACE + Memory-R1 + sleep loops (Moat #14)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


__all__ = [
    "AceCycle",
    "AceVerdict",
    "MemoryOp",
    "MemoryOpKind",
    "ResolvedDecision",
]


class AceVerdict(StrEnum):
    """Reflector outcome on one Generator candidate."""

    APPROVE = "approve"
    MODIFY = "modify"
    REJECT = "reject"


class MemoryOpKind(StrEnum):
    """Six Memory-R1 op classes (Yan et al. arXiv:2508.19828)."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"
    SUMMARIZE = "summarize"
    RETRIEVE = "retrieve"


@dataclass(frozen=True, slots=True)
class AceCycle:
    """One Generator → Reflector → Curator cycle.

    Attributes:
        cycle_id: Stable opaque identifier.
        target: Free-form key the cycle proposes a change to
            (e.g. ``"playbook:noise_complaint"``).
        candidate: The textual / structured candidate the
            Generator emitted.  Caller-defined shape (typically
            JSON-safe).
        reflector_verdict: Reflector's decision.
        curator_applied: ``True`` when the Curator wrote the
            candidate (only possible when ``reflector_verdict
            is APPROVE``).
        rationale: One-line plain-English summary; consumed by
            the audit log so the regulator can replay the cycle.
    """

    cycle_id: str
    target: str
    candidate: object
    reflector_verdict: AceVerdict
    curator_applied: bool
    rationale: str

    def __post_init__(self) -> None:
        if not self.cycle_id:
            raise ValueError("cycle_id required")
        if not self.target:
            raise ValueError("target required")
        if not self.rationale:
            raise ValueError("rationale required")
        if (
            self.curator_applied
            and self.reflector_verdict is not AceVerdict.APPROVE
        ):
            raise ValueError(
                "curator_applied=True only allowed when "
                "reflector_verdict is APPROVE"
            )


@dataclass(frozen=True, slots=True)
class MemoryOp:
    """One per-step Memory-R1 vote.

    Attributes:
        op_id: Stable opaque identifier.
        target: Same key the ACE cycle pointed at; the protocol
            consults the kind tag to decide whether to ratify or
            override the Curator's write.
        kind: One of :class:`MemoryOpKind`.
        rationale: One-line plain-English summary.
        reward: Optional caller-supplied reward signal (for the
            offline GRPO trainer).  ``None`` when the system is
            not currently collecting rewards.
        extra: Free-form metadata for downstream consumers.
    """

    op_id: str
    target: str
    kind: MemoryOpKind
    rationale: str
    reward: float | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.op_id:
            raise ValueError("op_id required")
        if not self.target:
            raise ValueError("target required")
        if not self.rationale:
            raise ValueError("rationale required")
        if self.reward is not None:
            if self.reward != self.reward or self.reward in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError("reward must be finite")


@dataclass(frozen=True, slots=True)
class ResolvedDecision:
    """Output of one ACE / Memory-R1 conflict resolution.

    Attributes:
        target: Key the loops disagreed about.
        applied_kind: The op kind that actually ran (might differ
            from both inputs when the protocol abstains).
        reason: Why the protocol picked this resolution.
        ace_cycle: The originating :class:`AceCycle`.
        memory_op: The :class:`MemoryOp` that voted on it.
        evaluated_at: tz-aware UTC instant the protocol decided.
    """

    target: str
    applied_kind: MemoryOpKind
    reason: str
    ace_cycle: AceCycle
    memory_op: MemoryOp
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not self.reason:
            raise ValueError("reason required")
