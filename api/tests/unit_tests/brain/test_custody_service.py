"""Contracts for tenant receipt signing and hash-key custody."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.brain.compliance.encryption import KeyHandle
from models.brain_signing_key import BrainSigningKey
from services.brain_custody_service import (
    BrainCustodyConfigurationError,
    BrainCustodyService,
    BrainSigningKeyNotFoundError,
    InMemoryBrainCustodyProvider,
)
from services.brain_signing_key_service import BrainSigningKeyService

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
OLD_SIGNER_REF = "kms://tenant/signers/old"
NEW_SIGNER_REF = "kms://tenant/signers/new"
HASH_REF = "kms://tenant/hash/default"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainSigningKey.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _seed(byte: int) -> bytes:
    return bytes([byte]) * 32


def _public_key_base64url(seed: bytes) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_key_bytes = private_key.public_key().public_bytes_raw()
    return base64.urlsafe_b64encode(public_key_bytes).decode("utf-8").rstrip("=")


def _public_key_from_verification_key(key: dict[str, str | None]) -> Ed25519PublicKey:
    raw = key["public_key_base64url"]
    assert isinstance(raw, str)
    padding = "=" * (-len(raw) % 4)
    return Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(raw + padding))


def _insert_key(
    session_maker: sessionmaker,
    *,
    tenant_id: str = TENANT,
    purpose: str = "receipt_signing",
    algorithm: str = "ed25519",
    key_id: str,
    public_key_base64url: str,
    kms_key_ref: str,
    status: str = "active",
    activated_at: datetime | None = None,
    retired_at: datetime | None = None,
) -> BrainSigningKey:
    row = BrainSigningKey(
        tenant_id=tenant_id,
        purpose=purpose,
        algorithm=algorithm,
        key_id=key_id,
        public_key_base64url=public_key_base64url,
        kms_key_ref=kms_key_ref,
        status=status,
        activated_at=activated_at or datetime(2026, 6, 11, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        retired_at=retired_at,
    )
    with session_maker() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _rotate_active_key(
    session_maker: sessionmaker,
    *,
    retired_at: datetime,
    replacement_key_id: str,
    replacement_public_key: str,
    replacement_kms_ref: str,
) -> None:
    with session_maker() as session:
        existing = session.execute(
            select(BrainSigningKey).where(
                BrainSigningKey.tenant_id == TENANT,
                BrainSigningKey.purpose == "receipt_signing",
                BrainSigningKey.status == "active",
            )
        ).scalar_one()
        existing.status = "retired"
        existing.retired_at = retired_at.replace(tzinfo=None)
        session.add(
            BrainSigningKey(
                tenant_id=TENANT,
                purpose="receipt_signing",
                algorithm="ed25519",
                key_id=replacement_key_id,
                public_key_base64url=replacement_public_key,
                kms_key_ref=replacement_kms_ref,
                status="active",
                activated_at=retired_at.replace(tzinfo=None),
                retired_at=None,
            )
        )
        session.commit()


def test_sign_receipt_returns_key_metadata_and_verifiable_signature(session_maker) -> None:
    seed = _seed(1)
    payload = b'{"decision_id":"d1","signed":true}'
    _insert_key(
        session_maker,
        key_id="brk_ed25519_v1",
        public_key_base64url=_public_key_base64url(seed),
        kms_key_ref=OLD_SIGNER_REF,
    )
    service = BrainCustodyService(
        session_maker=session_maker,
        custody_provider=InMemoryBrainCustodyProvider(signing_keys={OLD_SIGNER_REF: seed}),
    )

    signed = service.sign_receipt(TENANT, payload)

    assert signed["key_id"] == "brk_ed25519_v1"
    assert signed["algorithm"] == "Ed25519"
    signature = bytes.fromhex(signed["signature_hex"])
    Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(signature, payload)


def test_rotation_changes_key_id_and_old_rows_remain_verifiable(session_maker) -> None:
    old_seed = _seed(2)
    new_seed = _seed(3)
    old_key_id = "brk_ed25519_old"
    new_key_id = "brk_ed25519_new"
    old_payload = b'{"decision_id":"old","signed":true}'
    new_payload = b'{"decision_id":"new","signed":true}'

    _insert_key(
        session_maker,
        key_id=old_key_id,
        public_key_base64url=_public_key_base64url(old_seed),
        kms_key_ref=OLD_SIGNER_REF,
    )
    service = BrainCustodyService(
        session_maker=session_maker,
        custody_provider=InMemoryBrainCustodyProvider(
            signing_keys={
                OLD_SIGNER_REF: old_seed,
                NEW_SIGNER_REF: new_seed,
            }
        ),
    )

    old_signed = service.sign_receipt(TENANT, old_payload)
    _rotate_active_key(
        session_maker,
        retired_at=datetime(2026, 6, 12, 8, 30, tzinfo=UTC),
        replacement_key_id=new_key_id,
        replacement_public_key=_public_key_base64url(new_seed),
        replacement_kms_ref=NEW_SIGNER_REF,
    )
    new_signed = service.sign_receipt(TENANT, new_payload)

    verification_service = BrainSigningKeyService(session_maker=session_maker)
    old_key = verification_service.get_verification_key(old_key_id)
    new_key = verification_service.get_verification_key(new_key_id)

    assert old_signed["key_id"] == old_key_id
    assert new_signed["key_id"] == new_key_id
    assert old_key is not None
    assert new_key is not None

    _public_key_from_verification_key(old_key).verify(bytes.fromhex(old_signed["signature_hex"]), old_payload)
    _public_key_from_verification_key(new_key).verify(bytes.fromhex(new_signed["signature_hex"]), new_payload)


def test_sign_receipt_raises_when_no_active_key_registered(session_maker) -> None:
    service = BrainCustodyService(session_maker=session_maker, custody_provider=InMemoryBrainCustodyProvider())

    with pytest.raises(BrainSigningKeyNotFoundError, match="no active receipt_signing key"):
        service.sign_receipt(TENANT, b"{}")


def test_sign_receipt_rejects_registry_and_projected_key_mismatch(session_maker) -> None:
    _insert_key(
        session_maker,
        key_id="brk_ed25519_mismatch",
        public_key_base64url=_public_key_base64url(_seed(4)),
        kms_key_ref=OLD_SIGNER_REF,
    )
    service = BrainCustodyService(
        session_maker=session_maker,
        custody_provider=InMemoryBrainCustodyProvider(signing_keys={OLD_SIGNER_REF: _seed(5)}),
    )

    with pytest.raises(BrainCustodyConfigurationError, match="does not match published public key"):
        service.sign_receipt(TENANT, b'{"decision_id":"bad"}')


def test_hash_key_for_is_stable_per_tenant_and_purpose(session_maker) -> None:
    service = BrainCustodyService(
        session_maker=session_maker,
        custody_provider=InMemoryBrainCustodyProvider(hash_master_keys={HASH_REF: b"master-secret-012345"}),
        hash_key_ref_default=HASH_REF,
    )

    first = service.hash_key_for(TENANT, "moderation_pii_redaction")
    second = service.hash_key_for(TENANT, "moderation_pii_redaction")
    other_purpose = service.hash_key_for(TENANT, "audit")
    other_tenant = service.hash_key_for(OTHER_TENANT, "moderation_pii_redaction")

    assert isinstance(first, KeyHandle)
    assert first.key_bytes == second.key_bytes
    assert first.kid == second.kid
    assert first.key_bytes != other_purpose.key_bytes
    assert first.key_bytes != other_tenant.key_bytes


def test_projected_env_provider_supports_signing_and_hash_template(session_maker, monkeypatch) -> None:
    signer_seed = _seed(6)
    hash_secret = b"hash-master-secret"
    signer_env = "TEST_CUSTODY_KMS_TENANT_SIGNERS_ACTIVE"
    hash_env = "TEST_CUSTODY_KMS_TENANTS_11111111_1111_1111_1111_111111111111_HASH_DEFAULT"
    payload = b'{"decision_id":"env","signed":true}'
    _insert_key(
        session_maker,
        key_id="brk_ed25519_env",
        public_key_base64url=_public_key_base64url(signer_seed),
        kms_key_ref="kms://tenant/signers/active",
    )
    monkeypatch.setenv("BRAIN_CUSTODY_SECRET_ENV_PREFIX", "TEST_CUSTODY_")
    monkeypatch.setenv(signer_env, base64.urlsafe_b64encode(signer_seed).decode("utf-8").rstrip("="))
    monkeypatch.setenv(hash_env, base64.urlsafe_b64encode(hash_secret).decode("utf-8").rstrip("="))
    monkeypatch.setenv("BRAIN_HASH_KEY_REF_TEMPLATE", "kms://tenants/{tenant_id}/hash/default")

    service = BrainCustodyService(session_maker=session_maker)

    signed = service.sign_receipt(TENANT, payload)
    hash_key = service.hash_key_for(TENANT, "moderation_pii_redaction")

    assert signed["key_id"] == "brk_ed25519_env"
    assert hash_key.kid.startswith("kms://tenants/11111111-1111-1111-1111-111111111111/hash/default:")
    Ed25519PrivateKey.from_private_bytes(signer_seed).public_key().verify(
        bytes.fromhex(signed["signature_hex"]),
        payload,
    )


def test_signature_verification_fails_when_payload_changes(session_maker) -> None:
    seed = _seed(7)
    payload = b'{"decision_id":"stable","signed":true}'
    tampered_payload = b'{"decision_id":"stable","signed":false}'
    _insert_key(
        session_maker,
        key_id="brk_ed25519_tamper",
        public_key_base64url=_public_key_base64url(seed),
        kms_key_ref=OLD_SIGNER_REF,
    )
    service = BrainCustodyService(
        session_maker=session_maker,
        custody_provider=InMemoryBrainCustodyProvider(signing_keys={OLD_SIGNER_REF: seed}),
    )

    signed = service.sign_receipt(TENANT, payload)

    with pytest.raises(InvalidSignature):
        Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(
            bytes.fromhex(signed["signature_hex"]),
            tampered_payload,
        )
