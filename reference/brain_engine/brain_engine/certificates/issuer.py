"""Issue HMAC-signed :class:`AutonomyCertificate` records.

The issuer holds the long-lived signing key and stamps each cert
with a unique opaque ``cert_id``, the canonical payload, and the
hex-encoded HMAC-SHA256 signature.  Verification (in
:mod:`brain_engine.certificates.verifier`) reconstructs the same
payload, recomputes the HMAC, and compares with constant-time
:func:`hmac.compare_digest`.

The signing key is supplied by the caller — production wiring
loads it from the ``BRAIN_AUTONOMY_CERT_KEY`` environment variable
via the existing pydantic-settings layer; tests pass an explicit
short key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Final

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.certificates.cert import (
    AutonomyCertificate,
    canonical_payload,
)
from brain_engine.certificates.tier import AutonomyTier


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MIN_KEY_BYTES",
    "CertificateIssuer",
]


DEFAULT_TTL_SECONDS: Final[int] = 3600
MIN_KEY_BYTES: Final[int] = 32


class CertificateIssuer:
    """Mint signed :class:`AutonomyCertificate` records.

    The issuer is intentionally minimal — no persistence, no
    revocation list — so it stays trivially deterministic.
    Revocation lives upstream (the autonomy engine simply stops
    issuing fresh certs for a tenant).
    """

    def __init__(
        self,
        *,
        signing_key: bytes,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if len(signing_key) < MIN_KEY_BYTES:
            raise ValueError(
                f"signing_key must be at least {MIN_KEY_BYTES} "
                f"bytes; got {len(signing_key)}"
            )
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive")
        self._key = signing_key
        self._default_ttl = default_ttl_seconds

    def issue(
        self,
        *,
        action_kind: CardActionKind,
        property_id: str,
        owner_id: str,
        granted_tier: AutonomyTier,
        ttl_seconds: int | None = None,
        cert_id: str | None = None,
        issued_at: datetime | None = None,
    ) -> AutonomyCertificate:
        """Return a fresh signed certificate for the given tuple.

        Args:
            action_kind: Action class the cert authorises.
            property_id: Property the cert is scoped to.
            owner_id: Owner the cert is scoped to.
            granted_tier: Autonomy tier the cert grants.
            ttl_seconds: Override TTL; defaults to
                :attr:`default_ttl_seconds`.
            cert_id: Override identifier; defaults to a 16-byte
                URL-safe random token.
            issued_at: Override issuance instant; defaults to
                ``datetime.now(timezone.utc)``.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        issued = issued_at or datetime.now(timezone.utc)
        if issued.tzinfo is None:
            raise ValueError("issued_at must be tz-aware")
        expires = issued + timedelta(seconds=ttl)
        identifier = cert_id or secrets.token_urlsafe(16)
        payload = canonical_payload(
            cert_id=identifier,
            action_kind=action_kind,
            property_id=property_id,
            owner_id=owner_id,
            granted_tier=granted_tier,
            issued_at=issued,
            expires_at=expires,
        )
        signature = hmac.new(
            self._key, payload, hashlib.sha256,
        ).hexdigest()
        return AutonomyCertificate(
            cert_id=identifier,
            action_kind=action_kind,
            property_id=property_id,
            owner_id=owner_id,
            granted_tier=granted_tier,
            issued_at=issued,
            expires_at=expires,
            signature_hex=signature,
        )
