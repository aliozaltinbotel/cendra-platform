"""Lifespan wiring for Autonomy + Trust Meter (V2).

The autonomy stack tracks per-(property, workflow) progression
through the OBSERVE / SEMI_AUTO / AUTOPILOT band.  This bootstrap
owns the backend selection (Postgres-or-memory, identical
contract to the BlockerStore wiring), the engine, and the
read-only :class:`TrustMeterService` projection that feeds the
V2 wireframe Trust Meter endpoint.

The wire entry point is **async** because :class:`PgAutonomyStore`
opens an asyncpg pool during ``from_url`` — that is the only
constructor in this section that performs network I/O.

A subtlety preserved verbatim: the in-memory store is the
universal fallback for every postgres failure mode (no URL,
connection error, schema error).  The Trust Meter endpoint
stays reachable at the cost of cross-restart durability — exact
parity with the original inline section.

The bootstrap returns a 4-tuple ``(autonomy_store,
autonomy_store_close, autonomy_engine, trust_meter_service)``:

* ``autonomy_store`` — always non-None.
* ``autonomy_store_close`` — async close handle when the
  postgres backend wired successfully, otherwise ``None``.  The
  caller threads this back into the lifespan-local variable so
  the existing shutdown branch can invoke it unchanged.
* ``autonomy_engine`` — the assembled engine, always non-None.
* ``trust_meter_service`` — the read-only projection used by
  the ``GET /api/v1/properties/{id}/trust-meter`` endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from fastapi import FastAPI

from brain_engine.autonomy import (
    AutonomyEngine,
    AutonomyStore,
    InMemoryAutonomyStore,
    PgAutonomyStore,
    TrustMeterService,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[
    AutonomyStore,
    Callable[[], Awaitable[None]] | None,
    AutonomyEngine,
    TrustMeterService,
]:
    """Build the autonomy store, engine, and Trust Meter service.

    On success ``application.state.autonomy_store``,
    ``application.state.autonomy_engine``, and
    ``application.state.trust_meter_service`` are populated so
    that future readers migrated off the module globals can
    resolve them through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.

    Returns:
        A 4-tuple ``(autonomy_store, autonomy_store_close,
        autonomy_engine, trust_meter_service)``.  See the module
        docstring for the exact contract.
    """
    # Backend selection mirrors BLOCKER_STORE_BACKEND: env-flag
    # picks the implementation, missing/broken Postgres falls
    # back to InMemory so the Trust Meter endpoint stays up.
    autonomy_store: AutonomyStore
    autonomy_store_close: Callable[[], Awaitable[None]] | None = None
    autonomy_backend = os.getenv(
        "AUTONOMY_STORE_BACKEND", "memory",
    ).lower()
    if autonomy_backend == "postgres":
        autonomy_db_url = os.getenv(
            "AUTONOMY_STORE_DATABASE_URL"
        ) or os.getenv("DATABASE_URL")
        if autonomy_db_url:
            try:
                pg_autonomy_store = await PgAutonomyStore.from_url(
                    autonomy_db_url,
                )
                autonomy_store = pg_autonomy_store
                autonomy_store_close = pg_autonomy_store.close
                logger.info(
                    "AutonomyStore backend=postgres (persistent)",
                )
            except (OSError, ConnectionError, ValueError) as exc:
                logger.warning(
                    "PgAutonomyStore init failed — falling back "
                    "to memory: %s",
                    exc,
                )
                autonomy_store = InMemoryAutonomyStore()
        else:
            logger.warning(
                "AUTONOMY_STORE_BACKEND=postgres but no database "
                "URL set; using InMemoryAutonomyStore.",
            )
            autonomy_store = InMemoryAutonomyStore()
    else:
        autonomy_store = InMemoryAutonomyStore()
        logger.info(
            "AutonomyStore backend=memory (non-persistent)",
        )

    # TrustMeterService is built last because it depends on a
    # live engine.  The engine itself is a thin façade over the
    # store with no I/O at construction.
    autonomy_engine = AutonomyEngine(store=autonomy_store)
    trust_meter_service = TrustMeterService(engine=autonomy_engine)
    logger.info("TrustMeterService initialized")

    application.state.autonomy_store = autonomy_store
    application.state.autonomy_engine = autonomy_engine
    application.state.trust_meter_service = trust_meter_service
    return (
        autonomy_store,
        autonomy_store_close,
        autonomy_engine,
        trust_meter_service,
    )
