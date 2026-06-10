"""Lifespan wiring for the Interview engine (V2 proactive PM Q&A).

The interview stack drives Cendra's never-ending onboarding: the
engine selects the next high-priority question to ask the
property manager, the store captures the answer (text or voice
transcript) so the engine can re-rank.  This bootstrap owns the
backend selection (Postgres-or-memory, identical contract to the
BlockerStore wiring) and the engine composition.

The wire entry point is **async** because :class:`PgInterviewAnswerStore`
opens an asyncpg pool during ``from_url`` — that is the only
constructor in this section that performs network I/O.

A subtlety preserved verbatim: the in-memory store is the
universal fallback for every postgres failure mode (no URL,
connection error, schema error).  The ``/api/v1/interview/*``
endpoints stay reachable at the cost of cross-restart durability
— exact parity with the original inline section.

The bootstrap returns a 3-tuple ``(interview_store,
interview_store_close, interview_engine)``:

* ``interview_store`` — always non-None.
* ``interview_store_close`` — async close handle when the
  postgres backend wired successfully, otherwise ``None``.  The
  caller threads this back into the lifespan-local variable so
  the existing shutdown branch can invoke it unchanged.
* ``interview_engine`` — the assembled engine, always non-None.

This bootstrap deliberately does **not** call
``configure_interview_deps`` because that contract also needs
the voice transcriber (a separate concern wired between this
section and the ``configure_interview_deps`` invocation).  The
caller composes the two and binds the router.
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from fastapi import FastAPI

from brain_engine.interview import (
    InMemoryInterviewAnswerStore,
    InterviewAnswerStore,
    InterviewEngine,
    PgInterviewAnswerStore,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[
    InterviewAnswerStore,
    Callable[[], Awaitable[None]] | None,
    InterviewEngine,
]:
    """Build the interview store and engine.

    On success ``application.state.interview_store`` and
    ``application.state.interview_engine`` are populated so that
    future readers migrated off the module globals can resolve
    them through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.

    Returns:
        A 3-tuple ``(interview_store, interview_store_close,
        interview_engine)``.  See the module docstring for the
        exact contract.
    """
    # Backend selection mirrors BLOCKER_STORE_BACKEND: env-flag
    # picks the implementation, missing/broken Postgres falls
    # back to InMemory so the /api/v1/interview/* endpoints stay
    # reachable.
    interview_store: InterviewAnswerStore
    interview_store_close: Callable[[], Awaitable[None]] | None = None
    interview_backend = os.getenv(
        "INTERVIEW_STORE_BACKEND", "memory",
    ).lower()
    if interview_backend == "postgres":
        interview_db_url = os.getenv(
            "INTERVIEW_STORE_DATABASE_URL"
        ) or os.getenv("DATABASE_URL")
        if interview_db_url:
            try:
                pg_interview_store = (
                    await PgInterviewAnswerStore.from_url(
                        interview_db_url,
                    )
                )
                interview_store = pg_interview_store
                interview_store_close = pg_interview_store.close
                logger.info(
                    "InterviewAnswerStore backend=postgres "
                    "(persistent)",
                )
            except (OSError, ConnectionError, ValueError) as exc:
                logger.warning(
                    "PgInterviewAnswerStore init failed — falling "
                    "back to memory: %s",
                    exc,
                )
                interview_store = InMemoryInterviewAnswerStore()
        else:
            logger.warning(
                "INTERVIEW_STORE_BACKEND=postgres but no database "
                "URL set; using InMemoryInterviewAnswerStore.",
            )
            interview_store = InMemoryInterviewAnswerStore()
    else:
        interview_store = InMemoryInterviewAnswerStore()
        logger.info(
            "InterviewAnswerStore backend=memory (non-persistent)",
        )

    interview_engine = InterviewEngine(store=interview_store)

    application.state.interview_store = interview_store
    application.state.interview_engine = interview_engine
    return interview_store, interview_store_close, interview_engine
