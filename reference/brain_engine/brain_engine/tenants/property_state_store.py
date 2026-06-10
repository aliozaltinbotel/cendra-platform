"""Persistence Protocol + in-memory store for ``PropertyState``.

Three methods describe the entire contract:

* :meth:`get` — point lookup by ``property_channel_id``.  The
  hot path for the bootstrap-intent function (PR-B): every
  Sandbox UI property pick triggers one of these before
  deciding whether to enqueue.
* :meth:`create_if_absent` — idempotent insert.  Returns the
  existing row if one was already there so two concurrent
  ``request_bootstrap()`` invocations cannot stomp on each
  other's progress counts.
* :meth:`update` — full-row write keyed by
  ``property_channel_id``.  Callers compose the new value with
  ``dataclasses.replace(current, status=..., updated_at=...)``
  rather than passing a sparse update dict, which keeps the
  API type-safe end to end (no sentinels, no TypedDicts) and
  makes "did this caller see a stale snapshot?" auditable —
  the old and new objects are both right there in the call
  site.

The InMemory implementation lives here next to the Protocol so
unit tests can import the contract and one working
implementation with a single line.  The Postgres-backed
implementation lives in :mod:`property_state_postgres` and only
materialises when an :mod:`asyncpg` pool is wired.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Protocol

from brain_engine.tenants.property_state import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    PropertyState,
)

__all__ = [
    "InMemoryPropertyStateStore",
    "PropertyStateNotFoundError",
    "PropertyStateStore",
]


class PropertyStateNotFoundError(KeyError):
    """Raised by :meth:`PropertyStateStore.update` for unknown PKs.

    Inherits :class:`KeyError` so legacy callers that catch
    ``KeyError`` keep working, but the subclass surfaces the
    intent ("the row I wanted to update is not there") cleanly
    in tracebacks and structured logs.
    """


class PropertyStateStore(Protocol):
    """Read / write contract for the ``property_state`` table."""

    async def get(
        self,
        property_channel_id: str,
    ) -> PropertyState | None:
        """Return the stored row for ``property_channel_id``.

        Returns ``None`` when no row exists — callers should
        treat this as "this property has never been touched"
        and proceed to :meth:`create_if_absent` rather than
        raising.
        """
        ...

    async def create_if_absent(
        self,
        state: PropertyState,
    ) -> PropertyState:
        """Insert ``state`` or return the existing row.

        Idempotent: two concurrent callers passing the same
        ``property_channel_id`` both observe the *same* row
        (whichever one won the insert).  The returned object
        always reflects what is currently persisted — callers
        must not assume the returned row equals the argument.
        """
        ...

    async def update(
        self,
        state: PropertyState,
    ) -> PropertyState:
        """Persist ``state`` keyed by ``property_channel_id``.

        Overwrites every column.  Callers are expected to have
        read the current row, applied
        ``dataclasses.replace(...)`` with the intended changes
        (including ``updated_at``) and passed the result here.

        Raises:
            PropertyStateNotFoundError: when no row exists for
                ``state.property_channel_id``.  Callers wanting
                "create or update" semantics should fall back
                to :meth:`create_if_absent` first.
        """
        ...

    async def reap_orphaned(
        self,
        *,
        cutoff: datetime,
        now: datetime | None = None,
    ) -> list[str]:
        """Flip orphaned ``queued`` / ``warming`` rows to ``failed``.

        A row in ``queued`` or ``warming`` whose ``updated_at`` is
        older than ``cutoff`` belongs to a bootstrap no live task
        owns anymore — the pod that started it was killed, or the
        in-process task wedged.  Such a row blocks every future
        intent for that property through the in-flight dedup, so the
        reaper transitions it to ``failed`` (``retry_count`` bumped,
        ``current_job_id`` cleared) to free it for re-attempt.

        Args:
            cutoff: Rows last updated strictly before this instant
                are considered orphaned.
            now: Test seam for the failed-transition timestamp;
                defaults to ``datetime.now(UTC)``.

        Returns:
            The channel ids that were reaped (possibly empty).
        """
        ...

    async def list_stale_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[PropertyState]:
        """Return ``primed`` rows whose last warm predates ``cutoff``.

        The read side of the Stage 3 nightly stale-sweep (Track B):
        it surfaces every property the proactive backstop should
        re-warm because no reactive OTA webhook has refreshed it
        within the freshness TTL.

        Staleness is measured on ``COALESCE(last_bootstrap_at,
        updated_at)`` — the moment the row was last warmed — so a
        property kept fresh by the reactive path (which re-stamps
        ``last_bootstrap_at`` on every re-prime) is never selected,
        and a legacy primed row with a ``NULL`` ``last_bootstrap_at``
        still ages out on ``updated_at`` instead of being stranded by
        ``NULL < cutoff`` evaluating to false.

        Only ``primed`` rows are returned: ``cold`` / ``queued`` /
        ``warming`` / ``failed`` / already-``stale`` rows are owned by
        other paths (first-touch bootstrap, the in-flight dedup, the
        reaper, the reactive consumer) and a proactive sweep must
        leave them alone.

        Args:
            cutoff: Rows last warmed strictly before this instant are
                candidates.  Callers pass ``now - ttl``.
            limit: Hard cap on the rows returned in one sweep so a
                backlog cannot flood the ``bootstrap-intents`` queue
                in a single run; the remainder ages out on the next
                nightly tick.

        Returns:
            Up to ``limit`` ``primed`` rows, oldest-warmed first, so
            the most stale properties refresh first when the cap bites.
        """
        ...


class InMemoryPropertyStateStore:
    """Process-local store used by tests and the default pod.

    Backed by a plain ``dict`` keyed on
    ``property_channel_id``.  No locking: the contract assumes
    one event loop, which is the case for every test that
    instantiates this directly.  Concurrent multi-pod
    coordination is the job of
    :class:`PostgresPropertyStateStore`.
    """

    def __init__(self) -> None:
        self._rows: dict[str, PropertyState] = {}

    async def get(
        self,
        property_channel_id: str,
    ) -> PropertyState | None:
        return self._rows.get(property_channel_id)

    async def create_if_absent(
        self,
        state: PropertyState,
    ) -> PropertyState:
        existing = self._rows.get(state.property_channel_id)
        if existing is not None:
            return existing
        self._rows[state.property_channel_id] = state
        return state

    async def update(
        self,
        state: PropertyState,
    ) -> PropertyState:
        if state.property_channel_id not in self._rows:
            raise PropertyStateNotFoundError(
                state.property_channel_id,
            )
        self._rows[state.property_channel_id] = state
        return state

    async def reap_orphaned(
        self,
        *,
        cutoff: datetime,
        now: datetime | None = None,
    ) -> list[str]:
        now_ts = now or datetime.now(UTC)
        reaped: list[str] = []
        for cid, row in list(self._rows.items()):
            if row.status not in (
                PROPERTY_STATUS_QUEUED,
                PROPERTY_STATUS_WARMING,
            ):
                continue
            if row.updated_at is not None and row.updated_at >= cutoff:
                continue
            self._rows[cid] = dataclasses.replace(
                row,
                status=PROPERTY_STATUS_FAILED,
                current_job_id=None,
                last_error=f"reaped: orphaned {row.status}",
                retry_count=row.retry_count + 1,
                updated_at=now_ts,
            )
            reaped.append(cid)
        return reaped

    async def list_stale_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[PropertyState]:
        candidates = [
            row
            for row in self._rows.values()
            if row.status == PROPERTY_STATUS_PRIMED
            and (row.last_bootstrap_at or row.updated_at) < cutoff
        ]
        candidates.sort(key=lambda row: row.last_bootstrap_at or row.updated_at)
        return candidates[: max(0, limit)]
