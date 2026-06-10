"""Lifespan wiring for the :class:`OpsDecisionLogger`.

The ops logger is a thin façade over :class:`DecisionCaseStore`.
Every operations action that wants to be observable to the
pattern-learning layer (cleaner dispatch, vendor handoff,
maintenance triage, …) writes through it; the façade either
delegates to the store or, when the store is ``None``, becomes a
no-op.  This is why the section always constructs the logger
unconditionally — downstream call sites can hold a permanent
reference and never have to branch on whether learning is enabled.

There is no failure path here: :class:`OpsDecisionLogger` is a
plain Python object with no I/O at construction time, so the
``wire`` entry point is synchronous and cannot raise.  Any future
constructor that introduces I/O must turn this into ``async def``
and gain its own error envelope at the same time.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger
from brain_engine.patterns.store import DecisionCaseStore

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    case_store: DecisionCaseStore | None,
) -> OpsDecisionLogger:
    """Construct the ops logger and attach it to ``app.state``.

    Args:
        application: The FastAPI app whose ``state`` is the canonical
            home for the constructed logger.
        case_store: The DecisionCase store the logger writes through,
            or ``None`` when the learning layer is disabled.  The
            logger handles the ``None`` case internally as a no-op.

    Returns:
        The :class:`OpsDecisionLogger` instance, ready to be passed
        into :class:`ConversationService` and ops endpoints.
    """
    ops_logger = OpsDecisionLogger(case_store=case_store)
    application.state.ops_logger = ops_logger
    logger.info(
        "OpsDecisionLogger initialized (case_store=%s)",
        "active" if case_store is not None else "disabled",
    )
    return ops_logger
