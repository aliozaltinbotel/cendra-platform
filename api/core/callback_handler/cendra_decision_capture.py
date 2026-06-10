# CENDRA-HOOK(T7): DecisionCase capture on workflow tool/agent completion.
"""Cendra DecisionCase capture (touchpoint T7).

Records every gated workflow tool dispatch as an immutable
:class:`DecisionCase` (Batch 2 store) and feeds the post-hoc outcome
into the tenant's abstention calibration window, so evidence
accumulates while a workspace runs in OBSERVE.

Activation follows the T1 rollout switches (``BRAIN_GATES_MODE`` off →
this module does nothing).  Capture is **idempotent** — the
``case_id`` is a deterministic hash of (workflow run, node execution,
tool), and the Batch 2 case store appends with ON-CONFLICT-DO-NOTHING
semantics — and **best-effort**: persistence failures never affect the
tool call.  ``conversation_id`` is stored as the join key the audit /
console surfaces stitch on (PORTING_MAP T7 note).

Cases are captured with ``scenario="general"`` (unclassified — never
learnable, by design) and ``stage="ops"``; scenario classification
arrives with the Batch 5/6 service layer.  Batch 4 simplification:
workflow tool dispatch carries no model confidence, so calibration
samples use ``predicted_confidence=1.0`` — the Wilson success-rate
path is fully meaningful; the conformal path sharpens once confidence
flows from the agent loop.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Generator

logger = logging.getLogger(__name__)


def _capture_enabled() -> bool:
    return os.environ.get("BRAIN_GATES_MODE", "off").strip().lower() in ("observe", "enforce")


def _deterministic_case_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:32]


def capture_tool_outcome(
    *,
    tenant_id: str,
    app_id: str,
    tool_id: str,
    conversation_id: str | None,
    dispatch_key: str,
    success: bool,
    detail: str = "",
) -> None:
    """Persist one tool outcome as a DecisionCase + calibration sample."""
    if not _capture_enabled() or not tenant_id:
        return
    try:
        from sqlalchemy.orm import sessionmaker

        from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
        from core.brain.patterns.models import (
            CaseOutcome,
            DecisionAction,
            DecisionCase,
            DecisionType,
            ResolutionType,
        )
        from core.brain.runtime_gateway import record_tool_outcome
        from extensions.ext_database import db

        record_tool_outcome(
            tenant_id=tenant_id,
            tool_id=tool_id,
            predicted_confidence=1.0,
            success=success,
        )

        case = DecisionCase(
            case_id=_deterministic_case_id(tenant_id, dispatch_key, tool_id),
            stage="ops",
            scenario="general",
            property_id=app_id or "unknown",
            owner_id=tenant_id,
            reservation_id=conversation_id,
            message_text=detail[:2000],
            decision=DecisionAction(
                action_type=DecisionType.DISPATCH,
                params={"tool_id": tool_id},
            ),
            outcome=CaseOutcome(
                successful=success,
                resolution_type=ResolutionType.AUTO_RESOLVED if success else ResolutionType.ESCALATED,
            ),
            orchestrator_verdict={"source": "t7_capture", "tool_id": tool_id},
        )
        store = SQLAlchemyDecisionCaseStore(
            session_maker=sessionmaker(bind=db.engine, expire_on_commit=False),
            tenant_id=tenant_id,
        )
        store.store(case)
    except Exception:
        logger.exception("cendra decision capture failed (ignored)")


def instrument_tool_messages[T](
    messages: Generator[T, None, None],
    *,
    tenant_id: str,
    app_id: str,
    tool_id: str,
    conversation_id: str | None,
    dispatch_key: str,
) -> Generator[T, None, None]:
    """Yield the tool's messages through; record the outcome at the end.

    Success = the stream exhausted without raising; any exception is
    recorded as a failure and re-raised unchanged.
    """
    if not _capture_enabled():
        yield from messages
        return
    try:
        yield from messages
    except Exception as exc:
        capture_tool_outcome(
            tenant_id=tenant_id,
            app_id=app_id,
            tool_id=tool_id,
            conversation_id=conversation_id,
            dispatch_key=dispatch_key,
            success=False,
            detail=str(exc),
        )
        raise
    capture_tool_outcome(
        tenant_id=tenant_id,
        app_id=app_id,
        tool_id=tool_id,
        conversation_id=conversation_id,
        dispatch_key=dispatch_key,
        success=True,
    )
