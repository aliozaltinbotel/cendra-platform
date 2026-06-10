"""Invariants of :class:`AutonomyCertificate` and canonical payload."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.brain.certificates.cert import (
    AutonomyCertificate,
    canonical_payload,
)
from core.brain.certificates.tier import AutonomyTier


def _now() -> datetime:
    return datetime(2026, 5, 10, 12, 0, tzinfo=UTC)


def _cert(**overrides: object) -> AutonomyCertificate:
    base: dict[str, object] = {
        "cert_id": "abc123",
        "action_kind": "send_message",
        "property_id": "prop_x",
        "owner_id": "owner_x",
        "granted_tier": AutonomyTier.COLLABORATOR,
        "issued_at": _now(),
        "expires_at": _now() + timedelta(hours=1),
        "signature_hex": "deadbeef",
    }
    base.update(overrides)
    return AutonomyCertificate(**base)  # type: ignore[arg-type]


def test_certificate_is_immutable() -> None:
    """Certificate is a frozen dataclass."""
    cert = _cert()
    with pytest.raises((AttributeError, TypeError)):
        cert.cert_id = "x"  # type: ignore[misc]


def test_naive_issued_at_rejected() -> None:
    """tz-naive ``issued_at`` raises :class:`ValueError`."""
    with pytest.raises(ValueError, match="issued_at"):
        _cert(issued_at=datetime(2026, 5, 10))


def test_naive_expires_at_rejected() -> None:
    """tz-naive ``expires_at`` raises :class:`ValueError`."""
    with pytest.raises(ValueError, match="expires_at"):
        _cert(expires_at=datetime(2026, 5, 10))


def test_expires_must_be_after_issued() -> None:
    """``expires_at`` <= ``issued_at`` is rejected."""
    issued = _now()
    with pytest.raises(ValueError, match="expires_at"):
        _cert(issued_at=issued, expires_at=issued)
    with pytest.raises(ValueError, match="expires_at"):
        _cert(
            issued_at=issued,
            expires_at=issued - timedelta(seconds=1),
        )


def test_empty_signature_rejected() -> None:
    """Empty signature_hex raises :class:`ValueError`."""
    with pytest.raises(ValueError, match="signature_hex"):
        _cert(signature_hex="")


def test_is_expired_uses_at_when_provided() -> None:
    """``is_expired`` honours an explicit moment."""
    cert = _cert()
    assert cert.is_expired(at=_now() + timedelta(hours=2)) is True
    assert cert.is_expired(at=_now() - timedelta(seconds=1)) is False


def test_is_expired_at_must_be_aware() -> None:
    """tz-naive ``at`` raises :class:`ValueError`."""
    cert = _cert()
    with pytest.raises(ValueError, match="tz-aware"):
        cert.is_expired(at=datetime(2026, 5, 10))


def test_canonical_payload_is_stable() -> None:
    """Payload is deterministic byte sequence."""
    payload = canonical_payload(
        cert_id="abc",
        action_kind="send_message",
        property_id="prop",
        owner_id="own",
        granted_tier=AutonomyTier.OPERATOR,
        issued_at=_now(),
        expires_at=_now() + timedelta(hours=1),
    )
    assert payload == (b"abc|send_message|prop|own|operator|2026-05-10T12:00:00+00:00|2026-05-10T13:00:00+00:00")


def test_empty_action_kind_rejected() -> None:
    """Empty ``action_kind`` raises :class:`ValueError` (genericised port)."""
    with pytest.raises(ValueError, match="action_kind"):
        _cert(action_kind="")
