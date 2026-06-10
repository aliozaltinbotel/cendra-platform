"""Lifespan wiring for the :class:`DecisionCaseStore`.

Backend selection is delegated to
:func:`brain_engine.patterns.wiring.build_decision_case_store`, which
reads ``DECISION_CASE_STORE_BACKEND`` and the matching connection
knobs from the environment.  The store is the durable record of
every processed turn: ``ConversationService`` writes a
:class:`DecisionCase` per turn, the ops decision logger reads
through it, and several lifespan readers downstream
(:class:`OpsDecisionLogger`, :class:`DecisionCaseTimelineSource`,
:class:`DecisionCaseEvidenceAdapter`, the property-ownership
resolver, and the V2 onboarding bootstrap pipeline) all depend on
the same instance.

A failure to construct the store is non-fatal by design: the
in-memory backend is the dev default, and prod failures should not
take the whole process down — the dependent subsystems each guard
on ``case_store is not None`` and degrade to a no-op, which keeps
chat replies flowing even when the learning layer is offline.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.patterns.store import DecisionCaseStore
from brain_engine.patterns.wiring import (
    CloseCallable,
    build_decision_case_store,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[DecisionCaseStore | None, CloseCallable | None]:
    """Construct the DecisionCase store and attach it to app state.

    On success the store is exposed at ``application.state.case_store``
    so that future readers migrated off the module global can resolve
    it through the FastAPI request lifecycle.

    On failure the section logs a warning and returns ``(None, None)``
    so that ``lifespan`` can publish ``None`` into the legacy globals
    and downstream sections see "learning disabled" rather than crash.

    Args:
        application: The FastAPI app whose ``state`` is the canonical
            home for the constructed store.

    Returns:
        A tuple ``(store, close)``.  ``close`` must be awaited on
        shutdown to release any pool the factory owned; both elements
        are ``None`` when the backend failed to come up.
    """
    try:
        store, close = await build_decision_case_store()
    except (ValueError, OSError, ConnectionError) as exc:
        logger.warning(
            "DecisionCase store init failed — falling back to "
            "disabled: %s",
            exc,
        )
        return None, None

    application.state.case_store = store
    logger.info("DecisionCase store initialized")
    return store, close
