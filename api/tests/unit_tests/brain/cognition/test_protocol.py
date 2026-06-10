"""Conflict-resolution rules of :class:`InteractionProtocol`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.cognition.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
)
from core.brain.cognition.protocol import (
    InteractionProtocol,
)


def _ace(
    *,
    target: str = "t",
    verdict: AceVerdict = AceVerdict.APPROVE,
    applied: bool = True,
) -> AceCycle:
    return AceCycle(
        cycle_id="c1",
        target=target,
        candidate="cand",
        reflector_verdict=verdict,
        curator_applied=applied,
        rationale="ok",
    )


def _op(
    *,
    target: str = "t",
    kind: MemoryOpKind = MemoryOpKind.ADD,
) -> MemoryOp:
    return MemoryOp(
        op_id="o1",
        target=target,
        kind=kind,
        rationale="ok",
    )


@pytest.fixture
def protocol() -> InteractionProtocol:
    return InteractionProtocol()


def test_agreement_runs_the_op(
    protocol: InteractionProtocol,
) -> None:
    """ACE+Memory-R1 both ADD → ADD runs."""
    decision = protocol.resolve(
        ace_cycle=_ace(),
        memory_op=_op(kind=MemoryOpKind.ADD),
    )
    assert decision.applied_kind is MemoryOpKind.ADD
    assert "agreement" in decision.reason


def test_memory_noop_vetoes_ace(
    protocol: InteractionProtocol,
) -> None:
    """Memory-R1 NOOP overrides any ACE write."""
    decision = protocol.resolve(
        ace_cycle=_ace(),
        memory_op=_op(kind=MemoryOpKind.NOOP),
    )
    assert decision.applied_kind is MemoryOpKind.NOOP
    assert "veto" in decision.reason


def test_ace_rejected_falls_to_noop(
    protocol: InteractionProtocol,
) -> None:
    """Reflector rejecting ACE → NOOP regardless of Memory-R1."""
    decision = protocol.resolve(
        ace_cycle=_ace(
            verdict=AceVerdict.REJECT,
            applied=False,
        ),
        memory_op=_op(kind=MemoryOpKind.ADD),
    )
    assert decision.applied_kind is MemoryOpKind.NOOP
    assert "Curator did not apply" in decision.reason


def test_add_vs_delete_defers(
    protocol: InteractionProtocol,
) -> None:
    """ACE ADD + Memory-R1 DELETE → NOOP, defer to night."""
    decision = protocol.resolve(
        ace_cycle=_ace(),
        memory_op=_op(kind=MemoryOpKind.DELETE),
    )
    assert decision.applied_kind is MemoryOpKind.NOOP
    assert "defer to nightly consolidation" in decision.reason


def test_read_only_op_runs(
    protocol: InteractionProtocol,
) -> None:
    """RETRIEVE / SUMMARIZE never conflict; they always run."""
    for kind in (MemoryOpKind.RETRIEVE, MemoryOpKind.SUMMARIZE):
        decision = protocol.resolve(
            ace_cycle=_ace(),
            memory_op=_op(kind=kind),
        )
        assert decision.applied_kind is kind


def test_target_mismatch_raises(
    protocol: InteractionProtocol,
) -> None:
    """Resolving a mismatched-target pair raises."""
    with pytest.raises(ValueError, match="target mismatch"):
        protocol.resolve(
            ace_cycle=_ace(target="a"),
            memory_op=_op(target="b"),
        )


def test_naive_at_rejected(
    protocol: InteractionProtocol,
) -> None:
    """Caller-supplied naive ``at`` is rejected."""
    with pytest.raises(ValueError, match="tz-aware"):
        protocol.resolve(
            ace_cycle=_ace(),
            memory_op=_op(),
            at=datetime(2026, 5, 10),
        )


def test_default_at_is_utc_aware(
    protocol: InteractionProtocol,
) -> None:
    """The default clock yields a tz-aware UTC instant."""
    decision = protocol.resolve(
        ace_cycle=_ace(),
        memory_op=_op(),
    )
    assert decision.evaluated_at.tzinfo is UTC
