"""Lifespan wiring for the cognitive memory system.

The :class:`MemorySystem` is the cognitive backbone shared by every
read path that needs guest history, episodic recall, knowledge-graph
context, surprise scoring, procedural memory, or consolidation.  It
fans out across Redis (working / episodic / customer) and Qdrant
(semantic vectors), so initialisation has real I/O cost: the
``initialize`` step opens Redis + Qdrant clients, ensures
collections exist, and warms internal caches.

This is why ``wire`` is async — unlike R5/R6/R7 (sync constructors
that only allocated httpx clients), the memory system performs
network I/O during ``initialize`` and must be awaited.

The shutdown contract still lives in ``server.lifespan``: ``await
memory.shutdown()`` flushes pending writes and closes the Redis /
Qdrant connections.  Moving that teardown belongs to a later PR
once readers stop reaching the module global directly.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from fastapi import FastAPI

from brain_engine.memory.factory import (
    MemorySystem,
    create_memory_system,
)
from config.settings import Settings

logger = logging.getLogger(__name__)


# Task 3 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
# for the baseline) — env-flag gate that decides whether the
# conversation pipeline gets a handle on the cognitive memory
# system.  Default off: the legacy ``application.state.memory`` slot
# is always populated for module-level readers, but the new
# ``application.state.memory_system`` slot that ``ConversationService``
# consumes through ``get_conversation_service`` (Task 2) stays unset
# until an operator opts in.  Read on every call so a deploy can
# flip without restarting the API pod.
_MEMORY_INJECT_ENV: Final[str] = "BRAIN_MEMORY_INJECT_ENABLED"


def memory_inject_enabled() -> bool:
    """Whether the conversation pipeline sees ``app.state.memory_system``.

    Returns ``True`` when ``BRAIN_MEMORY_INJECT_ENABLED`` is one of the
    documented truthy strings.  Default off — the wiring lands in
    Task 4 (``_load_memory_context``) and we do not want it active
    until the team explicitly opts in.
    """
    raw = os.environ.get(_MEMORY_INJECT_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


async def wire(
    application: FastAPI,
    *,
    settings: Settings,
) -> MemorySystem:
    """Construct and initialise the cognitive memory system.

    On success ``application.state.memory`` is populated so that
    future readers migrated off the module global can resolve it
    through the FastAPI request lifecycle.

    Unlike the optional integrations (Botel PMS, ElevenLabs,
    Telegram), the memory system is **not** optional — every
    cognitive read path depends on it.  If construction or
    initialisation raises, the failure propagates out of lifespan
    and aborts startup, which is the intended fail-fast contract:
    the engine cannot serve requests without working memory.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed system.
        settings: The loaded :class:`Settings` instance providing
            the Redis / Qdrant URLs and the LLM model used for
            consolidation and entity extraction.

    Returns:
        The fully-initialised :class:`MemorySystem` instance.
        ``memory.shutdown()`` must be awaited on shutdown to flush
        pending writes and close Redis / Qdrant connections — that
        teardown stays in ``server.lifespan`` for now.
    """
    memory = create_memory_system(
        redis_url=settings.redis_url,
        qdrant_url=settings.qdrant_url,
        llm_model=settings.llm_model,
    )
    await memory.initialize()
    application.state.memory = memory
    logger.info(
        "Cognitive memory system initialized (Redis=%s, Qdrant=%s)",
        settings.redis_url,
        settings.qdrant_url,
    )

    # Task 3 of CLAUDE_CODE_WIRING_FIX_PLAN.md — when the wiring
    # flag is on, alias the freshly-initialised memory system into
    # ``application.state.memory_system`` so the ``ConversationService``
    # FastAPI dependency (Task 2) can pick it up.  This is a pure
    # alias: no second ``MemorySystem`` is constructed, so the
    # existing ``shutdown()`` path that flushes ``app.state.memory``
    # also tears the alias down — no double-close risk.
    if memory_inject_enabled():
        application.state.memory_system = memory
        logger.info(
            "Conversation memory injection ENABLED — "
            "app.state.memory_system aliased to app.state.memory "
            "(BRAIN_MEMORY_INJECT_ENABLED truthy).",
        )
    else:
        logger.info(
            "Conversation memory injection disabled — "
            "app.state.memory_system stays unset.  Set "
            "BRAIN_MEMORY_INJECT_ENABLED=1 to enable.",
        )

    return memory
