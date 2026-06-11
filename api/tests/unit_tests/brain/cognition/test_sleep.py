"""Behaviour of the nightly :func:`summarise_decisions`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.cognition.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
    ResolvedDecision,
)
from core.brain.cognition.sleep import (
    MIN_DECISIONS_FOR_PLAYBOOK_BUMP,
    summarise_decisions,
)


def _decision(
    *,
    target: str,
    applied_kind: MemoryOpKind,
    reason: str = "ok",
) -> ResolvedDecision:
    ace = AceCycle(
        cycle_id="c",
        target=target,
        candidate="x",
        reflector_verdict=AceVerdict.APPROVE,
        curator_applied=True,
        rationale="ok",
    )
    op = MemoryOp(
        op_id="o",
        target=target,
        kind=applied_kind,
        rationale="ok",
    )
    return ResolvedDecision(
        target=target,
        applied_kind=applied_kind,
        reason=reason,
        ace_cycle=ace,
        memory_op=op,
        evaluated_at=datetime(2026, 5, 10, tzinfo=UTC),
    )


def test_empty_decisions_zero_counts() -> None:
    """No decisions → empty kind_counts + no playbook bump."""
    period = datetime(2026, 5, 10, tzinfo=UTC)
    report = summarise_decisions(
        (),
        period_start=period,
        period_end=period,
    )
    assert report.kind_counts == {}
    assert report.unresolved_targets == ()
    assert report.playbook_bump_recommended is False


def test_kind_counts_aggregate() -> None:
    """Counts accumulate by ``applied_kind``."""
    period = datetime(2026, 5, 10, tzinfo=UTC)
    decisions = [
        _decision(
            target=f"t{i}",
            applied_kind=MemoryOpKind.ADD,
        )
        for i in range(3)
    ] + [
        _decision(target="t9", applied_kind=MemoryOpKind.NOOP),
    ]
    report = summarise_decisions(
        decisions,
        period_start=period,
        period_end=period,
    )
    assert report.kind_counts[MemoryOpKind.ADD] == 3
    assert report.kind_counts[MemoryOpKind.NOOP] == 1


def test_unresolved_targets_collected() -> None:
    """Decisions with the defer-reason flag the target."""
    period = datetime(2026, 5, 10, tzinfo=UTC)
    decisions = (
        _decision(
            target="contested",
            applied_kind=MemoryOpKind.NOOP,
            reason=("ACE ADD vs Memory-R1 DELETE; defer to nightly consolidation"),
        ),
    )
    report = summarise_decisions(
        decisions,
        period_start=period,
        period_end=period,
    )
    assert report.unresolved_targets == ("contested",)
    assert report.playbook_bump_recommended is False


def test_playbook_bump_when_threshold_met() -> None:
    """Enough ADDs without unresolved → bump recommended."""
    period = datetime(2026, 5, 10, tzinfo=UTC)
    decisions = [
        _decision(target=f"t{i}", applied_kind=MemoryOpKind.ADD) for i in range(MIN_DECISIONS_FOR_PLAYBOOK_BUMP)
    ]
    report = summarise_decisions(
        decisions,
        period_start=period,
        period_end=period,
    )
    assert report.playbook_bump_recommended is True


def test_period_validation() -> None:
    """``period_end`` < ``period_start`` is rejected."""
    with pytest.raises(ValueError, match="period_end"):
        summarise_decisions(
            (),
            period_start=datetime(2026, 5, 11, tzinfo=UTC),
            period_end=datetime(2026, 5, 10, tzinfo=UTC),
        )
