"""Value object + status vocabulary for the property bootstrap SSoT.

A :class:`PropertyState` row mirrors one record of the
``property_state`` table (migration ``034``).  It captures the
full lifecycle of a property's bootstrap: from never-touched
(``cold``) through queued / warming to a terminal ``primed`` /
``failed`` / ``stale`` state.

The single source of truth replaces the three legacy dedup
signals — ``PropertyProfile`` presence, the registry cooldown
column, and the in-process ``_pending`` set on the auto-trigger.
A future bootstrap-intent function (PR-B) and Stage 2 worker
will both read and write the same row, which is why the model
is intentionally split out of any store implementation: the
Protocol / InMemory / Postgres files all consume this module so
contract tests can mock either side without dragging the other in.

The class is frozen + slotted: a transition is expressed as
``dataclasses.replace(state, status=..., updated_at=now())``
rather than mutation, which makes "did this hand-off see a
stale snapshot?" questions trivial to audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

__all__ = [
    "ALLOWED_PROPERTY_STATUSES",
    "PROPERTY_STATUS_COLD",
    "PROPERTY_STATUS_FAILED",
    "PROPERTY_STATUS_PRIMED",
    "PROPERTY_STATUS_QUEUED",
    "PROPERTY_STATUS_STALE",
    "PROPERTY_STATUS_WARMING",
    "PropertyState",
]


#: Never touched.  No bootstrap intent has been recorded yet for
#: this property.  Default for every freshly-inserted row.
PROPERTY_STATUS_COLD: Final[str] = "cold"

#: Bootstrap intent has been recorded; the row is waiting for a
#: worker to pick it up.  In Stage 1 the worker is the in-process
#: ``asyncio.create_task`` shim; Stage 2 replaces it with an
#: Azure Service Bus consumer.
PROPERTY_STATUS_QUEUED: Final[str] = "queued"

#: A worker is currently pulling data for this property.
#: ``current_job_id`` points at the live job.  Transitions away
#: from ``warming`` always drain ``current_job_id`` so the
#: dashboard cannot show a stale job for a finished property.
PROPERTY_STATUS_WARMING: Final[str] = "warming"

#: Bootstrap finished successfully and the property is ready to
#: serve answers.  ``conversations_loaded`` / ``cases_extracted``
#: / ``rules_emitted`` carry the harvest counts so operators can
#: distinguish "empty property primed clean" from "primed with
#: meaningful history".
PROPERTY_STATUS_PRIMED: Final[str] = "primed"

#: Was primed once, then a backend OTA event arrived and the Stage 3
#: reactive consumer flipped the row here via ``mark_stale`` before
#: enqueuing a refresh — a transient marker, not a resting state, since
#: the refresh moves it on to ``queued`` within the same handler call.
#: The Stage 3 nightly stale-sweep (Track B) takes a different route:
#: it refreshes a ``primed`` row whose last warm (``last_bootstrap_at``)
#: predates the freshness TTL by transitioning it straight to
#: ``queued``, so it never parks the row in this status.
PROPERTY_STATUS_STALE: Final[str] = "stale"

#: The last bootstrap attempt raised.  ``last_error`` carries the
#: short message; ``retry_count`` is the cumulative retry budget
#: spend since the last ``primed`` transition.
PROPERTY_STATUS_FAILED: Final[str] = "failed"


#: Closed set of valid status values — matches the CHECK
#: constraint on ``property_state.status`` exactly.  Adding a
#: new value here without a paired SQL migration would produce
#: a runtime ``IntegrityError`` on the next transition, which is
#: the desired contract: schema drift surfaces at write time,
#: not as silent data corruption.
ALLOWED_PROPERTY_STATUSES: Final[frozenset[str]] = frozenset(
    {
        PROPERTY_STATUS_COLD,
        PROPERTY_STATUS_QUEUED,
        PROPERTY_STATUS_WARMING,
        PROPERTY_STATUS_PRIMED,
        PROPERTY_STATUS_STALE,
        PROPERTY_STATUS_FAILED,
    },
)


def _utcnow() -> datetime:
    """Return a tz-aware ``datetime`` in UTC.

    Wrapping ``datetime.now(UTC)`` keeps the default_factory call
    site short enough to fit a single dataclass field line and
    makes the frozen-default semantics explicit (we never want a
    naive timestamp in this model — Postgres ``TIMESTAMPTZ`` will
    coerce, but the contract is "aware in, aware out").
    """

    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PropertyState:
    """One row of the ``property_state`` table.

    The field ordering and types mirror the migration column
    definitions one-for-one so a code reviewer can diff this
    against ``034_property_state.sql`` line by line.

    Attributes:
        property_channel_id: Short Cendra channel id used as the
            primary key (e.g. ``"598808"``).  Mirrors the PK on
            ``property_tenant_registry`` (migration 032).
        customer_id: Cendra customer UUID owning this property.
        org_id: Optional Cendra workspace UUID.  ``None`` carries
            the same semantics as ``TenantContext.org_id`` — drop
            the optional GraphQL filter rather than send NULL.
        provider_type: Upper-case PMS identifier
            (``"HOSTAWAY"``, ``"LODGIFY"``, ``"GUESTY"`` …).
        status: One of :data:`ALLOWED_PROPERTY_STATUSES`.
            Defaults to ``cold`` for freshly inserted rows.
        current_job_id: Bootstrap job actively warming this row,
            or ``None`` in any terminal state.
        intent_dedup_key: Hash used by Stage 2's Service Bus
            ``MessageId`` to drop duplicate enqueues.
        conversations_loaded: Count of OTA conversations the
            warming pass ingested.
        cases_extracted: Decision-cases emitted from those
            conversations.
        rules_emitted: ``PatternRule`` count produced by the
            miner on the freshly extracted cases.
        profile_built: True once
            :class:`PropertyProfileHarvester` has stored a
            profile for this property.
        window_days: Archive window (in days) the last warming
            pass requested.  ``None`` until the first primed
            transition.
        first_seen_at: When the row was created.  Populated by
            the store on insert, never overwritten.
        last_bootstrap_at: Timestamp of the most recent terminal
            transition (``primed`` or ``failed``).
        last_data_event_at: Timestamp of the most recent OTA
            event ingested for this property.  Drives
            ``primed → stale`` detection.
        last_error: Short error message from the most recent
            ``failed`` transition, or ``None`` after a successful
            recovery.
        retry_count: Cumulative transient failures since the
            last ``primed`` transition.
        updated_at: Wall-clock time of the most recent
            transition.  Mutated on every write.
    """

    property_channel_id: str
    customer_id: str
    provider_type: str
    org_id: str | None = None
    status: str = PROPERTY_STATUS_COLD
    current_job_id: str | None = None
    intent_dedup_key: str | None = None
    conversations_loaded: int = 0
    cases_extracted: int = 0
    rules_emitted: int = 0
    profile_built: bool = False
    window_days: int | None = None
    first_seen_at: datetime = field(default_factory=_utcnow)
    last_bootstrap_at: datetime | None = None
    last_data_event_at: datetime | None = None
    last_error: str | None = None
    retry_count: int = 0
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.status not in ALLOWED_PROPERTY_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_PROPERTY_STATUSES))
            raise ValueError(
                f"PropertyState.status={self.status!r} not in "
                f"{{{allowed}}}",
            )
        if self.conversations_loaded < 0:
            raise ValueError(
                "PropertyState.conversations_loaded must be >= 0",
            )
        if self.cases_extracted < 0:
            raise ValueError(
                "PropertyState.cases_extracted must be >= 0",
            )
        if self.rules_emitted < 0:
            raise ValueError(
                "PropertyState.rules_emitted must be >= 0",
            )
        if self.retry_count < 0:
            raise ValueError(
                "PropertyState.retry_count must be >= 0",
            )
        if self.window_days is not None and self.window_days <= 0:
            raise ValueError(
                "PropertyState.window_days must be positive or None",
            )
