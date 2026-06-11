"""Shadow-verdict field shape + KPI derivability (CEN-33).

Observe-posture only: the block records *what enforce would have done*
and the scored-decision-mix KPI must be derivable from ledger rows
alone, with pre-change rows treated as ``unknown``.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from core.brain.gates import GateName, GateOutcome, PipelineDecision, PipelineVerdict
from core.brain.patterns.shadow_verdict import (
    SHADOW_KEY,
    UNKNOWN,
    WOULD_ABSTAIN,
    WOULD_ACT,
    read_shadow_verdict,
    serialize_shadow_verdict,
    verdict_of,
)

_AT = datetime(2026, 6, 11, tzinfo=UTC)


def _decision(verdict: PipelineVerdict, trace: tuple[GateOutcome, ...], rationale: str) -> PipelineDecision:
    return PipelineDecision(verdict=verdict, rationale=rationale, gate_trace=trace, evaluated_at=_AT)


def _proceed() -> PipelineDecision:
    trace = (
        GateOutcome(gate=GateName.ABSTENTION, verdict="proceed", rationale="wilson_lb=0.80 >= threshold"),
        GateOutcome(gate=GateName.RISK, verdict="proceed", rationale="cvar within budget"),
    )
    return _decision(PipelineVerdict.PROCEED, trace, "all gates passed")


def _defer_abstention() -> PipelineDecision:
    trace = (GateOutcome(gate=GateName.ABSTENTION, verdict="abstain", rationale="wilson_lb=0.45 < threshold=0.60"),)
    return _decision(PipelineVerdict.DEFER, trace, "wilson_lb=0.45 < threshold=0.60")


def _blocked_compliance() -> PipelineDecision:
    trace = (GateOutcome(gate=GateName.COMPLIANCE, verdict="blocked", rationale="never-AI denylist hit"),)
    return _decision(PipelineVerdict.BLOCKED, trace, "never-AI denylist hit")


def test_proceed_serialises_as_would_act_with_no_refusing_gate():
    block = serialize_shadow_verdict(_proceed(), model_confidence=0.9)
    assert block["schema"] == 1
    assert block["verdict"] == WOULD_ACT
    assert block["pipeline_verdict"] == "proceed"
    assert block["refusing_gate"] is None
    assert block["confidence"] == 0.9
    assert block["evaluated_at"] == _AT.isoformat()
    assert [row["gate"] for row in block["gate_trace"]] == ["abstention", "risk"]


def test_defer_serialises_as_would_abstain_naming_the_refusing_gate():
    block = serialize_shadow_verdict(_defer_abstention(), model_confidence=1.0)
    assert block["verdict"] == WOULD_ABSTAIN
    assert block["pipeline_verdict"] == "defer"
    assert block["refusing_gate"] == "abstention"


def test_blocked_serialises_as_would_abstain_with_blocking_gate():
    block = serialize_shadow_verdict(_blocked_compliance(), model_confidence=1.0)
    assert block["verdict"] == WOULD_ABSTAIN
    assert block["pipeline_verdict"] == "blocked"
    assert block["refusing_gate"] == "compliance"


def test_reader_returns_none_for_pre_change_rows():
    # backward compatible: legacy rows carry only source/tool_id
    assert read_shadow_verdict({"source": "t7_capture", "tool_id": "send_message"}) is None
    assert read_shadow_verdict({}) is None
    assert read_shadow_verdict(None) is None
    assert verdict_of({"source": "t7_capture", "tool_id": "x"}) == UNKNOWN


def test_verdict_of_buckets_a_recorded_block():
    ov = {"source": "t7_capture", "tool_id": "x", SHADOW_KEY: serialize_shadow_verdict(_proceed(), model_confidence=1.0)}
    assert verdict_of(ov) == WOULD_ACT
    assert read_shadow_verdict(ov)["pipeline_verdict"] == "proceed"


def test_scored_decision_mix_is_derivable_from_ledger_rows_alone():
    # A fixture of orchestrator_verdict JSON blobs as they would be read
    # straight off DecisionCase rows — no gate re-run, no side tables.
    ledger = [
        {"source": "t7_capture", "tool_id": "a", SHADOW_KEY: serialize_shadow_verdict(_proceed(), model_confidence=1.0)},
        {"source": "t7_capture", "tool_id": "b", SHADOW_KEY: serialize_shadow_verdict(_proceed(), model_confidence=1.0)},
        {
            "source": "t7_capture",
            "tool_id": "c",
            SHADOW_KEY: serialize_shadow_verdict(_defer_abstention(), model_confidence=1.0),
        },
        {
            "source": "t7_capture",
            "tool_id": "d",
            SHADOW_KEY: serialize_shadow_verdict(_blocked_compliance(), model_confidence=1.0),
        },
        # a pre-change row → unknown, never silently counted as act/abstain
        {"source": "t7_capture", "tool_id": "legacy"},
    ]

    mix = Counter(verdict_of(row) for row in ledger)
    assert mix[WOULD_ACT] == 2
    assert mix[WOULD_ABSTAIN] == 2
    assert mix[UNKNOWN] == 1

    # the "by refusing gate" dimension CEN-32 reads is in the same block
    by_gate = Counter(
        read_shadow_verdict(row)["refusing_gate"]
        for row in ledger
        if verdict_of(row) == WOULD_ABSTAIN
    )
    assert by_gate == Counter({"abstention": 1, "compliance": 1})
