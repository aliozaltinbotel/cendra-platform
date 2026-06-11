"""Service and model contracts for published verification keys."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models.brain_signing_key import BrainSigningKey
from services.brain_signing_key_service import BrainSigningKeyService

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainSigningKey.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _insert_key(
    session_maker: sessionmaker,
    *,
    tenant_id: str = TENANT,
    purpose: str = "receipt_signing",
    algorithm: str = "ed25519",
    key_id: str = "brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5",
    public_key_base64url: str = "ZHVtbXkta2V5",
    kms_key_ref: str = "kms://tenant/signers/active",
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


def test_get_verification_key_returns_public_shape_with_rfc3339_timestamps(session_maker) -> None:
    _insert_key(session_maker)
    _insert_key(
        session_maker,
        key_id="brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a6",
        status="retired",
        retired_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC).replace(tzinfo=None),
    )
    service = BrainSigningKeyService(session_maker=session_maker)

    active = service.get_verification_key("brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5")
    retired = service.get_verification_key("brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a6")

    assert active == {
        "key_id": "brk_ed25519_01976f88-b5d3-7c8a-b4ec-8d7db2d8e9a5",
        "algorithm": "Ed25519",
        "public_key_base64url": "ZHVtbXkta2V5",
        "status": "active",
        "activated_at": "2026-06-11T10:00:00Z",
        "retired_at": None,
    }
    assert retired is not None
    assert retired["status"] == "retired"
    assert retired["retired_at"] == "2026-06-12T09:30:00Z"


def test_list_verification_keys_scopes_to_tenant_and_filters_retired_rows(session_maker) -> None:
    _insert_key(session_maker, key_id="brk_ed25519_active")
    _insert_key(
        session_maker,
        key_id="brk_ed25519_retired",
        status="retired",
        retired_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC).replace(tzinfo=None),
    )
    _insert_key(
        session_maker,
        tenant_id=OTHER_TENANT,
        key_id="brk_ed25519_other_tenant",
    )
    service = BrainSigningKeyService(session_maker=session_maker)

    active_only = service.list_verification_keys(TENANT)
    with_retired = service.list_verification_keys(TENANT, include_retired=True)

    assert [key["key_id"] for key in active_only] == ["brk_ed25519_active"]
    assert [key["key_id"] for key in with_retired] == [
        "brk_ed25519_active",
        "brk_ed25519_retired",
    ]


def test_model_enforces_one_active_key_per_tenant_and_purpose(session_maker) -> None:
    _insert_key(session_maker, key_id="brk_ed25519_a1")

    with pytest.raises(IntegrityError):
        _insert_key(session_maker, key_id="brk_ed25519_a2")

    _insert_key(
        session_maker,
        key_id="brk_ed25519_r1",
        status="retired",
        retired_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC).replace(tzinfo=None),
    )


def test_model_enforces_global_key_id_uniqueness(session_maker) -> None:
    _insert_key(session_maker, key_id="brk_ed25519_dup")

    with pytest.raises(IntegrityError):
        _insert_key(
            session_maker,
            tenant_id=OTHER_TENANT,
            key_id="brk_ed25519_dup",
            status="retired",
            retired_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC).replace(tzinfo=None),
        )
