"""Idempotent event sequencer for the cascade-consumer (Risk 5).

Mümin's Service Bus emits one topic per resource family
(``botel-property-sync``, ``botel-reservation-sync``,
``botel-conversation-sync``, ``botel-guest-sync``).  Two failure
modes show up under load:

1. **Redelivery after DLQ retry** — the same message is delivered
   twice with the same ``MessageId``.  Applying it again would
   duplicate the memory write and re-trigger any side-effect.
2. **Out-of-order arrival** — under congestion, a newer event for
   the same subject can land before an older one.  Applying the
   older one second would silently overwrite fresh state with
   stale.

The sequencer fixes both.  Per ``(topic, entity_id)`` we keep the
last successfully-applied ``(sequence, event_id)`` in Postgres.
``claim()`` performs a single atomic upsert that:

- inserts the row when no record exists for the subject yet
  (first event we ever saw → APPLY)
- updates the row when ``EXCLUDED.last_sequence`` is strictly
  greater than the stored value (fresh event → APPLY)
- leaves the row untouched otherwise and reports back the stored
  value so the caller can decide whether to log DUPLICATE or
  OUT_OF_ORDER

The Postgres ``ON CONFLICT DO UPDATE WHERE`` clause makes the whole
operation atomic against concurrent consumers — even with two
listener replicas processing the same message family, exactly one
``claim()`` call wins.

Schema: table ``event_sequencer`` declared in
``deploy/postgres-migrations.yaml`` (migration ``010``).

Notes on idempotency at the next layer:
    The sequencer is the primary dedup; downstream sinks (PG fact
    store, etc.) may add their own content-addressed deduplication
    as a defence-in-depth safety net.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ─── SQL ───────────────────────────────────────────────────────────── #


# Single round-trip atomic claim.  Returns one row with the
# *post-state* ``last_sequence`` / ``last_event_id`` and an ``applied``
# flag computed from whether EXCLUDED beat the existing value.  The
# CTE shape lets us read both branches (insert vs no-op update) in
# one statement so the application never has to follow up.
_CLAIM_SQL: Final[str] = """
INSERT INTO event_sequencer (
    topic, entity_id, last_sequence, last_event_id, last_applied_at
)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (topic, entity_id) DO UPDATE SET
    last_sequence   = EXCLUDED.last_sequence,
    last_event_id   = EXCLUDED.last_event_id,
    last_applied_at = EXCLUDED.last_applied_at
WHERE EXCLUDED.last_sequence > event_sequencer.last_sequence
RETURNING last_sequence, last_event_id, last_applied_at,
          (xmax = 0) AS inserted
"""


_PEEK_SQL: Final[str] = (
    "SELECT last_sequence, last_event_id, last_applied_at "
    "FROM event_sequencer WHERE topic = $1 AND entity_id = $2"
)


# ─── Public types ─────────────────────────────────────────────────── #


class ApplyVerdict(Enum):
    """Result of an attempted ``claim()``.

    - ``APPLY``        — the sequencer accepted the event; the caller
      MUST proceed to apply it to downstream state.
    - ``DUPLICATE``    — the same ``last_event_id`` is already on
      record; the caller MUST skip silently.
    - ``OUT_OF_ORDER`` — a later sequence is already on record under
      a *different* event_id; the caller SHOULD skip and log a
      warning, because the fresher state has already been applied.
    """

    APPLY = "apply"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"


@dataclass(frozen=True, slots=True)
class ApplyDecision:
    """Verdict returned from :meth:`EventSequencer.claim`.

    Attributes:
        verdict: Whether the caller should proceed.
        last_seen_sequence: Sequence currently stored for the
            subject.  When ``verdict`` is ``APPLY`` this equals the
            requested sequence; otherwise it reflects the previously
            applied event.
        last_seen_event_id: Event id currently stored.
        last_seen_at: When the stored event was applied (UTC).
    """

    verdict: ApplyVerdict
    last_seen_sequence: int
    last_seen_event_id: str
    last_seen_at: datetime


# ─── Pool helper ──────────────────────────────────────────────────── #


async def create_sequencer_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 4,
) -> asyncpg.Pool:
    """Create a small ``asyncpg`` pool dedicated to the sequencer.

    The sequencer's SQL footprint is tiny (one upsert per event), so a
    narrow pool is sufficient and keeps the connection count under the
    cendra-pg-app secret's quota.

    Args:
        database_url: Postgres URI (``postgresql://…``).
        min_size: Minimum pool size.
        max_size: Maximum pool size.

    Returns:
        A live asyncpg connection pool.

    Raises:
        ImportError: When ``asyncpg`` is not installed.
    """
    import asyncpg  # local import — optional dependency

    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
    )


# ─── Store ────────────────────────────────────────────────────────── #


class EventSequencer:
    """Postgres-backed dedup + ordering ledger.

    Stateless apart from the injected pool.  All queries are
    parameterised; the ``topic`` and ``entity_id`` strings are NEVER
    interpolated into SQL.

    Attributes:
        _pool: Injected asyncpg pool.
        _log: Structured logger bound to this component.
        _owns_pool: Whether :meth:`close` should close the pool.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._owns_pool = owns_pool
        self._log = logger.bind(component="event_sequencer")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 4,
    ) -> EventSequencer:
        """Build a sequencer that owns a freshly-created pool."""
        pool = await create_sequencer_pool(
            database_url,
            min_size=min_size,
            max_size=max_size,
        )
        return cls(pool, owns_pool=True)

    async def close(self) -> None:
        """Close the underlying pool if this sequencer owns it."""
        if self._owns_pool:
            await self._pool.close()
            self._log.info("pool_closed")

    async def claim(
        self,
        *,
        topic: str,
        entity_id: str,
        sequence: int,
        event_id: str,
        applied_at: datetime,
    ) -> ApplyDecision:
        """Atomically claim ``sequence`` for ``(topic, entity_id)``.

        The single SQL statement decides between INSERT and no-op
        UPDATE; the caller never has to follow up.  ``RETURNING``
        produces a row only when the upsert wrote — when no row is
        returned, the existing value beat us and the caller must
        check it via :meth:`peek` to disambiguate ``DUPLICATE`` from
        ``OUT_OF_ORDER``.

        Args:
            topic: Service Bus topic name (``botel-*-sync``).  Used as
                the namespace for the sequence; a property update and
                a reservation update for the same property carry
                independent sequences.
            entity_id: Subject identifier within the topic — typically
                the property / reservation / guest / conversation id.
            sequence: Monotonic sequence number assigned by the source
                (Mümin's pipeline).  Must be strictly greater than
                the previously claimed sequence to be APPLY.
            event_id: Source-side event identifier (``MessageId`` from
                Service Bus).  Used to discriminate ``DUPLICATE``
                from ``OUT_OF_ORDER`` on equal sequences.
            applied_at: UTC timestamp recorded as
                ``last_applied_at``.  Caller controls this so tests
                can pin time and so prod can record source-side
                timestamps when available.

        Returns:
            :class:`ApplyDecision` carrying the verdict and the
            authoritative state stored after the call.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _CLAIM_SQL,
                topic,
                entity_id,
                sequence,
                event_id,
                applied_at,
            )
            if row is not None:
                return ApplyDecision(
                    verdict=ApplyVerdict.APPLY,
                    last_seen_sequence=int(row["last_sequence"]),
                    last_seen_event_id=str(row["last_event_id"]),
                    last_seen_at=row["last_applied_at"],
                )
            # The upsert did not write — peek to learn why.
            stored = await conn.fetchrow(_PEEK_SQL, topic, entity_id)

        if stored is None:
            # Race we cannot distinguish: the row vanished between
            # claim and peek.  Treat as OUT_OF_ORDER and log loudly so
            # an operator can investigate.  In practice this requires
            # a manual DELETE on the table.
            self._log.warning(
                "claim_peek_missing",
                topic=topic,
                entity_id=entity_id,
                sequence=sequence,
                event_id=event_id,
            )
            return ApplyDecision(
                verdict=ApplyVerdict.OUT_OF_ORDER,
                last_seen_sequence=sequence,
                last_seen_event_id="",
                last_seen_at=applied_at,
            )

        last_seq = int(stored["last_sequence"])
        last_eid = str(stored["last_event_id"])
        last_at = stored["last_applied_at"]

        if last_eid == event_id:
            verdict = ApplyVerdict.DUPLICATE
        else:
            verdict = ApplyVerdict.OUT_OF_ORDER

        return ApplyDecision(
            verdict=verdict,
            last_seen_sequence=last_seq,
            last_seen_event_id=last_eid,
            last_seen_at=last_at,
        )

    async def peek(
        self,
        *,
        topic: str,
        entity_id: str,
    ) -> ApplyDecision | None:
        """Return the currently stored ``(sequence, event_id)`` if any.

        Read-only; the cascade-consumer uses :meth:`claim` for the
        hot path.  ``peek`` exists for diagnostics and for the
        DLQ-watch alert that flags subjects whose ``last_applied_at``
        has fallen too far behind wall-clock.
        """
        async with self._pool.acquire() as conn:
            stored = await conn.fetchrow(_PEEK_SQL, topic, entity_id)
        if stored is None:
            return None
        return ApplyDecision(
            verdict=ApplyVerdict.APPLY,
            last_seen_sequence=int(stored["last_sequence"]),
            last_seen_event_id=str(stored["last_event_id"]),
            last_seen_at=stored["last_applied_at"],
        )
