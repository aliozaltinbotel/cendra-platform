"""Append-only audit log for PII / privileged-action access.

The audit logger is the regulator-facing surface.  Two contracts:

1. **Append-only.** Once an event is committed, no API path can
   modify or remove it.  Tamper detection comes from a chained
   blake2b digest — each event embeds the digest of the previous
   one, so any back-dated insertion breaks the chain.
2. **Survives a process crash.** The default backend is in-memory
   (used in tests).  Production wires an asyncpg-backed store via
   the ``AuditLogger`` Protocol; the chain digest is preserved on
   reconnect by reading the last committed event.

This module deliberately does not own the asyncpg backend — it
lives in ``brain_engine/store/pg_audit.py`` (next branch).  Today
we ship the Protocol, the in-memory implementation for tests, and
the immutable ``AuditEvent`` dataclass.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from core.brain.compliance.retention import DataClass

_GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable audit-log entry."""

    event_id: str
    occurred_at: datetime
    actor: str  # user id, service name, or "system"
    action: str  # verb in past tense — "read", "wrote"
    resource: str  # logical id (no PII)
    data_class: DataClass
    tenant_id: str
    metadata: dict[str, str] = field(default_factory=dict)
    prev_digest: str = _GENESIS

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("AuditEvent.event_id required")
        if not self.actor:
            raise ValueError("AuditEvent.actor required")
        if not self.tenant_id:
            raise ValueError("AuditEvent.tenant_id required")

    def chained_digest(self) -> str:
        """Compute the blake2b digest of this event + its predecessor.

        Encoded as JSON with sorted keys so the digest is deterministic
        across Python versions and dict insertion orders.
        """
        payload = asdict(self)
        payload["occurred_at"] = self.occurred_at.isoformat()
        payload["data_class"] = self.data_class.value
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.blake2b(encoded, digest_size=32).hexdigest()


class AuditLogger(Protocol):
    """Backend-agnostic append-only audit interface."""

    def append(self, event: AuditEvent) -> str:
        """Persist ``event`` and return its chained digest."""
        ...

    def last_digest(self, tenant_id: str) -> str:
        """Return the last committed digest for ``tenant_id``."""
        ...


class InMemoryAuditLogger:
    """Reference implementation for tests and dev sandboxes.

    NOT for production: the in-memory chain dies with the process.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._tail: dict[str, str] = {}

    def append(self, event: AuditEvent) -> str:
        if event.prev_digest != self._tail.get(
            event.tenant_id,
            _GENESIS,
        ):
            raise ValueError(
                "audit chain break — prev_digest mismatch",
            )
        digest = event.chained_digest()
        self._events.append(event)
        self._tail[event.tenant_id] = digest
        return digest

    def last_digest(self, tenant_id: str) -> str:
        return self._tail.get(tenant_id, _GENESIS)

    def all_events(self) -> list[AuditEvent]:
        """Snapshot of every event — read-only by contract."""
        return list(self._events)


def utc_now() -> datetime:
    """Tiny indirection so tests can monkeypatch the clock."""
    return datetime.now(tz=UTC)
