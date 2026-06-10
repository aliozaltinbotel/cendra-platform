"""End-to-end behaviour of :class:`CertificateVerifier`."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.brain.certificates.issuer import (
    MIN_KEY_BYTES,
    CertificateIssuer,
)
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import AutonomyTier
from core.brain.certificates.verifier import (
    CertificateVerifier,
    VerifyOutcome,
)


def _policy() -> TierPolicy:
    """Pack-style tier ceilings (mirrors packs/hospitality/tier_defaults.yaml)."""
    return TierPolicy(
        {
            "send_message": AutonomyTier.COLLABORATOR,
            "issue_refund": AutonomyTier.APPROVER,
        }
    )


@pytest.fixture
def key() -> bytes:
    return secrets.token_bytes(MIN_KEY_BYTES)


@pytest.fixture
def issuer(key: bytes) -> CertificateIssuer:
    return CertificateIssuer(signing_key=key)


@pytest.fixture
def verifier(key: bytes) -> CertificateVerifier:
    return CertificateVerifier(signing_key=key, policy=_policy())


def test_happy_path(
    issuer: CertificateIssuer,
    verifier: CertificateVerifier,
) -> None:
    """Issued cert verifies successfully under same key."""
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    result = verifier.verify(
        cert=cert,
        action_kind="send_message",
        property_id="p",
        owner_id="o",
    )
    assert result.outcome is VerifyOutcome.OK
    assert result.ok is True


def test_wrong_key_detected(issuer: CertificateIssuer) -> None:
    """Verification under a different key returns BAD_SIGNATURE."""
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    other = CertificateVerifier(
        signing_key=secrets.token_bytes(MIN_KEY_BYTES),
        policy=_policy(),
    )
    result = other.verify(
        cert=cert,
        action_kind="send_message",
        property_id="p",
        owner_id="o",
    )
    assert result.outcome is VerifyOutcome.BAD_SIGNATURE


def test_tampered_signature_detected(
    issuer: CertificateIssuer,
    verifier: CertificateVerifier,
) -> None:
    """Mutating the signature triggers BAD_SIGNATURE."""
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    forged = replace(cert, signature_hex="0" * len(cert.signature_hex))
    result = verifier.verify(
        cert=forged,
        action_kind="send_message",
        property_id="p",
        owner_id="o",
    )
    assert result.outcome is VerifyOutcome.BAD_SIGNATURE


def test_expired_detected(
    issuer: CertificateIssuer,
    verifier: CertificateVerifier,
) -> None:
    """Expired cert returns EXPIRED."""
    issued = datetime(2026, 5, 10, tzinfo=UTC)
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
        ttl_seconds=10,
        issued_at=issued,
    )
    result = verifier.verify(
        cert=cert,
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        at=issued + timedelta(seconds=11),
    )
    assert result.outcome is VerifyOutcome.EXPIRED


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {"action_kind": "issue_refund"},
            VerifyOutcome.WRONG_ACTION,
        ),
        (
            {"property_id": "other"},
            VerifyOutcome.WRONG_PROPERTY,
        ),
        (
            {"owner_id": "other"},
            VerifyOutcome.WRONG_OWNER,
        ),
    ],
    ids=["wrong_action", "wrong_property", "wrong_owner"],
)
def test_scope_mismatch_detected(
    issuer: CertificateIssuer,
    verifier: CertificateVerifier,
    override: dict[str, object],
    expected: VerifyOutcome,
) -> None:
    """Scope-check failures map to distinct outcomes."""
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    requested: dict[str, object] = {
        "action_kind": "send_message",
        "property_id": "p",
        "owner_id": "o",
    }
    requested.update(override)
    result = verifier.verify(
        cert=cert,
        **requested,  # type: ignore[arg-type]
    )
    assert result.outcome is expected


def test_cert_exceeding_policy_rejected(
    issuer: CertificateIssuer,
    verifier: CertificateVerifier,
) -> None:
    """Cert at OPERATOR for ISSUE_REFUND exceeds policy ceiling."""
    cert = issuer.issue(
        action_kind="issue_refund",
        property_id="p",
        owner_id="o",
        granted_tier=AutonomyTier.OPERATOR,
    )
    result = verifier.verify(
        cert=cert,
        action_kind="issue_refund",
        property_id="p",
        owner_id="o",
    )
    assert result.outcome is VerifyOutcome.EXCEEDS_POLICY
    assert "policy ceiling" in result.rationale


def test_empty_signing_key_rejected() -> None:
    """Empty signing key raises at construction."""
    with pytest.raises(ValueError, match="signing_key"):
        CertificateVerifier(signing_key=b"", policy=_policy())
