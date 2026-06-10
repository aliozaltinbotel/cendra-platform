"""Decision Pipeline Adapter (M19).

Operational façade that runs the pre-tool-call gate chain —
ComplianceMonitor (M10), AbstentionGate (M1), RiskGate (M9),
CertificateVerifier (M3) — in one place and emits the Art.12
audit record (M5) on the PROCEED path.

Public surface:

- :class:`PipelineVerdict` — three-valued result enum.
- :class:`GateName` / :class:`GateOutcome` — per-gate audit
  trace row value object.
- :class:`PipelineRequest` — frozen dataclass holding every
  input the adapter needs.
- :class:`PipelineDecision` — frozen aggregate output with
  audit_record only present on PROCEED.
- :class:`DecisionPipelineAdapter` — runtime façade.

Defensibility note (honest): this module is *not* a new patent
moat.  Every gate the adapter consults is patent-defensible on
its own (M1, M3, M9, M10, M5); the adapter is the operational
seam that guarantees the runtime never accidentally bypasses a
gate.  Patent claims rest on the gates, not on the orchestration.
"""

from __future__ import annotations

from brain_engine.decision_pipeline.adapter import (
    DecisionPipelineAdapter,
)
from brain_engine.decision_pipeline.models import (
    GateName,
    GateOutcome,
    PipelineDecision,
    PipelineRequest,
    PipelineVerdict,
)


__all__ = [
    "DecisionPipelineAdapter",
    "GateName",
    "GateOutcome",
    "PipelineDecision",
    "PipelineRequest",
    "PipelineVerdict",
]
