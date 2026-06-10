"""Lifespan wiring for the :class:`PatternRuleStore` and its router.

Backend selection is delegated to
:func:`brain_engine.patterns.wiring.build_pattern_rule_store`, which
reads ``PATTERN_RULE_STORE_BACKEND`` and the matching connection
knobs from the environment.

The router is constructed in the same step because it is owned by
the same lifecycle: a router without a store has nothing to
consult, and a store without a router would never be queried by
:class:`ConversationService` before the LLM call (Fix #4c).
Bundling the two into a single ``wire`` keeps the failure mode
atomic — either both come up or neither does, no half-state where
``_rule_store`` exists but the router is ``None``.

A failure to construct the store is non-fatal by design: the
conversation pipeline guards on ``rule_router is not None`` and
falls back to standard LLM generation without learned-rule hints.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.patterns.router import PatternRuleRouter
from brain_engine.patterns.store import PatternRuleStore
from brain_engine.patterns.wiring import (
    CloseCallable,
    build_pattern_rule_store,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[
    PatternRuleStore | None,
    CloseCallable | None,
    PatternRuleRouter | None,
]:
    """Construct the PatternRule store and router, attach to app state.

    On success both ``application.state.rule_store`` and
    ``application.state.rule_router`` are populated so that future
    readers migrated off the module globals can resolve them through
    the FastAPI request lifecycle.

    On a documented backend failure the section logs a warning and
    returns ``(None, None, None)`` so that ``lifespan`` can publish
    ``None`` into the legacy globals and downstream readers see "rule
    learning disabled" rather than crash.

    Args:
        application: The FastAPI app whose ``state`` is the canonical
            home for the constructed store and router.

    Returns:
        A tuple ``(store, close, router)``.  ``close`` must be
        awaited on shutdown to release any pool the factory owned;
        all three elements are ``None`` when the backend failed.
    """
    try:
        store, close = await build_pattern_rule_store()
    except (ValueError, OSError, ConnectionError) as exc:
        logger.warning(
            "PatternRule store init failed — falling back to "
            "disabled: %s",
            exc,
        )
        return None, None, None

    router = PatternRuleRouter(rule_store=store)
    application.state.rule_store = store
    application.state.rule_router = router
    logger.info("PatternRule store + router initialized")
    return store, close, router
