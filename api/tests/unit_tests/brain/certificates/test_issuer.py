"""Behaviour of :class:`CertificateIssuer`."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest

from core.brain.certificates.issuer import (
    MIN_KEY_BYTES,
    CertificateIssuer,
)
from core.brain.certificates.tier import AutonomyTier


@pytest.fixture
def signing_key() -> bytes:
    return secrets.token_bytes(MIN_KEY_BYTES)


def test_short_key_rejected() -> None:
    """Keys shorter than ``MIN_KEY_BYTES`` raise."""
    with pytest.raises(ValueError, match="signing_key"):
        CertificateIssuer(signing_key=b"too-short")


def test_zero_default_ttl_rejected(signing_key: bytes) -> None:
    """Non-positive TTL is rejected at construction."""
    with pytest.raises(ValueError, match="default_ttl_seconds"):
        CertificateIssuer(
            signing_key=signing_key,
            default_ttl_seconds=0,
        )


def test_issue_default_ttl(signing_key: bytes) -> None:
    """Default TTL produces a 1-hour expiry."""
    issuer = CertificateIssuer(signing_key=signing_key)
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    delta = (cert.expires_at - cert.issued_at).total_seconds()
    assert 3590 <= delta <= 3610


def test_issue_with_custom_ttl(signing_key: bytes) -> None:
    """Custom TTL is honoured."""
    issuer = CertificateIssuer(signing_key=signing_key)
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
        ttl_seconds=60,
    )
    delta = (cert.expires_at - cert.issued_at).total_seconds()
    assert 55 <= delta <= 65


def test_issue_negative_ttl_rejected(signing_key: bytes) -> None:
    """Negative ``ttl_seconds`` at issue time is rejected."""
    issuer = CertificateIssuer(signing_key=signing_key)
    with pytest.raises(ValueError, match="ttl_seconds"):
        issuer.issue(
            action_kind="send_message",
            property_id="p",
            owner_id="o",
            granted_tier=AutonomyTier.COLLABORATOR,
            ttl_seconds=0,
        )


def test_issue_signature_is_deterministic(
    signing_key: bytes,
) -> None:
    """Same inputs + key produce identical signatures."""
    issuer = CertificateIssuer(signing_key=signing_key)
    issued = datetime(2026, 5, 10, tzinfo=UTC)
    a = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
        cert_id="fixed",
        issued_at=issued,
    )
    b = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
        cert_id="fixed",
        issued_at=issued,
    )
    assert a.signature_hex == b.signature_hex
    assert a.cert_id == b.cert_id == "fixed"


def test_issue_with_naive_issued_at_rejected(
    signing_key: bytes,
) -> None:
    """tz-naive ``issued_at`` is rejected."""
    issuer = CertificateIssuer(signing_key=signing_key)
    with pytest.raises(ValueError, match="issued_at"):
        issuer.issue(
            action_kind="send_message",
            property_id="p",
            owner_id="o",
            granted_tier=AutonomyTier.COLLABORATOR,
            issued_at=datetime(2026, 5, 10),
        )
