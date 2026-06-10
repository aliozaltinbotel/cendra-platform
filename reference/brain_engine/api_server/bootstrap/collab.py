"""Lifespan wiring for the V2 collaboration surface.

This bootstrap owns two related lifespan sections that share the
"V2 UI artefact + collaboration" domain:

* :class:`CardStore` — the five-slot decision-card lifecycle
  (PENDING → CONFIRMED / DISMISSED / EXPIRED) backing the V2
  proposal cards.  Backend selection mirrors the other Pg-backed
  stores: ``CARD_STORE_BACKEND`` flips between ``memory`` and
  ``postgres``, with the in-memory store as the universal fallback
  for missing URL or driver / pool failure so an ops
  misconfiguration cannot bring the API down.
* Team mention + handoff stores — the lightweight in-memory
  collaboration scratch-pad.  No Postgres backing yet because the
  data is short-lived (the receiving teammate either acts on the
  handoff or it ages out into the audit log).  Wiring stays here
  because both stores feed the same router via
  :func:`configure_team_deps` and are conceptually one "team
  collaboration" surface.

Bundling these two concerns in one bootstrap follows the §17
guideline of grouping by domain coherence: a future module that
adds a postgres-backed mention/handoff store will live next to
the card store wiring rather than spawning a third tiny bootstrap.

The wire entry point is **async** because
:meth:`PgCardStore.from_url` opens an asyncpg pool — that is the
only constructor in this section that performs network I/O.

Two intentional differences from the other Pg-backed bootstraps
are preserved verbatim because the deployed behaviour depends on
them:

* The card store catches the broad ``Exception`` (annotated
  ``# pragma: no cover — defensive``) rather than the narrower
  ``(OSError, ConnectionError, ValueError)`` used by the
  Blocker / Autonomy / Interview bootstraps, because asyncpg-
  specific exceptions do not subclass any of those.
* The card store's database-URL fallback uses
  ``os.getenv("CARD_STORE_DATABASE_URL", os.getenv("DATABASE_URL"))``
  rather than the ``or``-chain elsewhere.  The two patterns
  differ only when ``CARD_STORE_DATABASE_URL`` is set to the empty
  string explicitly, which the deployed config never does — but
  the surface is preserved exactly to keep the refactor a no-op
  at runtime.

The bootstrap returns a 4-tuple ``(card_store, card_store_close,
mention_store, handoff_store)``:

* ``card_store`` — always non-None.
* ``card_store_close`` — async close handle when the postgres
  backend wired successfully, otherwise ``None``.  The caller
  threads this back into the lifespan-local
  ``_card_store_close`` so the existing shutdown branch invokes
  it unchanged.
* ``mention_store`` / ``handoff_store`` — the two in-memory
  stores, always non-None.
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from fastapi import FastAPI

from brain_engine.api.card_endpoints import configure_card_deps
from brain_engine.api.team_endpoints import configure_team_deps
from brain_engine.cards import CardStore, InMemoryCardStore, PgCardStore
from brain_engine.team import (
    HandoffStore,
    InMemoryHandoffStore,
    InMemoryMentionStore,
    MentionStore,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[
    CardStore,
    Callable[[], Awaitable[None]] | None,
    MentionStore,
    HandoffStore,
]:
    """Build the card store and the in-memory collaboration stores.

    On success ``application.state.{card_store, mention_store,
    handoff_store}`` are populated so future readers migrated off
    the module globals can resolve them through the FastAPI
    request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.

    Returns:
        A 4-tuple ``(card_store, card_store_close, mention_store,
        handoff_store)``.  See the module docstring for the exact
        contract.
    """
    # ── Decision card store (V2 UI artefact lifecycle) ──────────────
    card_store: CardStore
    card_store_close: Callable[[], Awaitable[None]] | None = None
    card_backend = os.getenv("CARD_STORE_BACKEND", "memory").lower()
    if card_backend == "postgres":
        card_db_url = os.getenv(
            "CARD_STORE_DATABASE_URL", os.getenv("DATABASE_URL"),
        )
        if card_db_url:
            try:
                pg_card_store = await PgCardStore.from_url(card_db_url)
                card_store = pg_card_store
                card_store_close = pg_card_store.close
                logger.info("CardStore backend=postgres (persistent)")
            except Exception as exc:  # pragma: no cover — defensive
                # Broad ``Exception`` matches the deployed contract;
                # asyncpg errors do not subclass OSError/ValueError.
                logger.warning(
                    "PgCardStore init failed — falling back to "
                    "memory: %s",
                    exc,
                )
                card_store = InMemoryCardStore()
        else:
            logger.warning(
                "CARD_STORE_BACKEND=postgres but no database URL "
                "set — using InMemoryCardStore.",
            )
            card_store = InMemoryCardStore()
    else:
        card_store = InMemoryCardStore()
        logger.info("CardStore backend=memory (non-persistent)")
    configure_card_deps({"card_store": card_store})

    # ── Team mention + handoff stores (V2 collaboration) ───────────
    # In-memory only for now — the data is short-lived; postgres
    # backing can be added later mirroring the InMemory contract.
    mention_store: MentionStore = InMemoryMentionStore()
    handoff_store: HandoffStore = InMemoryHandoffStore()
    configure_team_deps(
        {
            "mention_store": mention_store,
            "handoff_store": handoff_store,
        },
    )
    logger.info(
        "Team mention/handoff stores initialized (backend=memory)",
    )

    application.state.card_store = card_store
    application.state.mention_store = mention_store
    application.state.handoff_store = handoff_store
    return card_store, card_store_close, mention_store, handoff_store
