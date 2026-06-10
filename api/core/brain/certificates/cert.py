"""Signed autonomy certificate value object.

A *certificate* authorises one (action_kind, property, owner) tuple
to execute at a given :class:`AutonomyTier` for a bounded time
window.  ``action_kind`` is an opaque, vertical-neutral string —
the action vocabulary itself is pack / tenant data (golden rule 4),
never kernel code; the reference's ``CardActionKind`` enum values
serialise to exactly these strings, so payloads stay wire-compatible.

The runtime middleware verifies the certificate's HMAC signature
before any tool-call so a forged or tampered cert is detectable in
constant time.

The signed payload is a stable, deterministic byte string:

    cert_id|action_kind|property_id|owner_id|granted_tier|
    issued_at_iso|expires_at_iso

The signature is hex-encoded so certificates can travel through
audit logs and JSON APIs without binary encoding gymnastics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.brain.certificates.tier import AutonomyTier

__all__ = [
    "AutonomyCertificate",
    "canonical_payload",
]


@dataclass(frozen=True, slots=True)
class AutonomyCertificate:
    """Tamper-evident authorisation token for one action component.

    Attributes:
        cert_id: Unique opaque identifier (issuer-assigned).
        action_kind: Action class the cert authorises (non-empty,
            vertical-defined string, e.g. ``"send_message"``).
        property_id: Property the cert is scoped to.
        owner_id: Owner the cert is scoped to.
        granted_tier: Highest autonomy the cert grants.
        issued_at: UTC instant the cert was issued.
        expires_at: UTC instant after which the cert is invalid.
        signature_hex: Hex-encoded HMAC over the canonical payload.
    """

    cert_id: str
    action_kind: str
    property_id: str
    owner_id: str
    granted_tier: AutonomyTier
    issued_at: datetime
    expires_at: datetime
    signature_hex: str

    def __post_init__(self) -> None:
        """Validate datetimes are tz-aware and ordering is correct."""
        if not self.action_kind:
            raise ValueError("action_kind required")
        if self.issued_at.tzinfo is None:
            raise ValueError("issued_at must be tz-aware")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be tz-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be strictly after issued_at")
        if not self.signature_hex:
            raise ValueError("signature_hex must be non-empty")

    def is_expired(self, *, at: datetime | None = None) -> bool:
        """Return ``True`` when the certificate has expired."""
        moment = at or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("`at` must be tz-aware when provided")
        return moment >= self.expires_at


def canonical_payload(
    *,
    cert_id: str,
    action_kind: str,
    property_id: str,
    owner_id: str,
    granted_tier: AutonomyTier,
    issued_at: datetime,
    expires_at: datetime,
) -> bytes:
    """Return the canonical UTF-8 bytes the HMAC signs.

    The format is intentionally simple — a pipe-separated string of
    primitive fields — so any verifier in any language reproduces
    exactly the same payload.  ISO-8601 with explicit ``+00:00``
    offset removes any locale ambiguity.
    """
    parts = (
        cert_id,
        action_kind,
        property_id,
        owner_id,
        granted_tier.value,
        _iso(issued_at),
        _iso(expires_at),
    )
    return "|".join(parts).encode("utf-8")


def _iso(moment: datetime) -> str:
    """Render a tz-aware ``datetime`` as ISO-8601 with UTC offset."""
    if moment.tzinfo is None:
        raise ValueError("moment must be tz-aware")
    return moment.astimezone(UTC).isoformat()
