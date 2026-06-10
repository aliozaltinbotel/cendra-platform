"""Lifespan wiring for the EvidenceService (GAP L).

The evidence service composes an :class:`EvidenceBundle` for the
``POST /v1/cases/{id}/evidence`` endpoint by fanning out across
four sources — pattern rules, decision cases, blockers, and the
memory prompt aggregator.  This bootstrap owns everything from
the blocker-store backend selection (Postgres-or-memory) through
to the four adapters and the final service composition.

The wire entry point is **async** because :class:`PgBlockerStore`
opens an asyncpg pool during ``from_url`` — that is the only
constructor in this section that performs network I/O.  Every
other collaborator (``CustomerMemory``, ``FactStore``,
``MemoryPromptAggregator``, the four evidence adapters) is built
synchronously.

A subtlety preserved verbatim: the in-memory ``BlockerStore`` is
the universal fallback whenever the postgres branch is unable to
construct a live store (no URL, connection error, schema error).
The endpoint stays up at the cost of cross-restart durability —
exact parity with the original inline section.

The bootstrap returns a 4-tuple ``(evidence_service,
blocker_store, blocker_store_close, prompt_aggregator)``:

* ``evidence_service`` — always non-None.
* ``blocker_store`` — always non-None (memory fallback covers
  every failure).
* ``blocker_store_close`` — the async close handle when the
  postgres backend wired successfully, otherwise ``None``.  The
  caller threads this back into the lifespan-local variable so
  the existing shutdown branch can invoke it.
* ``prompt_aggregator`` — the assembled aggregator so the caller
  can re-assign the module global for downstream readers.

``configure_memory_deps`` is invoked here so the memory-edit
router shares the *same* :class:`FactStore` instance the prompt
aggregator reads from — preventing a second Qdrant connection
and keeping PM edits visible to the assembler immediately.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from brain_engine.api.memory_endpoints import configure_memory_deps
from brain_engine.blockers.engine import BlockerStore, InMemoryBlockerStore
from brain_engine.blockers.postgres_store import PgBlockerStore
from brain_engine.evidence import (
    BlockerEvidenceAdapter,
    DecisionCaseEvidenceAdapter,
    EvidenceService,
    MemoryPromptEvidenceAdapter,
    PatternRuleEvidenceAdapter,
)
from brain_engine.gestures.extractors import (
    CustomerMemoryExtractor,
    FactsExtractor,
    GuestHistoryExtractor,
)
from brain_engine.gestures.prompts import MemoryPromptAggregator
from brain_engine.memory.customer_memory import CustomerMemory
from brain_engine.memory.factory import MemorySystem
from brain_engine.memory.fact_store import FactStore
from brain_engine.patterns.store import DecisionCaseStore, PatternRuleStore
from config.settings import Settings

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
    *,
    rule_store: PatternRuleStore | None,
    case_store: DecisionCaseStore | None,
    memory: MemorySystem,
    settings: Settings,
) -> tuple[
    EvidenceService,
    BlockerStore,
    Callable[[], Awaitable[None]] | None,
    MemoryPromptAggregator,
]:
    """Build the EvidenceService and all four upstream adapters.

    On success ``application.state.evidence_service``,
    ``application.state.blocker_store``, and
    ``application.state.prompt_aggregator`` are populated so that
    future readers migrated off the module globals can resolve
    them through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.
        rule_store: Pattern-rule store from R3.  When ``None``
            the :class:`PatternRuleEvidenceAdapter` is skipped.
        case_store: DecisionCase store from R2.  When ``None``
            the :class:`DecisionCaseEvidenceAdapter` is skipped.
        memory: Cognitive memory system from R8.  Required —
            its ``guest_history`` feeds the
            :class:`GuestHistoryExtractor`.
        settings: The loaded :class:`Settings` providing
            ``redis_url`` / ``qdrant_url`` for the upstream
            memory stores.

    Returns:
        A 4-tuple ``(evidence_service, blocker_store,
        blocker_store_close, prompt_aggregator)``.  See the
        module docstring for the exact contract.
    """
    # Blocker backend is selected by BLOCKER_STORE_BACKEND
    # ("memory" | "postgres", default "memory") with the Postgres
    # URI resolved from BLOCKER_STORE_DATABASE_URL or
    # DATABASE_URL.  A misconfigured Postgres setup is non-fatal:
    # the lifespan falls back to the in-memory store, and the
    # existing blocker endpoints keep working (losing only
    # cross-restart durability).
    blocker_store: BlockerStore
    blocker_store_close: Callable[[], Awaitable[None]] | None = None
    blocker_backend = os.getenv(
        "BLOCKER_STORE_BACKEND", "memory",
    ).lower()
    if blocker_backend == "postgres":
        blocker_db_url = os.getenv(
            "BLOCKER_STORE_DATABASE_URL"
        ) or os.getenv("DATABASE_URL")
        if blocker_db_url:
            try:
                pg_blocker_store = await PgBlockerStore.from_url(
                    blocker_db_url,
                )
                blocker_store = pg_blocker_store
                blocker_store_close = pg_blocker_store.close
                logger.info(
                    "BlockerStore backend=postgres (persistent)",
                )
            except (OSError, ConnectionError, ValueError) as exc:
                logger.warning(
                    "PgBlockerStore init failed — falling back "
                    "to memory: %s",
                    exc,
                )
                blocker_store = InMemoryBlockerStore()
        else:
            logger.warning(
                "BLOCKER_STORE_BACKEND=postgres but no database "
                "URL set; using InMemoryBlockerStore.",
            )
            blocker_store = InMemoryBlockerStore()
    else:
        blocker_store = InMemoryBlockerStore()
        logger.info("BlockerStore backend=memory (non-persistent)")

    # Memory-prompt aggregator fans out to three extractors so
    # that EvidenceBundle.prompts carries production signal
    # rather than the empty placeholder tuple.
    customer_memory_store = CustomerMemory(redis_url=settings.redis_url)
    fact_store = FactStore(qdrant_url=settings.qdrant_url)
    # Wire the memory-edit router with the same FactStore
    # instance so PM edits land in the exact collection the
    # context assembler reads from — no second Qdrant connection.
    configure_memory_deps({"fact_store": fact_store})
    prompt_extractors: tuple[Any, ...] = (
        CustomerMemoryExtractor(customer_memory_store),
        GuestHistoryExtractor(memory.guest_history),
        FactsExtractor(fact_store),
    )
    prompt_aggregator = MemoryPromptAggregator(prompt_extractors)
    logger.info(
        "MemoryPromptAggregator initialized with %d extractors "
        "(customer_memory, guest_history, facts)",
        len(prompt_extractors),
    )

    # Evidence adapters — each guarded so a missing upstream
    # store degrades the bundle silently rather than the whole
    # endpoint.
    rule_adapter = (
        PatternRuleEvidenceAdapter(rule_store)
        if rule_store is not None
        else None
    )
    case_adapter = (
        DecisionCaseEvidenceAdapter(case_store)
        if case_store is not None
        else None
    )
    blocker_adapter = BlockerEvidenceAdapter(blocker_store)
    prompt_adapter = MemoryPromptEvidenceAdapter(prompt_aggregator)
    evidence_service = EvidenceService(
        rule_source=rule_adapter,
        case_source=case_adapter,
        prompt_source=prompt_adapter,
        blocker_source=blocker_adapter,
    )
    logger.info(
        "EvidenceService initialized (rules=%s, cases=%s, "
        "prompts=%s, blockers=%s)",
        "yes" if rule_adapter is not None else "no",
        "yes" if case_adapter is not None else "no",
        "yes",
        "yes",
    )

    application.state.evidence_service = evidence_service
    application.state.blocker_store = blocker_store
    application.state.prompt_aggregator = prompt_aggregator
    return (
        evidence_service,
        blocker_store,
        blocker_store_close,
        prompt_aggregator,
    )
