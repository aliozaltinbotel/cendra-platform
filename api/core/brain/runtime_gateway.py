"""Runtime gateway — the kernel side of the T1 tool-dispatch hook.

``core.workflow.node_runtime`` (touchpoint T1, FORK_LEDGER.md) calls
:func:`evaluate_tool_dispatch` before every workflow tool invocation.
This module deliberately sees only primitives (tenant/app/tool ids,
conversation id) so the kernel-isolation contract holds — the adapter
imports brain, never the reverse.

Rollout is governed by ``BRAIN_GATES_MODE``:

- ``off`` (default) — returns ``None`` immediately; the fork behaves
  bit-for-bit like upstream Dify.
- ``observe`` — the gate chain runs and its decision is logged (the
  P2 phase-gate posture: one workspace watches verdicts accumulate),
  but dispatch always proceeds.
- ``enforce`` — non-PROCEED verdicts are returned to the touchpoint,
  which refuses the tool call.

``BRAIN_GATES_TENANTS`` optionally narrows either active mode to a
comma-separated tenant allowlist (P2: *one* workspace runs
``inquiry_reply`` in OBSERVE).

Batch 5: the compliance monitor (Reg 2024/1028, GDPR Art. 22, HITL
and never-AI checks) now occupies the chain's first slot via
:class:`_MonitorComplianceGate`; the Art.12 audit factory remains a
follow-up seam.  Calibration evidence lives in a per-process
:class:`InMemoryCalibrationStore` keyed by tenant; the persistent
calibration store arrives with the Batch 5 service layer.  Risk samples
are not available at generic tool dispatch, so the risk gate is
configured permissive-by-absence in observe/enforce until the planner
supplies loss distributions (its INSUFFICIENT_DATA defer is recorded in
the trace either way).
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Final

from core.brain.abstention.calibrator import ConformalCalibrator
from core.brain.abstention.gate import AbstentionGate
from core.brain.abstention.protocols import InMemoryCalibrationStore
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.verifier import CertificateVerifier
from core.brain.compliance.checks import DEFAULT_BUILTIN_CHECKS
from core.brain.compliance.monitor import ComplianceContext, ComplianceMonitor
from core.brain.gates import (
    ComplianceVerdict,
    DecisionPipelineAdapter,
    PipelineDecision,
    PipelineRequest,
    PipelineVerdict,
)
from core.brain.risk.gate import RiskGate

__all__ = [
    "GATES_MODE_ENV",
    "GATES_TENANTS_ENV",
    "evaluate_tool_dispatch",
    "record_tool_outcome",
    "reset_gateway_state",
]

logger = logging.getLogger(__name__)

GATES_MODE_ENV: Final[str] = "BRAIN_GATES_MODE"
GATES_TENANTS_ENV: Final[str] = "BRAIN_GATES_TENANTS"

_MODE_OFF: Final[str] = "off"
_MODE_OBSERVE: Final[str] = "observe"
_MODE_ENFORCE: Final[str] = "enforce"

# 32-byte placeholder key: certificates are not minted in Batch 4, so the
# certificate gate is always skipped (no cert on the request).  The real
# key arrives via dify_config in Batch 5 alongside the issuer service.
_PLACEHOLDER_KEY: Final[bytes] = b"cendra-batch4-placeholder-key-32"

_lock = threading.Lock()
_calibration_stores: dict[str, InMemoryCalibrationStore] = {}
_adapters: dict[str, DecisionPipelineAdapter] = {}


def _mode() -> str:
    raw = os.environ.get(GATES_MODE_ENV, _MODE_OFF).strip().lower()
    if raw in (_MODE_OFF, _MODE_OBSERVE, _MODE_ENFORCE):
        return raw
    logger.warning("unknown %s=%r — treating as off", GATES_MODE_ENV, raw)
    return _MODE_OFF


def _tenant_enabled(tenant_id: str) -> bool:
    raw = os.environ.get(GATES_TENANTS_ENV, "").strip()
    if not raw:
        return True
    allowed = {t.strip() for t in raw.split(",") if t.strip()}
    return tenant_id in allowed


class _MonitorComplianceGate:
    """Adapts the ported ComplianceMonitor to the gates.ComplianceGate seam."""

    def __init__(self) -> None:
        self._monitor = ComplianceMonitor(checks=DEFAULT_BUILTIN_CHECKS)

    def evaluate(self, request, *, at):
        verdict = self._monitor.evaluate(
            ComplianceContext(
                property_id=request.property_id,
                owner_id=request.owner_id,
                action_kind=request.action_kind,
                jurisdiction=request.jurisdiction,
                registration_id=request.registration_id,
                booking_dates=request.booking_dates,
                is_natural_person_decision=request.is_natural_person_decision,
                has_human_consent=request.has_human_consent,
                extra=request.compliance_extra,
            ),
            at=at,
        )
        # monitor PASS maps to a non-blocking, non-review row
        kind = verdict.kind.value if verdict.kind.value != "pass" else "ok"
        return ComplianceVerdict(kind=kind, rationale=verdict.rationale)


def _adapter_for(tenant_id: str) -> DecisionPipelineAdapter:
    with _lock:
        adapter = _adapters.get(tenant_id)
        if adapter is None:
            store = _calibration_stores.setdefault(tenant_id, InMemoryCalibrationStore())
            adapter = DecisionPipelineAdapter(
                abstention_gate=AbstentionGate(calibrator=ConformalCalibrator(store=store)),
                risk_gate=RiskGate(),
                certificate_verifier=CertificateVerifier(signing_key=_PLACEHOLDER_KEY, policy=TierPolicy()),
                compliance_gate=_MonitorComplianceGate(),
            )
            _adapters[tenant_id] = adapter
        return adapter


def evaluate_tool_dispatch(
    *,
    tenant_id: str,
    app_id: str,
    tool_id: str,
    conversation_id: str | None = None,
    model_confidence: float = 1.0,
) -> PipelineDecision | None:
    """Run the gate chain for one tool dispatch.

    Returns ``None`` when gating is off (or the tenant is outside the
    allowlist) **and** in observe mode after logging — callers treat
    ``None`` as "proceed unchanged".  In enforce mode the full
    :class:`PipelineDecision` comes back and non-PROCEED verdicts must
    refuse the dispatch.
    """
    mode = _mode()
    if mode == _MODE_OFF or not tenant_id or not _tenant_enabled(tenant_id):
        return None

    request = PipelineRequest(
        decision_id=uuid.uuid4().hex,
        property_id=app_id or "unknown",
        owner_id=tenant_id,
        action_kind=tool_id,
        rationale="workflow tool dispatch",
        provenance_digest=(conversation_id or "no-conversation").ljust(16, "-"),
        tool_id=tool_id,
        model_confidence=model_confidence,
        handler_solver="workflow",
    )
    decision = _adapter_for(tenant_id).decide(request)
    logger.info(
        "brain.gates mode=%s tenant=%s app=%s tool=%s verdict=%s rationale=%s",
        mode,
        tenant_id,
        app_id,
        tool_id,
        decision.verdict.value,
        decision.rationale,
    )
    if mode == _MODE_OBSERVE:
        return None
    # Batch 4 enforce posture: risk samples are unavailable at generic
    # dispatch, so a risk INSUFFICIENT_DATA defer must not break every
    # tool call — only abstention/certificate/compliance verdicts bind.
    if decision.verdict is not PipelineVerdict.PROCEED and decision.gate_trace[-1].gate.value == "risk":
        logger.info("brain.gates risk gate lacks samples at dispatch — passing through (Batch 4)")
        return None
    return decision


def record_tool_outcome(
    *,
    tenant_id: str,
    tool_id: str,
    predicted_confidence: float,
    success: bool,
) -> None:
    """Feed a post-hoc tool outcome into the tenant's calibration window.

    Called by the T7 capture path so abstention evidence accumulates
    while the workspace observes.
    """
    if not tenant_id:
        return
    from core.brain.abstention.models import CalibrationSample

    with _lock:
        store = _calibration_stores.setdefault(tenant_id, InMemoryCalibrationStore())
    store.record(
        CalibrationSample.now(
            tool_id=tool_id,
            predicted_confidence=max(0.0, min(1.0, predicted_confidence)),
            actual_success=success,
        )
    )


def reset_gateway_state() -> None:
    """Drop per-process gateway state (tests / config reload)."""
    with _lock:
        _calibration_stores.clear()
        _adapters.clear()
