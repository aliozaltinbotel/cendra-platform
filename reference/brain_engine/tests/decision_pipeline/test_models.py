"""Invariants of decision-pipeline value objects."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.compliance.art12_decision import (
    Art12Decision,
    HandlerSolver,
)
from brain_engine.decision_pipeline.models import (
    GateName,
    GateOutcome,
    PipelineDecision,
    PipelineRequest,
    PipelineVerdict,
)


def _request(**overrides: object) -> PipelineRequest:
    base: dict[str, object] = {
        "decision_id": "d1",
        "property_id": "p1",
        "owner_id": "o1",
        "action_kind": CardActionKind.SEND_MESSAGE,
        "rationale": "ok",
        "provenance_digest": "a" * 64,
        "tool_id": "tool",
        "model_confidence": 0.9,
        "handler_solver": "llm",
    }
    base.update(overrides)
    return PipelineRequest(**base)  # type: ignore[arg-type]


def _audit() -> Art12Decision:
    return Art12Decision(
        decision_id="d1",
        occurred_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        handler_solver=HandlerSolver.LLM,
        rationale="ok",
        provenance_digest="a" * 64,
    )


def test_pipeline_verdict_three_values() -> None:
    assert {v.value for v in PipelineVerdict} == {
        "proceed",
        "defer",
        "blocked",
    }


def test_gate_name_values() -> None:
    assert {g.value for g in GateName} == {
        "compliance",
        "abstention",
        "risk",
        "certificate",
    }


def test_gate_outcome_validation() -> None:
    """Empty rationale / verdict raises."""
    with pytest.raises(ValueError, match="verdict"):
        GateOutcome(
            gate=GateName.COMPLIANCE,
            verdict="",
            rationale="x",
        )
    with pytest.raises(ValueError, match="rationale"):
        GateOutcome(
            gate=GateName.COMPLIANCE,
            verdict="ok",
            rationale="",
        )


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"decision_id": ""}, "decision_id"),
        ({"property_id": ""}, "property_id"),
        ({"owner_id": ""}, "owner_id"),
        ({"rationale": ""}, "rationale"),
        ({"provenance_digest": ""}, "provenance_digest"),
        ({"tool_id": ""}, "tool_id"),
        ({"handler_solver": ""}, "handler_solver"),
        ({"model_confidence": -0.1}, "model_confidence"),
        ({"model_confidence": 1.1}, "model_confidence"),
    ],
    ids=[
        "empty_decision_id",
        "empty_property_id",
        "empty_owner_id",
        "empty_rationale",
        "empty_provenance",
        "empty_tool_id",
        "empty_handler_solver",
        "neg_confidence",
        "high_confidence",
    ],
)
def test_pipeline_request_validation(
    override: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _request(**override)


def test_decision_proceed_requires_audit_record() -> None:
    """PROCEED without audit_record raises fail-fast."""
    with pytest.raises(ValueError, match="PROCEED verdict"):
        PipelineDecision(
            verdict=PipelineVerdict.PROCEED,
            rationale="ok",
            gate_trace=(),
            evaluated_at=datetime(
                2026, 5, 11, tzinfo=timezone.utc,
            ),
            audit_record=None,
        )


def test_decision_defer_must_not_carry_audit() -> None:
    """DEFER with audit_record raises fail-fast."""
    with pytest.raises(ValueError, match="non-PROCEED"):
        PipelineDecision(
            verdict=PipelineVerdict.DEFER,
            rationale="ok",
            gate_trace=(),
            evaluated_at=datetime(
                2026, 5, 11, tzinfo=timezone.utc,
            ),
            audit_record=_audit(),
        )


def test_decision_naive_evaluated_at_rejected() -> None:
    with pytest.raises(ValueError, match="evaluated_at"):
        PipelineDecision(
            verdict=PipelineVerdict.BLOCKED,
            rationale="ok",
            gate_trace=(),
            evaluated_at=datetime(2026, 5, 11),
        )
