"""Live Art. 12 receipt emission at the gate chain's PROCEED seam (CEN-81).

This module is the ``audit_factory`` the runtime gateway injects into
:class:`core.brain.gates.DecisionPipelineAdapter`: when — and only when —
every gate passes, the emitter builds the :class:`Art12Decision` record,
seals it in a :class:`ReceiptEnvelope` (CEN-79), and appends it durably
through the CEN-80 audit backend.  Three properties are pinned here:

1. **Signed bytes carry the decision context.**  The gate trace and the
   model confidence the chain consulted travel inside ``extra``, which is
   part of :func:`core.brain.compliance.art12_decision.canonical_record`
   — so the Ed25519 signature (and the chained digest) bind exactly what
   was decided and why, not just the headline verdict.
2. **Idempotent by ``decision_id``.**  Re-emitting a decision that is
   already persisted returns the stored envelope instead of forking the
   chain; a lost tail race against a concurrent writer is retried once
   with a refreshed ``prev_digest``.
3. **Fail-open.**  Emission is best-effort: a persistence or signing
   failure is logged and the dispatch proceeds unreceipted
   (``PipelineDecision.audit_record = None``) — the gate chain must never
   become a new outage mode for upstream tool calls, in observe mode
   especially.

Observe-mode honesty (CEN-14 PRD §2.5): with no signer provisioned the
envelope is minted **unsigned** (``signed=False``) — never a fake
signature — and surfaces must render it as such.  The signer arrives
through a provider callable so the kernel never imports the service
layer (kernel-isolation contract; the custody service registers itself
at wiring time, see ``runtime_gateway.register_receipt_signer``).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Final

from core.brain.compliance.art12_decision import Art12Decision, HandlerSolver

if TYPE_CHECKING:
    from core.brain.certificates.receipt import ReceiptEnvelope, ReceiptSigner
    from core.brain.compliance.art12_audit import Art12AuditLogger
    from core.brain.gates import GateOutcome, PipelineRequest

__all__ = [
    "EXTRA_GATE_TRACE_KEY",
    "EXTRA_MODEL_CONFIDENCE_KEY",
    "OUTCOME_FAILURE",
    "OUTCOME_SUCCESS",
    "ReceiptEmitter",
    "SignerProvider",
]

logger = logging.getLogger(__name__)

EXTRA_GATE_TRACE_KEY: Final[str] = "gate_trace"
EXTRA_MODEL_CONFIDENCE_KEY: Final[str] = "model_confidence"

# Outcome vocabulary the T7 stitch writes back onto the emitted row.
OUTCOME_SUCCESS: Final[str] = "success"
OUTCOME_FAILURE: Final[str] = "failure"

SignerProvider = Callable[[], "ReceiptSigner | None"]
"""Resolve the tenant's receipt signer at emission time (``None`` → unsigned).

A provider rather than a bound signer so a key provisioned *after* the
per-tenant adapter was cached starts signing without a process restart.
"""

# One refresh of prev_digest after losing the tail to a concurrent writer.
_CHAIN_ATTEMPTS: Final[int] = 2


class ReceiptEmitter:
    """Mints and durably persists one receipt per gate-chain PROCEED."""

    def __init__(
        self,
        *,
        tenant_id: str,
        audit_logger: Art12AuditLogger,
        signer_provider: SignerProvider | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._logger = audit_logger
        self._signer_provider = signer_provider
        self._lock = threading.Lock()

    def build(
        self,
        request: PipelineRequest,
        moment: datetime,
        gate_trace: tuple[GateOutcome, ...],
    ) -> ReceiptEnvelope | None:
        """``audit_factory`` seam — called by the adapter on PROCEED only.

        Returns the sealed envelope (it becomes
        ``PipelineDecision.audit_record``) or ``None`` when emission
        failed; the failure is logged and the dispatch is unaffected.
        """
        try:
            return self._emit(request=request, moment=moment, gate_trace=gate_trace)
        except Exception:
            logger.exception(
                "art12 receipt emission failed tenant=%s decision=%s (dispatch proceeds unreceipted)",
                self._tenant_id,
                request.decision_id[:8],
            )
            return None

    def _emit(
        self,
        *,
        request: PipelineRequest,
        moment: datetime,
        gate_trace: tuple[GateOutcome, ...],
    ) -> ReceiptEnvelope:
        # Local import: certificates/__init__ imports receipt.py, which
        # imports this package — a module-level import here would deadlock
        # package init when certificates is imported first.
        from core.brain.certificates.receipt import seal_receipt

        existing = self._logger.get_envelope(request.decision_id)
        if existing is not None:
            return existing

        signer = self._signer_provider() if self._signer_provider is not None else None
        extra = self._signed_extra(request=request, gate_trace=gate_trace)

        with self._lock:
            last_error: ValueError | None = None
            for _ in range(_CHAIN_ATTEMPTS):
                record = Art12Decision(
                    decision_id=request.decision_id,
                    occurred_at=moment,
                    property_id=request.property_id,
                    owner_id=request.owner_id,
                    action_kind=request.action_kind,
                    handler_solver=HandlerSolver(request.handler_solver),
                    rationale=request.rationale,
                    provenance_digest=request.provenance_digest,
                    autonomy_tier=request.autonomy_tier,
                    planner_style=request.planner_style,
                    prev_digest=self._logger.last_digest(),
                    extra=extra,
                )
                envelope = seal_receipt(record, tenant_id=self._tenant_id, signer=signer)
                try:
                    self._logger.append_envelope(envelope)
                    return envelope
                except ValueError as exc:
                    # Tail advanced under a concurrent writer — refresh
                    # prev_digest and rebuild; any other ValueError
                    # (e.g. conflicting payload) recurs and surfaces.
                    last_error = exc
            assert last_error is not None
            raise last_error

    def _signed_extra(
        self,
        *,
        request: PipelineRequest,
        gate_trace: tuple[GateOutcome, ...],
    ) -> dict[str, str]:
        """Fold the decision context into the canonical (signed) payload."""
        extra = dict(request.extra)
        extra[EXTRA_GATE_TRACE_KEY] = json.dumps(
            [
                {"gate": row.gate.value, "verdict": row.verdict, "rationale": row.rationale}
                for row in gate_trace
            ],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        extra[EXTRA_MODEL_CONFIDENCE_KEY] = f"{request.model_confidence:.6f}"
        return extra
