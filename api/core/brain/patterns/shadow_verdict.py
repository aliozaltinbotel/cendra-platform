"""Shadow-verdict field shape recorded in T7 DecisionCase capture (CEN-33).

Additive kernel module: it carries no touchpoint marker (those tag edited
upstream lines inside registered touchpoints only).  The T7 hook lives in
``core/callback_handler/cendra_decision_capture.py``; this file just
defines the field shape it persists.

**Observe posture only.**  Under ``BRAIN_GATES_MODE=observe`` the gate
chain runs but never refuses a dispatch (see ``runtime_gateway``).  This
module captures *what enforce would have done* — would-act vs
would-abstain, the refusing gate (if any), and the confidence the chain
consulted — so the scored-decision-mix KPI is derivable from the ledger
alone.  Nothing here computes or persists anything that enables enforce;
it only serialises a verdict the chain already produced.

The block is stored under ``DecisionCase.orchestrator_verdict[SHADOW_KEY]``
(a JSONB column — no schema migration).  Readers (the accrual-metrics
endpoint, CEN-32) call :func:`read_shadow_verdict` / :func:`verdict_of`
and treat a missing/empty block as :data:`UNKNOWN`, so pre-change rows
stay readable.

Field shape (``schema=1``)::

    {
        "schema": 1,
        "verdict": "would_act" | "would_abstain",   # the headline binary
        "pipeline_verdict": "proceed" | "defer" | "blocked",
        "refusing_gate": "compliance" | "certificate"
                       | "abstention" | "risk" | None,
        "confidence": <float>,        # gate-chain confidence consulted
        "rationale": "<terminal rationale>",
        "gate_trace": [{"gate", "verdict", "rationale"}, ...],
        "evaluated_at": "<iso8601>",
    }

``verdict`` is the KPI bucket: ``would_act`` iff the chain reached
``PROCEED``; every non-proceed (defer *or* blocked) is ``would_abstain``
(``pipeline_verdict`` + ``refusing_gate`` preserve the distinction).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "RECEIPT_REF_KEY",
    "SHADOW_KEY",
    "SHADOW_VERDICT_SCHEMA",
    "UNKNOWN",
    "WOULD_ABSTAIN",
    "WOULD_ACT",
    "read_shadow_verdict",
    "serialize_shadow_verdict",
    "verdict_of",
]

SHADOW_VERDICT_SCHEMA: Final[int] = 1
SHADOW_KEY: Final[str] = "shadow"

RECEIPT_REF_KEY: Final[str] = "receipt"
"""Optional additive key on the shadow block (CEN-81; schema stays 1).

When the gate chain reached PROCEED and an Art. 12 receipt was emitted,
the runtime gateway attaches ``{"decision_id", "record_digest",
"signed"}`` under this key so the T7 capture can stitch the dispatch
outcome / ``case_id`` back onto the persisted receipt row.  Absent on
non-PROCEED verdicts, pre-CEN-81 rows, and failed emissions — readers
treat absence as "no receipt"."""

WOULD_ACT: Final[str] = "would_act"
WOULD_ABSTAIN: Final[str] = "would_abstain"
UNKNOWN: Final[str] = "unknown"


def serialize_shadow_verdict(decision: Any, *, model_confidence: float) -> dict[str, Any]:
    """Serialise one gate-chain :class:`PipelineDecision` to the shadow block.

    Pure transform of a decision the chain already computed — it never
    re-runs a gate.  ``refusing_gate`` is the gate that short-circuited
    the chain (the terminal trace row) when the verdict is not
    ``PROCEED``; ``None`` on a would-act verdict.

    The ``core.brain.gates`` import is local so readers
    (:func:`read_shadow_verdict`, :func:`verdict_of`) stay free of the
    gate-chain dependency graph.
    """
    from core.brain.gates import PipelineVerdict

    proceeded = decision.verdict is PipelineVerdict.PROCEED
    refusing_gate: str | None = None
    if not proceeded and decision.gate_trace:
        refusing_gate = decision.gate_trace[-1].gate.value
    return {
        "schema": SHADOW_VERDICT_SCHEMA,
        "verdict": WOULD_ACT if proceeded else WOULD_ABSTAIN,
        "pipeline_verdict": decision.verdict.value,
        "refusing_gate": refusing_gate,
        "confidence": float(model_confidence),
        "rationale": decision.rationale,
        "gate_trace": [
            {"gate": row.gate.value, "verdict": row.verdict, "rationale": row.rationale}
            for row in decision.gate_trace
        ],
        "evaluated_at": decision.evaluated_at.isoformat(),
    }


def read_shadow_verdict(orchestrator_verdict: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the stored shadow block, or ``None`` when absent (→ unknown).

    Backward compatible: pre-change rows carry only ``source``/``tool_id``
    and yield ``None``.
    """
    if not orchestrator_verdict:
        return None
    shadow = orchestrator_verdict.get(SHADOW_KEY)
    if isinstance(shadow, Mapping):
        return dict(shadow)
    return None


def verdict_of(orchestrator_verdict: Mapping[str, Any] | None) -> str:
    """KPI bucket for one ledger row: ``would_act`` / ``would_abstain`` / ``unknown``.

    This makes the scored-decision-mix derivable from ledger rows alone:
    bucket every ``DecisionCase.orchestrator_verdict`` through this function.
    """
    shadow = read_shadow_verdict(orchestrator_verdict)
    if shadow is None:
        return UNKNOWN
    verdict = shadow.get("verdict")
    if verdict in (WOULD_ACT, WOULD_ABSTAIN):
        return verdict
    return UNKNOWN
