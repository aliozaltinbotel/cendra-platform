"""ACE / Memory-R1 conflict-resolution protocol (Moat #14 v0.1).

Both loops vote on the same playbook key.  The protocol is the
small state machine that decides what *actually* runs when the
votes disagree.  The rule is conservative-first:

    * If both votes agree → that op runs.
    * If Memory-R1 says NOOP → run NOOP regardless of ACE
      (Memory-R1 has explicit veto power on writes).
    * If Memory-R1 says DELETE while ACE Curator wrote ADD →
      run NOOP (the day's evidence is split; defer to the
      nightly consolidation worker).
    * If ACE Curator did *not* apply (Reflector rejected) →
      run NOOP regardless of Memory-R1.
    * If Memory-R1 says RETRIEVE / SUMMARIZE → run that op
      (read-only; never conflicts with ACE writes).
    * Otherwise → run the Memory-R1 op (it carries the RL
      reward signal).

Every resolution emits a :class:`ResolvedDecision` carrying both
inputs and a one-line ``reason`` so the audit log records *why*
the protocol picked the resolution it did.

Defensibility (Moat #14): the patent claim is on the *protocol*
— the interaction rules between three already-published loops
(ACE arXiv:2510.04618; Memory-R1 arXiv:2508.19828; Letta sleep-
time arXiv:2504.13171).  Each loop has prior art in isolation;
their integrated runtime with conflict resolution does not.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.brain.cognition.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
    ResolvedDecision,
)

__all__ = ["InteractionProtocol"]


logger = logging.getLogger(__name__)


_READ_ONLY_OPS = frozenset(
    {
        MemoryOpKind.RETRIEVE,
        MemoryOpKind.SUMMARIZE,
    }
)


class InteractionProtocol:
    """Resolve one ACE / Memory-R1 disagreement into one op."""

    def resolve(
        self,
        *,
        ace_cycle: AceCycle,
        memory_op: MemoryOp,
        at: datetime | None = None,
    ) -> ResolvedDecision:
        """Run the conflict-resolution rule and return the verdict."""
        if ace_cycle.target != memory_op.target:
            raise ValueError(f"target mismatch: ace={ace_cycle.target!r} memory={memory_op.target!r}")
        moment = at or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("`at` must be tz-aware when provided")
        applied_kind, reason = self._decide(
            ace_cycle=ace_cycle,
            memory_op=memory_op,
        )
        logger.info(
            "cognition.resolved target=%s applied=%s ace=%s memory=%s",
            ace_cycle.target,
            applied_kind.value,
            ace_cycle.reflector_verdict.value,
            memory_op.kind.value,
        )
        return ResolvedDecision(
            target=ace_cycle.target,
            applied_kind=applied_kind,
            reason=reason,
            ace_cycle=ace_cycle,
            memory_op=memory_op,
            evaluated_at=moment,
        )

    # ── internal rules ─────────────────────────────────────── #

    def _decide(
        self,
        *,
        ace_cycle: AceCycle,
        memory_op: MemoryOp,
    ) -> tuple[MemoryOpKind, str]:
        if memory_op.kind in _READ_ONLY_OPS:
            return (
                memory_op.kind,
                f"read-only memory op {memory_op.kind.value}",
            )
        if memory_op.kind is MemoryOpKind.NOOP:
            return (
                MemoryOpKind.NOOP,
                "Memory-R1 NOOP veto",
            )
        if not ace_cycle.curator_applied:
            return (
                MemoryOpKind.NOOP,
                "ACE Curator did not apply; no write attempted",
            )
        ace_kind = self._infer_ace_kind(ace_cycle)
        if ace_kind == memory_op.kind:
            return (
                ace_kind,
                f"agreement on {ace_kind.value}",
            )
        if ace_kind is MemoryOpKind.ADD and memory_op.kind is MemoryOpKind.DELETE:
            return (
                MemoryOpKind.NOOP,
                "ACE ADD vs Memory-R1 DELETE; defer to nightly consolidation",
            )
        return (
            memory_op.kind,
            (f"divergence ace={ace_kind.value} memory={memory_op.kind.value}; Memory-R1 carries reward signal"),
        )

    @staticmethod
    def _infer_ace_kind(cycle: AceCycle) -> MemoryOpKind:
        """Map ACE Curator outcome to a :class:`MemoryOpKind`.

        v0.1 assumes APPROVE+applied means ADD.  Future versions
        may expose explicit op-kind on AceCycle for finer-grained
        UPDATE / DELETE intent.
        """
        if cycle.reflector_verdict is AceVerdict.APPROVE and cycle.curator_applied:
            return MemoryOpKind.ADD
        return MemoryOpKind.NOOP
