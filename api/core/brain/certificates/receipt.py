"""Ed25519-signed criticality receipt envelope (Moat #3, PRD §2).

The certificate layer carries **two distinct artifacts** that earlier
designs conflated (CEN-14 PRD §2.4):

- **Authorization certificate** (:class:`AutonomyCertificate`) — an
  *input* token consumed by the certificate gate before dispatch.
  Internal, server-to-server, short-lived: HMAC-SHA256 stays the
  signing scheme (:mod:`core.brain.certificates.issuer` /
  :mod:`core.brain.certificates.verifier`).  Symmetric integrity is
  sufficient because issuer and verifier share one trust domain.
- **Criticality receipt** (this module) — an *output* attestation
  minted at PROCEED: the signed record of what was decided, why, and
  at what confidence.  Its audience spans trust domains (operator,
  guest, regulator, Cendra), so it requires **Ed25519** public-key
  signatures for non-repudiation.  HMAC must never sign a receipt:
  whoever could verify could also forge.

The receipt signs the canonical bytes of the Art. 12 decision record
(:func:`core.brain.compliance.art12_decision.canonical_record`), so
the signature binds exactly the payload a regulator replays.  Private
keys never enter the kernel: signing is delegated through the
:class:`ReceiptSigner` protocol, structurally satisfied by the tenant
key-custody contract (``BrainCustodyService.sign_receipt``, CEN-78),
which resolves the active ``brain_signing_keys`` row and returns only
public metadata — ``key_id``, ``algorithm``, ``signature_hex``.

Rotation: every receipt is stamped with the immutable ``key_id`` that
signed it.  Verification resolves the public key through a
caller-supplied lookup (the published verification-key registry keeps
rotated-out keys readable), so historical receipts keep verifying
after rotation.

Fallback posture (PRD §2.5): when no signing key is provisioned for a
tenant the envelope is minted **unsigned** — chain-linked integrity
digest only, ``signed=False``, no fake signature.  Surfaces render
unsigned records honestly.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.brain.compliance.art12_decision import (
    Art12Decision,
    canonical_record,
    chained_digest,
)

__all__ = [
    "ED25519_PUBLIC_KEY_BYTES",
    "ED25519_SIGNATURE_BYTES",
    "RECEIPT_ALGORITHM_ED25519",
    "ReceiptEnvelope",
    "ReceiptSigner",
    "ReceiptVerifyOutcome",
    "ReceiptVerifyResult",
    "VerificationKeyLookup",
    "canonical_receipt_payload",
    "seal_receipt",
    "verify_receipt",
]


RECEIPT_ALGORITHM_ED25519: Final[str] = "Ed25519"
ED25519_PUBLIC_KEY_BYTES: Final[int] = 32
ED25519_SIGNATURE_BYTES: Final[int] = 64


def canonical_receipt_payload(decision: Art12Decision) -> bytes:
    """Return the exact bytes the tenant key signs.

    Identical to :func:`canonical_record` — sorted-key, separator-
    stable JSON — so any verifier in any language reproduces the
    payload from the receipt's public fields alone.  Kept as a named
    seam so the signed-bytes contract survives even if the audit
    digest scheme evolves separately.
    """
    return canonical_record(decision)


class ReceiptSigner(Protocol):
    """Structural slice of the tenant key-custody contract (CEN-78).

    ``BrainCustodyService.sign_receipt`` satisfies this protocol: it
    resolves the tenant's active receipt-signing key, signs the
    canonical payload, and returns only public signature metadata
    (``key_id``, ``algorithm``, ``signature_hex``).  The kernel never
    sees private bytes.
    """

    def sign_receipt(self, tenant_id: str, payload: bytes | bytearray) -> Mapping[str, str]:
        """Sign ``payload`` with the tenant's active published key."""
        ...


VerificationKeyLookup = Callable[[str], str | None]
"""Resolve an immutable ``key_id`` to its ``public_key_base64url``.

Backed by the published verification-key registry
(``brain_signing_keys`` rows / ``GET /v1/brain/verification-keys/<key_id>``).
Rotated-out keys stay resolvable so historical receipts keep
verifying; return ``None`` for unknown ids.
"""


@dataclass(frozen=True, slots=True)
class ReceiptEnvelope:
    """Dispute-grade envelope over one Art. 12 decision record.

    Attributes:
        record: The Art. 12 decision record the receipt attests.
        record_digest: Chained BLAKE2B-256 hex digest of the record —
            the within-log integrity layer, present whether or not
            the envelope is signed.
        signed: ``True`` when an Ed25519 signature is attached.
            ``False`` is the honest no-key fallback, never a silent
            failure.
        key_id: Immutable id of the signing key (rotation lookup
            input). ``None`` when unsigned.
        algorithm: Signature algorithm label (``"Ed25519"``).
            ``None`` when unsigned.
        signature_hex: Hex-encoded Ed25519 signature over
            :func:`canonical_receipt_payload`. ``None`` when unsigned.
    """

    record: Art12Decision
    record_digest: str
    signed: bool
    key_id: str | None = None
    algorithm: str | None = None
    signature_hex: str | None = None

    def __post_init__(self) -> None:
        """Reject envelopes that claim one signing posture and carry another."""
        if len(self.record_digest) != 64:
            raise ValueError("record_digest must be 64 hex chars (BLAKE2B-256)")
        signature_fields = (self.key_id, self.algorithm, self.signature_hex)
        if self.signed:
            if not all(signature_fields):
                raise ValueError("signed envelope requires key_id, algorithm, and signature_hex")
        elif any(field is not None for field in signature_fields):
            raise ValueError("unsigned envelope must not carry key_id, algorithm, or signature_hex")


def seal_receipt(
    decision: Art12Decision,
    *,
    tenant_id: str,
    signer: ReceiptSigner | None,
) -> ReceiptEnvelope:
    """Mint the receipt envelope for ``decision``.

    With a ``signer`` the envelope carries the tenant's Ed25519
    signature over the canonical payload.  With ``signer=None`` (no
    key provisioned for the tenant) the envelope is minted unsigned —
    integrity digest only, flagged ``signed=False``.
    """
    if not tenant_id.strip():
        raise ValueError("tenant_id required")

    record_digest = chained_digest(decision)
    if signer is None:
        return ReceiptEnvelope(record=decision, record_digest=record_digest, signed=False)

    signature = signer.sign_receipt(tenant_id, canonical_receipt_payload(decision))
    return ReceiptEnvelope(
        record=decision,
        record_digest=record_digest,
        signed=True,
        key_id=signature["key_id"],
        algorithm=signature["algorithm"],
        signature_hex=signature["signature_hex"],
    )


class ReceiptVerifyOutcome(StrEnum):
    """Distinct verification outcomes the audit trail records."""

    OK = "ok"
    UNSIGNED = "unsigned"
    UNKNOWN_KEY = "unknown_key"
    UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
    MALFORMED_KEY = "malformed_key"
    MALFORMED_SIGNATURE = "malformed_signature"
    BAD_SIGNATURE = "bad_signature"


@dataclass(frozen=True, slots=True)
class ReceiptVerifyResult:
    """Structured outcome of a receipt verification.

    Attributes:
        outcome: The :class:`ReceiptVerifyOutcome` that ended the
            verification.
        rationale: One-line plain-English explanation for the audit
            trail.
    """

    outcome: ReceiptVerifyOutcome
    rationale: str

    @property
    def ok(self) -> bool:
        """Whether the signature verified."""
        return self.outcome is ReceiptVerifyOutcome.OK


def verify_receipt(
    envelope: ReceiptEnvelope,
    *,
    key_lookup: VerificationKeyLookup,
) -> ReceiptVerifyResult:
    """Verify ``envelope`` against the published verification keys.

    Resolution goes through ``key_lookup`` by the envelope's stamped
    ``key_id``, so receipts signed under rotated-out keys verify as
    long as the registry keeps the old public key published.
    """
    if not envelope.signed:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.UNSIGNED,
            rationale="envelope is unsigned (no-key fallback); integrity digest only",
        )

    # __post_init__ guarantees these are present on a signed envelope.
    assert envelope.key_id is not None
    assert envelope.algorithm is not None
    assert envelope.signature_hex is not None

    if envelope.algorithm.strip().lower() != RECEIPT_ALGORITHM_ED25519.lower():
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.UNSUPPORTED_ALGORITHM,
            rationale=f"unsupported receipt algorithm {envelope.algorithm!r}",
        )

    public_key_base64url = key_lookup(envelope.key_id)
    if public_key_base64url is None:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.UNKNOWN_KEY,
            rationale=f"key_id {envelope.key_id!r} is not published in the verification-key registry",
        )

    try:
        public_key = _decode_public_key(public_key_base64url)
    except ValueError as exc:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.MALFORMED_KEY,
            rationale=f"published key for {envelope.key_id!r} is malformed: {exc}",
        )

    try:
        signature = bytes.fromhex(envelope.signature_hex)
    except ValueError:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.MALFORMED_SIGNATURE,
            rationale="signature_hex is not valid hex",
        )
    if len(signature) != ED25519_SIGNATURE_BYTES:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.MALFORMED_SIGNATURE,
            rationale=f"Ed25519 signatures are {ED25519_SIGNATURE_BYTES} bytes; got {len(signature)}",
        )

    try:
        public_key.verify(signature, canonical_receipt_payload(envelope.record))
    except InvalidSignature:
        return ReceiptVerifyResult(
            outcome=ReceiptVerifyOutcome.BAD_SIGNATURE,
            rationale="signature does not verify over the canonical record payload",
        )

    return ReceiptVerifyResult(
        outcome=ReceiptVerifyOutcome.OK,
        rationale=f"Ed25519 signature verified under key_id {envelope.key_id}",
    )


def _decode_public_key(public_key_base64url: str) -> Ed25519PublicKey:
    """Decode a registry ``public_key_base64url`` value (unpadded ok)."""
    normalized = public_key_base64url.strip()
    if not normalized:
        raise ValueError("public key is empty")
    padding = "=" * (-len(normalized) % 4)
    try:
        raw = base64.urlsafe_b64decode(normalized + padding)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("public key is not base64url") from exc
    if len(raw) != ED25519_PUBLIC_KEY_BYTES:
        raise ValueError(f"Ed25519 public keys are {ED25519_PUBLIC_KEY_BYTES} bytes; got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)
