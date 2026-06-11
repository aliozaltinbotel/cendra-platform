"""Tests for the Ed25519 criticality-receipt envelope (CEN-79).

Covers the PRD §2 contract: canonical signed bytes, the auth-cert /
receipt split invariants, verification failure modes, the unsigned
no-key fallback, and rotation lookup by immutable ``key_id``.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.brain.certificates.receipt import (
    ReceiptEnvelope,
    ReceiptVerifyOutcome,
    canonical_receipt_payload,
    seal_receipt,
    verify_receipt,
)
from core.brain.compliance.art12_decision import (
    Art12Decision,
    HandlerSolver,
    canonical_record,
    chained_digest,
)

OCCURRED_AT = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def _decision(**overrides: object) -> Art12Decision:
    fields: dict = {
        "decision_id": "dec-001",
        "occurred_at": OCCURRED_AT,
        "property_id": "prop-1",
        "owner_id": "owner-1",
        "action_kind": "send_message",
        "handler_solver": HandlerSolver.LLM,
        "rationale": "guest asked for the wifi code",
        "provenance_digest": "a" * 64,
    }
    fields.update(overrides)
    return Art12Decision(**fields)


def _public_key_base64url(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


class FakeCustodySigner:
    """In-memory stand-in for ``BrainCustodyService.sign_receipt``.

    Returns the same public-metadata mapping shape as the custody
    contract: ``key_id`` / ``algorithm`` / ``signature_hex``.
    """

    def __init__(self, *, key_id: str, private_key: Ed25519PrivateKey, algorithm: str = "Ed25519") -> None:
        self.key_id = key_id
        self.algorithm = algorithm
        self._private_key = private_key
        self.calls: list[tuple[str, bytes]] = []

    def sign_receipt(self, tenant_id: str, payload: bytes | bytearray) -> dict[str, str]:
        payload_bytes = bytes(payload)
        self.calls.append((tenant_id, payload_bytes))
        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "signature_hex": self._private_key.sign(payload_bytes).hex(),
        }


@pytest.fixture
def private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def signer(private_key: Ed25519PrivateKey) -> FakeCustodySigner:
    return FakeCustodySigner(key_id="brk_ed25519_0001", private_key=private_key)


class TestCanonicalPayload:
    def test_matches_art12_canonical_record(self) -> None:
        decision = _decision()
        assert canonical_receipt_payload(decision) == canonical_record(decision)

    def test_deterministic_across_extra_insertion_order(self) -> None:
        first = _decision(extra={"a": "1", "b": "2"})
        second = _decision(extra={"b": "2", "a": "1"})
        assert canonical_receipt_payload(first) == canonical_receipt_payload(second)

    def test_sorted_keys_compact_separators_utf8(self) -> None:
        payload = canonical_receipt_payload(_decision(rationale="ünïcode rationale"))
        decoded = json.loads(payload.decode("utf-8"))
        assert list(decoded) == sorted(decoded)
        assert b", " not in payload
        assert "ünïcode rationale" in payload.decode("utf-8")


class TestSealReceipt:
    def test_signed_envelope_carries_custody_metadata(self, signer: FakeCustodySigner) -> None:
        decision = _decision()
        envelope = seal_receipt(decision, tenant_id="tenant-1", signer=signer)

        assert envelope.signed is True
        assert envelope.key_id == "brk_ed25519_0001"
        assert envelope.algorithm == "Ed25519"
        assert envelope.signature_hex
        assert envelope.record_digest == chained_digest(decision)
        # the signer received exactly the canonical bytes
        assert signer.calls == [("tenant-1", canonical_receipt_payload(decision))]

    def test_no_key_fallback_mints_unsigned_envelope(self) -> None:
        decision = _decision()
        envelope = seal_receipt(decision, tenant_id="tenant-1", signer=None)

        assert envelope.signed is False
        assert envelope.key_id is None
        assert envelope.algorithm is None
        assert envelope.signature_hex is None
        assert envelope.record_digest == chained_digest(decision)

    def test_blank_tenant_id_rejected(self, signer: FakeCustodySigner) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            seal_receipt(_decision(), tenant_id="  ", signer=signer)


class TestEnvelopeInvariants:
    def test_signed_without_signature_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match="signed envelope"):
            ReceiptEnvelope(record=_decision(), record_digest="0" * 64, signed=True)

    def test_unsigned_with_signature_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsigned envelope"):
            ReceiptEnvelope(
                record=_decision(),
                record_digest="0" * 64,
                signed=False,
                key_id="brk_ed25519_0001",
            )

    def test_bad_record_digest_rejected(self) -> None:
        with pytest.raises(ValueError, match="record_digest"):
            ReceiptEnvelope(record=_decision(), record_digest="abc", signed=False)


class TestVerifyReceipt:
    def test_round_trip_verifies(self, signer: FakeCustodySigner, private_key: Ed25519PrivateKey) -> None:
        registry = {"brk_ed25519_0001": _public_key_base64url(private_key)}
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)

        result = verify_receipt(envelope, key_lookup=registry.get)

        assert result.ok
        assert result.outcome is ReceiptVerifyOutcome.OK

    def test_unsigned_envelope_reports_unsigned(self) -> None:
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=None)
        result = verify_receipt(envelope, key_lookup=lambda _key_id: None)
        assert not result.ok
        assert result.outcome is ReceiptVerifyOutcome.UNSIGNED

    def test_tampered_record_fails_verification(
        self, signer: FakeCustodySigner, private_key: Ed25519PrivateKey
    ) -> None:
        registry = {"brk_ed25519_0001": _public_key_base64url(private_key)}
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)
        tampered = _decision(rationale="a different story")
        forged = ReceiptEnvelope(
            record=tampered,
            record_digest=chained_digest(tampered),
            signed=True,
            key_id=envelope.key_id,
            algorithm=envelope.algorithm,
            signature_hex=envelope.signature_hex,
        )

        result = verify_receipt(forged, key_lookup=registry.get)

        assert result.outcome is ReceiptVerifyOutcome.BAD_SIGNATURE

    def test_signature_from_other_key_fails(self, signer: FakeCustodySigner) -> None:
        other_key = Ed25519PrivateKey.generate()
        registry = {"brk_ed25519_0001": _public_key_base64url(other_key)}
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)

        result = verify_receipt(envelope, key_lookup=registry.get)

        assert result.outcome is ReceiptVerifyOutcome.BAD_SIGNATURE

    def test_unknown_key_id_reported_distinctly(self, signer: FakeCustodySigner) -> None:
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)
        result = verify_receipt(envelope, key_lookup=lambda _key_id: None)
        assert result.outcome is ReceiptVerifyOutcome.UNKNOWN_KEY

    def test_unsupported_algorithm_reported(self, private_key: Ed25519PrivateKey) -> None:
        hmac_labelled = FakeCustodySigner(key_id="brk_ed25519_0001", private_key=private_key, algorithm="HMAC-SHA256")
        registry = {"brk_ed25519_0001": _public_key_base64url(private_key)}
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=hmac_labelled)

        result = verify_receipt(envelope, key_lookup=registry.get)

        assert result.outcome is ReceiptVerifyOutcome.UNSUPPORTED_ALGORITHM

    def test_malformed_published_key_reported(self, signer: FakeCustodySigner) -> None:
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)
        result = verify_receipt(envelope, key_lookup=lambda _key_id: "not-32-bytes")
        assert result.outcome is ReceiptVerifyOutcome.MALFORMED_KEY

    def test_malformed_signature_hex_reported(self, private_key: Ed25519PrivateKey) -> None:
        registry = {"brk_ed25519_0001": _public_key_base64url(private_key)}
        envelope = ReceiptEnvelope(
            record=_decision(),
            record_digest=chained_digest(_decision()),
            signed=True,
            key_id="brk_ed25519_0001",
            algorithm="Ed25519",
            signature_hex="zz-not-hex",
        )

        result = verify_receipt(envelope, key_lookup=registry.get)

        assert result.outcome is ReceiptVerifyOutcome.MALFORMED_SIGNATURE


class TestRotationLookup:
    def test_receipt_signed_under_rotated_out_key_still_verifies(self) -> None:
        old_key = Ed25519PrivateKey.generate()
        new_key = Ed25519PrivateKey.generate()
        registry = {
            "brk_ed25519_old": _public_key_base64url(old_key),
            "brk_ed25519_new": _public_key_base64url(new_key),
        }
        old_signer = FakeCustodySigner(key_id="brk_ed25519_old", private_key=old_key)
        historical = seal_receipt(_decision(), tenant_id="tenant-1", signer=old_signer)

        new_signer = FakeCustodySigner(key_id="brk_ed25519_new", private_key=new_key)
        current = seal_receipt(_decision(decision_id="dec-002"), tenant_id="tenant-1", signer=new_signer)

        assert verify_receipt(historical, key_lookup=registry.get).ok
        assert verify_receipt(current, key_lookup=registry.get).ok

    def test_lookup_receives_the_stamped_key_id(self, signer: FakeCustodySigner) -> None:
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)
        seen: list[str] = []

        def lookup(key_id: str) -> str | None:
            seen.append(key_id)
            return None

        verify_receipt(envelope, key_lookup=lookup)
        assert seen == ["brk_ed25519_0001"]

    def test_unpadded_and_padded_base64url_keys_both_accepted(self, private_key: Ed25519PrivateKey) -> None:
        signer = FakeCustodySigner(key_id="brk_ed25519_0001", private_key=private_key)
        envelope = seal_receipt(_decision(), tenant_id="tenant-1", signer=signer)
        unpadded = _public_key_base64url(private_key)
        padded = unpadded + "=" * (-len(unpadded) % 4)

        assert verify_receipt(envelope, key_lookup=lambda _k: unpadded).ok
        assert verify_receipt(envelope, key_lookup=lambda _k: padded).ok


class TestAuthCertReceiptSplit:
    def test_auth_cert_hmac_path_is_untouched_and_distinct(self) -> None:
        # The split is structural: the HMAC issuer/verifier pair stays the
        # authorization-certificate path and exposes no receipt API.
        from core.brain.certificates import issuer as issuer_module
        from core.brain.certificates import receipt as receipt_module
        from core.brain.certificates.issuer import CertificateIssuer

        assert "seal_receipt" not in dir(CertificateIssuer)
        assert not hasattr(issuer_module, "seal_receipt")
        assert not hasattr(receipt_module, "CertificateIssuer")
        # receipt module never imports the HMAC machinery
        import inspect

        source = inspect.getsource(receipt_module)
        assert "import hmac" not in source
        assert not hasattr(receipt_module, "hmac")
