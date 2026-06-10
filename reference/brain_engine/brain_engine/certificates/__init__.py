"""Criticality-tiered autonomy certificates (Moat #3).

The certificate layer operationalises Feng et al.'s five-tier
autonomy ladder (arXiv:2506.12469) into a runtime check that gates
every side-effecting tool-call:

- :class:`AutonomyTier` — five-tier StrEnum (OBSERVER → OPERATOR).
- :class:`TierPolicy` / :data:`DEFAULT_TIER_POLICY` — ceiling per
  :class:`brain_engine.cards.action_kinds.CardActionKind`.
- :class:`AutonomyCertificate` — tamper-evident authorisation
  token (frozen dataclass, HMAC-SHA256 signature, tz-aware expiry).
- :class:`CertificateIssuer` — mints fresh signed certs with a
  caller-supplied 32-byte key and configurable TTL (default 1 h).
- :class:`CertificateVerifier` / :class:`VerifyOutcome` /
  :class:`VerifyResult` — runtime middleware that replays the HMAC,
  checks expiry / scope / policy ceiling, and returns a structured
  result the audit log records.

Defensibility (Moat #3): per-action-class autonomy ladder with
cryptographic certificate binding and runtime middleware verifier
for regulated LLM-agents.  Feng et al. defines the labels;
:mod:`brain_engine.certificates` is the first integrated runtime
implementation.
"""

from __future__ import annotations

from brain_engine.certificates.cert import (
    AutonomyCertificate,
    canonical_payload,
)
from brain_engine.certificates.issuer import (
    DEFAULT_TTL_SECONDS,
    MIN_KEY_BYTES,
    CertificateIssuer,
)
from brain_engine.certificates.policy import (
    DEFAULT_TIER_POLICY,
    TierPolicy,
)
from brain_engine.certificates.tier import (
    TIER_RANK,
    AutonomyTier,
    tier_rank,
)
from brain_engine.certificates.verifier import (
    CertificateVerifier,
    VerifyOutcome,
    VerifyResult,
)


__all__ = [
    "DEFAULT_TIER_POLICY",
    "DEFAULT_TTL_SECONDS",
    "MIN_KEY_BYTES",
    "TIER_RANK",
    "AutonomyCertificate",
    "AutonomyTier",
    "CertificateIssuer",
    "CertificateVerifier",
    "TierPolicy",
    "VerifyOutcome",
    "VerifyResult",
    "canonical_payload",
    "tier_rank",
]
