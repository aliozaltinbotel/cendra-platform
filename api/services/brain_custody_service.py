"""Tenant-scoped custody service for receipt signing and HASH redaction.

This service closes the CEN-78/CEN-85 contract boundary between the
published verification-key registry and the callers that need private
key operations:

- ``sign_receipt(tenant_id, payload)`` resolves the active
  ``brain_signing_keys`` row for ``receipt_signing`` and returns only
  public receipt metadata plus a signature.
- ``hash_key_for(tenant_id, purpose)`` returns a
  :class:`core.brain.compliance.encryption.KeyHandle` for internal
  callers such as HASH redaction without exposing the underlying master
  secret.

The production seam intentionally matches the rest of the additive
brain runtime: BRAIN_* env vars configure projected-secret lookup, and
the actual secret bytes are resolved lazily from env vars whose names
are derived from the stored ``kms_key_ref``.  This keeps private bytes
inside the custody layer even when the deployment projects KMS-backed
secrets into the pod environment.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Final, Protocol, TypedDict

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.compliance.encryption import KeyDerivation, KeyHandle
from extensions.ext_database import db
from models.brain_signing_key import BrainSigningKey

logger = logging.getLogger(__name__)

__all__ = [
    "BrainCustodyConfigurationError",
    "BrainCustodyError",
    "BrainCustodyService",
    "BrainHashKeyNotConfiguredError",
    "BrainSigningKeyNotFoundError",
    "InMemoryBrainCustodyProvider",
    "ProjectedKMSBrainCustodyProvider",
    "SignedReceipt",
]

RECEIPT_SIGNING_PURPOSE: Final[str] = "receipt_signing"
SIGNING_ALGORITHM_ED25519: Final[str] = "ed25519"

_ALGORITHM_LABELS: Final[dict[str, str]] = {
    SIGNING_ALGORITHM_ED25519: "Ed25519",
}

_SECRET_ENV_PREFIX_ENV: Final[str] = "BRAIN_CUSTODY_SECRET_ENV_PREFIX"
_SECRET_ENV_PREFIX_DEFAULT: Final[str] = "BRAIN_CUSTODY_SECRET_"
_HASH_KEY_REFS_JSON_ENV: Final[str] = "BRAIN_HASH_KEY_REFS_JSON"
_HASH_KEY_REF_DEFAULT_ENV: Final[str] = "BRAIN_HASH_KEY_REF_DEFAULT"
_HASH_KEY_REF_TEMPLATE_ENV: Final[str] = "BRAIN_HASH_KEY_REF_TEMPLATE"
_HASH_KEY_REF_TEMPLATE_DEFAULT: Final[str] = "kms://tenants/{tenant_id}/hash/default"
_HASH_DERIVATION_NAMESPACE: Final[str] = "hash"


class SignedReceipt(TypedDict):
    key_id: str
    algorithm: str
    signature_hex: str


class BrainCustodyError(RuntimeError):
    """Base error for tenant custody failures."""


class BrainSigningKeyNotFoundError(BrainCustodyError):
    """Raised when no active signing key exists for a tenant."""


class BrainHashKeyNotConfiguredError(BrainCustodyError):
    """Raised when no hash-key reference can be resolved for a tenant."""


class BrainCustodyConfigurationError(BrainCustodyError):
    """Raised when projected custody material is missing or malformed."""


class BrainCustodyProvider(Protocol):
    """Low-level private-key operations behind the public custody service."""

    def sign(self, *, kms_key_ref: str, algorithm: str, payload: bytes) -> bytes:
        """Return a signature over ``payload`` using the private key at ``kms_key_ref``."""

    def public_key_base64url(self, *, kms_key_ref: str, algorithm: str) -> str:
        """Return the public key bound to ``kms_key_ref`` in base64url form."""

    def hash_key_for(self, *, tenant_id: str, purpose: str, kms_key_ref: str) -> KeyHandle:
        """Return a derived hash key for the tenant/purpose tuple."""


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


def _normalize_algorithm(value: str) -> str:
    return value.strip().lower()


def _display_algorithm(value: str) -> str:
    normalized = _normalize_algorithm(value)
    return _ALGORITHM_LABELS.get(normalized, value)


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _decode_base64_bytes(raw: str) -> bytes:
    normalized = raw.strip()
    if not normalized:
        raise BrainCustodyConfigurationError("projected custody secret is empty")
    padding = "=" * (-len(normalized) % 4)
    try:
        return base64.urlsafe_b64decode(normalized + padding)
    except (ValueError, binascii.Error) as exc:
        raise BrainCustodyConfigurationError("projected custody secret must be base64/base64url bytes") from exc


def _signing_key_from_secret(secret: bytes, *, kms_key_ref: str) -> Ed25519PrivateKey:
    if len(secret) != 32:
        raise BrainCustodyConfigurationError(
            f"{kms_key_ref} must resolve to a base64-encoded 32-byte Ed25519 seed; got {len(secret)} bytes"
        )
    try:
        return Ed25519PrivateKey.from_private_bytes(secret)
    except ValueError as exc:
        raise BrainCustodyConfigurationError(f"{kms_key_ref} does not contain a valid Ed25519 seed") from exc


def _public_key_base64url(private_key: Ed25519PrivateKey) -> str:
    public_key_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _base64url_encode(public_key_bytes)


def _hash_key_handle(*, tenant_id: str, purpose: str, kms_key_ref: str, master_key: bytes) -> KeyHandle:
    derived = KeyDerivation.derive(
        master_key=master_key,
        purpose=f"{_HASH_DERIVATION_NAMESPACE}:{purpose}",
        salt=tenant_id.encode("utf-8"),
    )
    fingerprint = hashlib.blake2b(master_key, digest_size=8).hexdigest()
    return KeyHandle(kid=f"{kms_key_ref}:{purpose}:{fingerprint}", key_bytes=derived)


def _kms_env_var(prefix: str, kms_key_ref: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", kms_key_ref).strip("_").upper()
    if not suffix:
        raise BrainCustodyConfigurationError("kms_key_ref required")
    return f"{prefix}{suffix}"


def _load_hash_key_refs(raw: str | None) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrainCustodyConfigurationError(
            f"{_HASH_KEY_REFS_JSON_ENV} must be a JSON object of tenant_id -> kms_key_ref"
        ) from exc
    if not isinstance(decoded, dict):
        raise BrainCustodyConfigurationError(f"{_HASH_KEY_REFS_JSON_ENV} must decode to an object")

    result: dict[str, str] = {}
    for tenant_id, kms_key_ref in decoded.items():
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise BrainCustodyConfigurationError(f"{_HASH_KEY_REFS_JSON_ENV} contains an empty tenant_id key")
        if not isinstance(kms_key_ref, str) or not kms_key_ref.strip():
            raise BrainCustodyConfigurationError(
                f"{_HASH_KEY_REFS_JSON_ENV} must map {tenant_id!r} to a non-empty kms_key_ref"
            )
        result[tenant_id.strip()] = kms_key_ref.strip()
    return result


class InMemoryBrainCustodyProvider:
    """Test seam that keeps signing and hash secrets in memory."""

    def __init__(
        self,
        *,
        signing_keys: Mapping[str, bytes] | None = None,
        hash_master_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        self._signing_keys = dict(signing_keys or {})
        self._hash_master_keys = dict(hash_master_keys or {})

    def sign(self, *, kms_key_ref: str, algorithm: str, payload: bytes) -> bytes:
        private_key = self._private_key(kms_key_ref=kms_key_ref, algorithm=algorithm)
        return private_key.sign(payload)

    def public_key_base64url(self, *, kms_key_ref: str, algorithm: str) -> str:
        private_key = self._private_key(kms_key_ref=kms_key_ref, algorithm=algorithm)
        return _public_key_base64url(private_key)

    def hash_key_for(self, *, tenant_id: str, purpose: str, kms_key_ref: str) -> KeyHandle:
        master_key = self._hash_master_keys.get(kms_key_ref)
        if master_key is None:
            raise BrainCustodyConfigurationError(f"hash master key {kms_key_ref!r} is not loaded")
        return _hash_key_handle(
            tenant_id=tenant_id,
            purpose=purpose,
            kms_key_ref=kms_key_ref,
            master_key=master_key,
        )

    def _private_key(self, *, kms_key_ref: str, algorithm: str) -> Ed25519PrivateKey:
        normalized = _normalize_algorithm(algorithm)
        if normalized != SIGNING_ALGORITHM_ED25519:
            raise BrainCustodyConfigurationError(f"unsupported signing algorithm {algorithm!r}")
        secret = self._signing_keys.get(kms_key_ref)
        if secret is None:
            raise BrainCustodyConfigurationError(f"signing key {kms_key_ref!r} is not loaded")
        return _signing_key_from_secret(secret, kms_key_ref=kms_key_ref)


class ProjectedKMSBrainCustodyProvider:
    """Production seam that resolves KMS-backed secrets from env projection.

    The deployment owns the actual KMS integration. The app receives the
    secret material through env vars derived from ``kms_key_ref`` so the
    service stays cloud-agnostic and upstream-friendly.
    """

    def __init__(self, *, secret_env_prefix: str | None = None) -> None:
        prefix = secret_env_prefix or os.environ.get(_SECRET_ENV_PREFIX_ENV, _SECRET_ENV_PREFIX_DEFAULT)
        self._secret_env_prefix = prefix.strip() or _SECRET_ENV_PREFIX_DEFAULT

    def sign(self, *, kms_key_ref: str, algorithm: str, payload: bytes) -> bytes:
        private_key = self._private_key(kms_key_ref=kms_key_ref, algorithm=algorithm)
        return private_key.sign(payload)

    def public_key_base64url(self, *, kms_key_ref: str, algorithm: str) -> str:
        private_key = self._private_key(kms_key_ref=kms_key_ref, algorithm=algorithm)
        return _public_key_base64url(private_key)

    def hash_key_for(self, *, tenant_id: str, purpose: str, kms_key_ref: str) -> KeyHandle:
        secret = self._secret_bytes_for(kms_key_ref)
        return _hash_key_handle(
            tenant_id=tenant_id,
            purpose=purpose,
            kms_key_ref=kms_key_ref,
            master_key=secret,
        )

    def _private_key(self, *, kms_key_ref: str, algorithm: str) -> Ed25519PrivateKey:
        normalized = _normalize_algorithm(algorithm)
        if normalized != SIGNING_ALGORITHM_ED25519:
            raise BrainCustodyConfigurationError(f"unsupported signing algorithm {algorithm!r}")
        return _signing_key_from_secret(self._secret_bytes_for(kms_key_ref), kms_key_ref=kms_key_ref)

    def _secret_bytes_for(self, kms_key_ref: str) -> bytes:
        env_var = _kms_env_var(self._secret_env_prefix, kms_key_ref)
        raw = os.environ.get(env_var, "")
        if not raw:
            raise BrainCustodyConfigurationError(
                f"{env_var} is not set; load the KMS-backed secret for {kms_key_ref}"
            )
        return _decode_base64_bytes(raw)


class BrainCustodyService:
    """Resolve tenant custody operations without leaking private bytes."""

    def __init__(
        self,
        *,
        session_maker: sessionmaker | None = None,
        custody_provider: BrainCustodyProvider | None = None,
        hash_key_refs: Mapping[str, str] | None = None,
        hash_key_ref_default: str | None = None,
        hash_key_ref_template: str | None = None,
    ) -> None:
        self._sessions = session_maker or _session_maker()
        self._custody = custody_provider or ProjectedKMSBrainCustodyProvider()
        self._hash_key_refs = dict(hash_key_refs) if hash_key_refs is not None else _load_hash_key_refs(
            os.environ.get(_HASH_KEY_REFS_JSON_ENV)
        )
        self._hash_key_ref_default = (
            hash_key_ref_default
            if hash_key_ref_default is not None
            else (os.environ.get(_HASH_KEY_REF_DEFAULT_ENV, "").strip() or None)
        )
        self._hash_key_ref_template = (
            hash_key_ref_template
            if hash_key_ref_template is not None
            else os.environ.get(_HASH_KEY_REF_TEMPLATE_ENV, _HASH_KEY_REF_TEMPLATE_DEFAULT)
        )

    def sign_receipt(self, tenant_id: str, payload: bytes | bytearray) -> SignedReceipt:
        """Sign canonical receipt bytes with the tenant's active published key."""
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise BrainSigningKeyNotFoundError("tenant_id required")
        if isinstance(payload, bytearray):
            payload = bytes(payload)
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")

        row = self._active_signing_key(normalized_tenant_id)
        normalized_algorithm = _normalize_algorithm(row.algorithm)
        actual_public_key = self._custody.public_key_base64url(
            kms_key_ref=row.kms_key_ref,
            algorithm=normalized_algorithm,
        )
        if actual_public_key != row.public_key_base64url:
            raise BrainCustodyConfigurationError(
                f"projected signer {row.kms_key_ref} does not match published public key for {row.key_id}"
            )

        signature = self._custody.sign(
            kms_key_ref=row.kms_key_ref,
            algorithm=normalized_algorithm,
            payload=payload,
        )
        return {
            "key_id": row.key_id,
            "algorithm": _display_algorithm(row.algorithm),
            "signature_hex": signature.hex(),
        }

    def hash_key_for(self, tenant_id: str, purpose: str) -> KeyHandle:
        """Return the tenant-scoped hash key for ``purpose``."""
        normalized_tenant_id = tenant_id.strip()
        normalized_purpose = purpose.strip()
        if not normalized_tenant_id:
            raise BrainHashKeyNotConfiguredError("tenant_id required")
        if not normalized_purpose:
            raise BrainHashKeyNotConfiguredError("purpose required")

        kms_key_ref = self._resolve_hash_key_ref(normalized_tenant_id)
        return self._custody.hash_key_for(
            tenant_id=normalized_tenant_id,
            purpose=normalized_purpose,
            kms_key_ref=kms_key_ref,
        )

    def _active_signing_key(self, tenant_id: str) -> BrainSigningKey:
        with self._sessions() as session:
            row = session.execute(
                select(BrainSigningKey)
                .where(
                    BrainSigningKey.tenant_id == tenant_id,
                    BrainSigningKey.purpose == RECEIPT_SIGNING_PURPOSE,
                    BrainSigningKey.status == "active",
                )
                .order_by(BrainSigningKey.activated_at.desc(), BrainSigningKey.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        if row is None:
            raise BrainSigningKeyNotFoundError(
                f"no active {RECEIPT_SIGNING_PURPOSE} key registered for tenant {tenant_id}"
            )
        return row

    def _resolve_hash_key_ref(self, tenant_id: str) -> str:
        if tenant_id in self._hash_key_refs:
            return self._hash_key_refs[tenant_id]
        if self._hash_key_ref_default:
            return self._hash_key_ref_default
        template = self._hash_key_ref_template.strip()
        if template:
            try:
                return template.format(tenant_id=tenant_id)
            except KeyError as exc:
                raise BrainCustodyConfigurationError(
                    f"{_HASH_KEY_REF_TEMPLATE_ENV} may only reference {{tenant_id}}"
                ) from exc
        raise BrainHashKeyNotConfiguredError(f"no hash-key reference configured for tenant {tenant_id}")
