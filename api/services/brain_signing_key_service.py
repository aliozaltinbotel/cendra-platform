"""Read-side service for published verification keys (CEN-84).

This is the chassis adapter behind the verification-key publication
surface. It exposes the contract agreed in the CEN-78 plan:

- ``get_verification_key(key_id)`` for public third-party verification
- ``list_verification_keys(tenant_id, include_retired=...)`` for
  tenant-authenticated operator inventory

Only published verification metadata leaves this service. Purpose and KMS
locator stay internal so the public route never leaks anything beyond the
receipt-verification contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from extensions.ext_database import db
from models.brain_signing_key import BrainSigningKey


class BrainSigningKeyStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class PublishedVerificationKey(TypedDict):
    key_id: str
    algorithm: str
    public_key_base64url: str
    status: str
    activated_at: str
    retired_at: str | None


_ALGORITHM_LABELS: dict[str, str] = {
    "ed25519": "Ed25519",
}


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


def _utc_isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


class BrainSigningKeyService:
    """Read-only publication facade for verification-key metadata."""

    def __init__(self, *, session_maker: sessionmaker | None = None) -> None:
        self._sessions = session_maker or _session_maker()

    def get_verification_key(self, key_id: str) -> PublishedVerificationKey | None:
        normalized_key_id = key_id.strip()
        if not normalized_key_id:
            return None

        with self._sessions() as session:
            row = session.execute(
                select(BrainSigningKey).where(BrainSigningKey.key_id == normalized_key_id)
            ).scalar_one_or_none()
        if row is None:
            return None
        return self._serialize(row)

    def list_verification_keys(
        self,
        tenant_id: str,
        *,
        include_retired: bool = False,
    ) -> list[PublishedVerificationKey]:
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id required")

        stmt = (
            select(BrainSigningKey)
            .where(BrainSigningKey.tenant_id == normalized_tenant_id)
            .order_by(
                BrainSigningKey.status.asc(),
                BrainSigningKey.activated_at.desc(),
                BrainSigningKey.created_at.desc(),
            )
        )
        if not include_retired:
            stmt = stmt.where(BrainSigningKey.status == BrainSigningKeyStatus.ACTIVE.value)

        with self._sessions() as session:
            rows = session.execute(stmt).scalars().all()
        return [self._serialize(row) for row in rows]

    @staticmethod
    def _serialize(row: BrainSigningKey) -> PublishedVerificationKey:
        algorithm = row.algorithm.strip().lower()
        return {
            "key_id": row.key_id,
            "algorithm": _ALGORITHM_LABELS.get(algorithm, row.algorithm),
            "public_key_base64url": row.public_key_base64url,
            "status": row.status,
            "activated_at": _utc_isoformat(row.activated_at) or "",
            "retired_at": _utc_isoformat(row.retired_at),
        }
