"""Criticality-tiered autonomy certificates (Moat #3).

The certificate layer operationalises Feng et al.'s five-tier
autonomy ladder (arXiv:2506.12469) into a runtime check that gates
every side-effecting tool-call:

- :class:`AutonomyTier` — five-tier StrEnum (OBSERVER → OPERATOR).
- :class:`TierPolicy` — ceiling per action kind.  Action kinds are
  opaque vertical-neutral strings; the kernel ships no vocabulary
  and no default mapping — ceilings are pack / tenant data (e.g.
  ``packs/hospitality/tier_defaults.yaml``).
- :class:`AutonomyCertificate` — tamper-evident authorisation
  token (frozen dataclass, HMAC-SHA256 signature, tz-aware expiry).
- :class:`CertificateIssuer` — mints fresh signed certs with a
  caller-supplied 32-byte key and configurable TTL (default 1 h).
- :class:`CertificateVerifier` / :class:`VerifyOutcome` /
  :class:`VerifyResult` — runtime middleware that replays the HMAC,
  checks expiry / scope / policy ceiling, and returns a structured
  result the audit log records.

Two distinct signed artifacts live here (CEN-14 PRD §2.4):

- the **authorization certificate** above — an *input* token,
  internal and short-lived, HMAC-SHA256 (symmetric is sufficient
  inside one trust domain);
- the **criticality receipt** (:mod:`core.brain.certificates.receipt`)
  — an *output* attestation minted at PROCEED for audiences across
  trust domains (operator, guest, regulator), signed **Ed25519** for
  non-repudiation.  HMAC must never sign a receipt.

Defensibility (Moat #3): per-action-class autonomy ladder with
cryptographic certificate binding and runtime middleware verifier
for regulated LLM-agents.  Feng et al. defines the labels;
:mod:`core.brain.certificates` is the first integrated runtime
implementation.
"""

from __future__ import annotations

from core.brain.certificates.cert import (
    AutonomyCertificate,
    canonical_payload,
)
from core.brain.certificates.issuer import (
    DEFAULT_TTL_SECONDS,
    MIN_KEY_BYTES,
    CertificateIssuer,
)
from core.brain.certificates.policy import (
    TierPolicy,
)
from core.brain.certificates.receipt import (
    RECEIPT_ALGORITHM_ED25519,
    ReceiptEnvelope,
    ReceiptSigner,
    ReceiptVerifyOutcome,
    ReceiptVerifyResult,
    VerificationKeyLookup,
    canonical_receipt_payload,
    seal_receipt,
    verify_receipt,
)
from core.brain.certificates.tier import (
    TIER_RANK,
    AutonomyTier,
    tier_rank,
)
from core.brain.certificates.verifier import (
    CertificateVerifier,
    VerifyOutcome,
    VerifyResult,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MIN_KEY_BYTES",
    "RECEIPT_ALGORITHM_ED25519",
    "TIER_RANK",
    "AutonomyCertificate",
    "AutonomyTier",
    "CertificateIssuer",
    "CertificateVerifier",
    "ReceiptEnvelope",
    "ReceiptSigner",
    "ReceiptVerifyOutcome",
    "ReceiptVerifyResult",
    "TierPolicy",
    "VerificationKeyLookup",
    "VerifyOutcome",
    "VerifyResult",
    "canonical_payload",
    "canonical_receipt_payload",
    "seal_receipt",
    "tier_rank",
    "verify_receipt",
]
