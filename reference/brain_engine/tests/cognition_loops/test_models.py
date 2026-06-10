"""Invariants of cognition-loop value objects."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from brain_engine.cognition_loops.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
    ResolvedDecision,
)


def _ace(**overrides: object) -> AceCycle:
    base: dict[str, object] = {
        "cycle_id": "c1",
        "target": "t",
        "candidate": "x",
        "reflector_verdict": AceVerdict.APPROVE,
        "curator_applied": True,
        "rationale": "ok",
    }
    base.update(overrides)
    return AceCycle(**base)  # type: ignore[arg-type]


def test_ace_curator_applied_requires_approve() -> None:
    """Curator can only write when Reflector approved."""
    with pytest.raises(ValueError, match="curator_applied"):
        _ace(
            reflector_verdict=AceVerdict.REJECT,
            curator_applied=True,
        )


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"cycle_id": ""}, "cycle_id"),
        ({"target": ""}, "target"),
        ({"rationale": ""}, "rationale"),
    ],
    ids=["empty_id", "empty_target", "empty_rationale"],
)
def test_ace_string_field_validation(
    override: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _ace(**override)


def test_memory_op_reward_finiteness() -> None:
    """Inf / NaN rewards are rejected."""
    with pytest.raises(ValueError, match="reward"):
        MemoryOp(
            op_id="o",
            target="t",
            kind=MemoryOpKind.ADD,
            rationale="r",
            reward=math.inf,
        )


def test_memory_op_reward_optional() -> None:
    """``reward=None`` is permitted."""
    op = MemoryOp(
        op_id="o",
        target="t",
        kind=MemoryOpKind.ADD,
        rationale="r",
    )
    assert op.reward is None


def test_resolved_decision_requires_aware_time() -> None:
    """Naive timestamps are rejected."""
    ace = _ace()
    op = MemoryOp(
        op_id="o",
        target="t",
        kind=MemoryOpKind.ADD,
        rationale="r",
    )
    with pytest.raises(ValueError, match="evaluated_at"):
        ResolvedDecision(
            target="t",
            applied_kind=MemoryOpKind.ADD,
            reason="ok",
            ace_cycle=ace,
            memory_op=op,
            evaluated_at=datetime(2026, 5, 10),
        )


def test_six_memory_op_kinds_ship() -> None:
    """The Memory-R1 enum mirrors the paper's six op classes."""
    assert {k.value for k in MemoryOpKind} == {
        "add",
        "update",
        "delete",
        "noop",
        "summarize",
        "retrieve",
    }


def test_three_ace_verdicts() -> None:
    """ACE Reflector enum: approve / modify / reject."""
    assert {v.value for v in AceVerdict} == {
        "approve",
        "modify",
        "reject",
    }


def test_resolved_decision_has_aware_default_time() -> None:
    """Caller-supplied UTC datetime round-trips intact."""
    ace = _ace()
    op = MemoryOp(
        op_id="o",
        target="t",
        kind=MemoryOpKind.ADD,
        rationale="r",
    )
    moment = datetime(2026, 5, 10, tzinfo=timezone.utc)
    decision = ResolvedDecision(
        target="t",
        applied_kind=MemoryOpKind.ADD,
        reason="ok",
        ace_cycle=ace,
        memory_op=op,
        evaluated_at=moment,
    )
    assert decision.evaluated_at == moment
