"""Consent registry for guest data processing.

Reference: ``brain_engine_advisory.md`` §4 — GDPR consent tracking.

Every PII-touching path inside the engine that operates on a *purpose
beyond contract performance* must consult this store before reading
or writing.  Examples:

* AI personalisation that re-uses past guest sentiment ⇒
  :class:`ConsentPurpose.AI_PERSONALISATION`.
* Automated marketing or upsell prompts ⇒ :class:`MARKETING`.
* Aggregated cross-property pattern mining that includes guest text
  ⇒ :class:`PATTERN_MINING`.

Contract performance — replying to the guest about *their own*
booking — is governed by GDPR Art. 6(1)(b) and does **not** route
through this store; that path is implicit and always allowed.

The Protocol surface is sync, deterministic, and side-effect-free
on read; production backs it with an asyncpg-backed store under
``brain_engine/store/pg_consent.py`` (out of scope for this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class ConsentPurpose(StrEnum):
    """Purposes the engine may pursue against guest data."""

    AI_PERSONALISATION = "ai_personalisation"
    MARKETING = "marketing"
    PATTERN_MINING = "pattern_mining"
    OPS_AUTOMATION = "ops_automation"
    VOICE_PROCESSING = "voice_processing"
    THIRD_PARTY_SHARING = "third_party_sharing"


class ConsentSource(StrEnum):
    """How the consent was captured — kept for the audit trail."""

    GUEST_PORTAL = "guest_portal"
    BOOKING_FLOW = "booking_flow"
    PM_PROXY = "pm_proxy"  # PM ticked it on guest's behalf
    LEGITIMATE_INTEREST = "legitimate_interest"
    IMPORTED = "imported"  # migrated from prior system


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """One immutable consent decision."""

    subject_id: str
    tenant_id: str
    purpose: ConsentPurpose
    granted: bool
    source: ConsentSource
    captured_at: datetime
    expires_at: datetime | None = None
    withdrawn_at: datetime | None = None
    evidence_ref: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("ConsentRecord.subject_id required")
        if not self.tenant_id:
            raise ValueError("ConsentRecord.tenant_id required")
        if self.expires_at is not None and (self.expires_at <= self.captured_at):
            raise ValueError(
                "ConsentRecord.expires_at must be after captured_at",
            )
        if self.withdrawn_at is not None and (self.withdrawn_at < self.captured_at):
            raise ValueError(
                "ConsentRecord.withdrawn_at must be ≥ captured_at",
            )

    def is_active(self, *, at: datetime) -> bool:
        """Return ``True`` if the record is in force at ``at``.

        The check folds together three independent "off" switches
        (granted=False, withdrawn, expired) into a single predicate
        callers can use without re-implementing the rules.
        """
        if not self.granted:
            return False
        if self.withdrawn_at is not None and at >= self.withdrawn_at:
            return False
        if self.expires_at is not None and at >= self.expires_at:
            return False
        return True


class ConsentStore(Protocol):
    """Storage contract for consent records."""

    def latest(
        self,
        *,
        subject_id: str,
        tenant_id: str,
        purpose: ConsentPurpose,
    ) -> ConsentRecord | None:
        """Return the most recent record or ``None``."""

    def record(self, event: ConsentRecord) -> None:
        """Persist a new record (append-only)."""

    def history(
        self,
        *,
        subject_id: str,
        tenant_id: str,
    ) -> tuple[ConsentRecord, ...]:
        """Return every record for this subject in capture order."""


class InMemoryConsentStore:
    """Reference implementation backing tests and dev fixtures.

    Production swaps this for an asyncpg-backed store; the Protocol
    keeps both interchangeable.  Records are append-only — writes
    never mutate prior entries.
    """

    def __init__(self) -> None:
        self._events: list[ConsentRecord] = []

    def latest(
        self,
        *,
        subject_id: str,
        tenant_id: str,
        purpose: ConsentPurpose,
    ) -> ConsentRecord | None:
        for event in reversed(self._events):
            if event.subject_id == subject_id and event.tenant_id == tenant_id and event.purpose == purpose:
                return event
        return None

    def record(self, event: ConsentRecord) -> None:
        self._events.append(event)

    def history(
        self,
        *,
        subject_id: str,
        tenant_id: str,
    ) -> tuple[ConsentRecord, ...]:
        return tuple(event for event in self._events if event.subject_id == subject_id and event.tenant_id == tenant_id)


def has_consent(
    store: ConsentStore,
    *,
    subject_id: str,
    tenant_id: str,
    purpose: ConsentPurpose,
    at: datetime | None = None,
) -> bool:
    """Convenience predicate — most call-sites only need a bool."""
    record = store.latest(
        subject_id=subject_id,
        tenant_id=tenant_id,
        purpose=purpose,
    )
    if record is None:
        return False
    return record.is_active(at=at or datetime.now(tz=UTC))
