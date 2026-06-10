"""At-rest encryption primitives for sensitive memory tiers.

Reference: ``brain_engine_advisory.md`` §4 (3) — encryption at rest.

Two layers:

* :class:`KeyDerivation` — HKDF-blake2b over a master secret to fan
  out per-purpose sub-keys.  Pure stdlib, no external dependency, so
  the module loads even on a slim runtime image.
* :class:`Encryptor` Protocol with one production implementation
  (:class:`AESGCMEncryptor`) that lazy-imports ``cryptography``.  The
  Protocol seam keeps unit tests independent of the AES library —
  ``InMemoryEncryptor`` (a noop) is enough to exercise the cascade.

The module never holds plaintext keys at module scope.  Master
secrets enter via :class:`EnvKeyProvider` (or future :class:`KeyVault…`
provider in ``core.brain.security``) and stay inside the provider's
closure.  Logging or repr-ing a :class:`KeyHandle` returns its ``kid``
only — never the bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Protocol, override


@dataclass(frozen=True, slots=True)
class KeyHandle:
    """Opaque pointer to a derived key.

    ``key_bytes`` is held by reference for the lifetime of the
    handle; callers must not log it.  ``kid`` is the safe-to-log
    identifier that ties an audit-log entry back to a specific key
    rotation.
    """

    kid: str
    key_bytes: bytes

    def __post_init__(self) -> None:
        if not self.kid:
            raise ValueError("KeyHandle.kid required")
        if len(self.key_bytes) < 16:
            raise ValueError("KeyHandle.key_bytes must be ≥ 16 bytes")

    @override
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"KeyHandle(kid={self.kid!r})"


class EncryptionKeyProvider(Protocol):
    """Resolves a logical purpose to a derived :class:`KeyHandle`."""

    def key_for(self, purpose: str) -> KeyHandle:
        """Return the active key for ``purpose``."""


class KeyDerivation:
    """HKDF-style derivation over blake2b.

    Spec: RFC 5869, but with blake2b as the underlying hash so we
    avoid pulling in ``cryptography`` for code paths that only need
    deterministic sub-keys (e.g. blake2b-MAC redaction).  Output
    length is fixed at 32 bytes — sufficient for AES-256 keys and
    blake2b MAC keys; widen via a follow-up if the engine ever needs
    longer output.
    """

    _HASH_LEN = 32

    @classmethod
    def derive(
        cls,
        *,
        master_key: bytes,
        purpose: str,
        salt: bytes = b"",
    ) -> bytes:
        """Return a 32-byte sub-key for ``purpose``.

        ``salt`` is optional but recommended — typically the rotation
        epoch so the same purpose under a fresh master gives a
        distinct sub-key without bumping the call-site.
        """
        if len(master_key) < 16:
            raise ValueError("master_key must be ≥ 16 bytes")
        if not purpose:
            raise ValueError("purpose required")
        prk = hmac.new(
            salt or b"\x00" * cls._HASH_LEN,
            master_key,
            hashlib.blake2b,
        ).digest()[: cls._HASH_LEN]
        info = purpose.encode("utf-8")
        block = hmac.new(
            prk,
            info + b"\x01",
            hashlib.blake2b,
        ).digest()
        return block[: cls._HASH_LEN]


class EnvKeyProvider:
    """Reads the master secret from an env var; derives per-purpose keys.

    Designed for the AKS pod where Key Vault references are projected
    into env via the Secret Provider Class CRD.  The env var name is
    configurable so EU-residency deployments can pin a region-scoped
    key without code changes.
    """

    def __init__(
        self,
        *,
        env_var: str = "BRAIN_MASTER_KEY",
        rotation_epoch: str = "2026-04",
    ) -> None:
        self._env_var = env_var
        self._rotation_epoch = rotation_epoch

    def key_for(self, purpose: str) -> KeyHandle:
        raw = os.environ.get(self._env_var, "")
        if not raw:
            raise RuntimeError(
                f"{self._env_var} is not set; refusing to derive keys",
            )
        try:
            master = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError(
                f"{self._env_var} must be base64-encoded bytes",
            ) from exc
        salt = self._rotation_epoch.encode("utf-8")
        sub_key = KeyDerivation.derive(
            master_key=master,
            purpose=purpose,
            salt=salt,
        )
        kid = f"{self._rotation_epoch}:{purpose}"
        return KeyHandle(kid=kid, key_bytes=sub_key)


class Encryptor(Protocol):
    """Symmetric authenticated encryption surface."""

    def encrypt(
        self,
        *,
        plaintext: bytes,
        key: KeyHandle,
        aad: bytes = b"",
    ) -> bytes:
        """Return a self-contained ciphertext (nonce ‖ ct ‖ tag)."""

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        key: KeyHandle,
        aad: bytes = b"",
    ) -> bytes:
        """Return plaintext or raise ``ValueError`` on auth failure."""


class AESGCMEncryptor:
    """AES-256-GCM encryptor.

    Lazy-imports ``cryptography`` on first use so the rest of the
    compliance layer can be imported on minimal runtimes (e.g. CI
    docs builds) where the dependency is absent.
    """

    _NONCE_LEN = 12

    def __init__(self) -> None:
        self._aesgcm_cls = self._load_aesgcm()

    @staticmethod
    def _load_aesgcm() -> type:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import (
                AESGCM,
            )
        except ImportError as exc:  # pragma: no cover - exercised in CI
            raise RuntimeError(
                "cryptography is required for AESGCMEncryptor; install via requirements.txt",
            ) from exc
        return AESGCM

    def encrypt(
        self,
        *,
        plaintext: bytes,
        key: KeyHandle,
        aad: bytes = b"",
    ) -> bytes:
        nonce = os.urandom(self._NONCE_LEN)
        cipher = self._aesgcm_cls(key.key_bytes)
        ct = cipher.encrypt(nonce, plaintext, aad or None)
        return nonce + ct

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        key: KeyHandle,
        aad: bytes = b"",
    ) -> bytes:
        if len(ciphertext) < self._NONCE_LEN + 16:
            raise ValueError("ciphertext shorter than nonce + tag")
        nonce, body = (
            ciphertext[: self._NONCE_LEN],
            ciphertext[self._NONCE_LEN :],
        )
        cipher = self._aesgcm_cls(key.key_bytes)
        return cipher.decrypt(nonce, body, aad or None)
