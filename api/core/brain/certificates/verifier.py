"""Runtime middleware verifier for :class:`AutonomyCertificate`.

Verification is the gate the action pipeline checks *before* any
side-effecting tool-call.  A successful verification means:

- The cert's HMAC signature reproduces under the configured key
  (constant-time comparison).
- The cert is not expired.
- The cert is scoped to the action class / property / owner the
  caller is about to act on.
- The cert's ``granted_tier`` does not exceed the
  :class:`TierPolicy`'s ceiling for the action class.

Each failure mode produces a distinct :class:`VerifyOutcome` so the
audit log can record *which* check rejected the call.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from core.brain.certificates.cert import (
    AutonomyCertificate,
    canonical_payload,
)
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import (
    AutonomyTier,
    tier_rank,
)

__all__ = [
    "CertificateVerifier",
    "VerifyOutcome",
    "VerifyResult",
]


logger = logging.getLogger(__name__)


class VerifyOutcome(StrEnum):
    """Distinct verification outcomes the audit log records."""

    OK = "ok"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    WRONG_ACTION = "wrong_action"
    WRONG_PROPERTY = "wrong_property"
    WRONG_OWNER = "wrong_owner"
    EXCEEDS_POLICY = "exceeds_policy"


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Structured outcome of a verification.

    Attributes:
        outcome: The :class:`VerifyOutcome` that ended the
            verification.
        rationale: One-line plain-English explanation; consumed by
            the audit log so the regulator can replay the decision.
    """

    outcome: VerifyOutcome
    rationale: str

    @property
    def ok(self) -> bool:
        """Whether verification passed all checks."""
        return self.outcome is VerifyOutcome.OK


class CertificateVerifier:
    """Verify a presented :class:`AutonomyCertificate`.

    The verifier is stateless apart from its configured signing key
    and tier policy — both injected at construction so callers can
    plug in tenant-specific policies (Moat #2 DSL output) without
    forking the verifier itself.  There is no default policy: the
    kernel ships no action vocabulary, so the caller always supplies
    the pack- or tenant-derived :class:`TierPolicy`.
    """

    def __init__(
        self,
        *,
        signing_key: bytes,
        policy: TierPolicy,
    ) -> None:
        if not signing_key:
            raise ValueError("signing_key must be non-empty")
        self._key = signing_key
        self._policy = policy

    def verify(
        self,
        *,
        cert: AutonomyCertificate,
        action_kind: str,
        property_id: str,
        owner_id: str,
        at: datetime | None = None,
    ) -> VerifyResult:
        """Run every check in order; return the first failure or OK.

        Order:
            1. Signature (constant-time HMAC comparison).
            2. Expiry.
            3. Action match.
            4. Property match.
            5. Owner match.
            6. Policy ceiling.
        """
        outcome = self._signature_check(cert)
        if outcome is not None:
            return outcome
        outcome = self._expiry_check(cert, at=at)
        if outcome is not None:
            return outcome
        outcome = self._scope_checks(
            cert=cert,
            action_kind=action_kind,
            property_id=property_id,
            owner_id=owner_id,
        )
        if outcome is not None:
            return outcome
        outcome = self._policy_check(cert)
        if outcome is not None:
            return outcome
        return VerifyResult(
            outcome=VerifyOutcome.OK,
            rationale=(f"cert {cert.cert_id} valid; tier={cert.granted_tier.value}"),
        )

    # ── Individual checks ─────────────────────────────────────── #

    def _signature_check(
        self,
        cert: AutonomyCertificate,
    ) -> VerifyResult | None:
        payload = canonical_payload(
            cert_id=cert.cert_id,
            action_kind=cert.action_kind,
            property_id=cert.property_id,
            owner_id=cert.owner_id,
            granted_tier=cert.granted_tier,
            issued_at=cert.issued_at,
            expires_at=cert.expires_at,
        )
        expected = hmac.new(
            self._key,
            payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, cert.signature_hex):
            logger.warning(
                "cert.bad_signature cert_id=%s",
                cert.cert_id,
            )
            return VerifyResult(
                outcome=VerifyOutcome.BAD_SIGNATURE,
                rationale="HMAC mismatch",
            )
        return None

    def _expiry_check(
        self,
        cert: AutonomyCertificate,
        *,
        at: datetime | None,
    ) -> VerifyResult | None:
        if cert.is_expired(at=at):
            return VerifyResult(
                outcome=VerifyOutcome.EXPIRED,
                rationale=(f"expired at {cert.expires_at.isoformat()}"),
            )
        return None

    @staticmethod
    def _scope_checks(
        *,
        cert: AutonomyCertificate,
        action_kind: str,
        property_id: str,
        owner_id: str,
    ) -> VerifyResult | None:
        if cert.action_kind != action_kind:
            return VerifyResult(
                outcome=VerifyOutcome.WRONG_ACTION,
                rationale=(f"cert action {cert.action_kind} != requested {action_kind}"),
            )
        if cert.property_id != property_id:
            return VerifyResult(
                outcome=VerifyOutcome.WRONG_PROPERTY,
                rationale=(f"cert property {cert.property_id!r} != requested {property_id!r}"),
            )
        if cert.owner_id != owner_id:
            return VerifyResult(
                outcome=VerifyOutcome.WRONG_OWNER,
                rationale=(f"cert owner {cert.owner_id!r} != requested {owner_id!r}"),
            )
        return None

    def _policy_check(
        self,
        cert: AutonomyCertificate,
    ) -> VerifyResult | None:
        ceiling: AutonomyTier = self._policy.ceiling_for(cert.action_kind)
        if tier_rank(cert.granted_tier) > tier_rank(ceiling):
            return VerifyResult(
                outcome=VerifyOutcome.EXCEEDS_POLICY,
                rationale=(
                    f"granted={cert.granted_tier.value} exceeds policy ceiling={ceiling.value} for {cert.action_kind}"
                ),
            )
        return None
