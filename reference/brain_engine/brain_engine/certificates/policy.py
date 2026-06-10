"""Default :class:`TierPolicy` mapping action kinds to ceiling tiers.

A *policy* answers: "for this action class, what is the highest
:class:`AutonomyTier` (most autonomy) the runtime is willing to
allow?"  The verifier consults the policy when a certificate is
presented; if the cert's ``granted_tier`` exceeds the policy's
``max_authorised_tier``, the verification fails.

Owner-policy DSL (Moat #2) will write into a richer policy that
narrows these defaults per owner; here we ship the conservative
baseline aligned with EU AI Act Art. 14 (human oversight on
significant-effect actions) and the Brain Engine Reversibility
tiers in :mod:`brain_engine.cards.action_kinds`.
"""

from __future__ import annotations

from typing import Final, Mapping

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.certificates.tier import AutonomyTier


__all__ = [
    "DEFAULT_TIER_POLICY",
    "TierPolicy",
]


_DEFAULTS: Final[Mapping[CardActionKind, AutonomyTier]] = {
    # Communication / bookkeeping — low risk, high autonomy ok
    CardActionKind.SEND_MESSAGE: AutonomyTier.COLLABORATOR,
    CardActionKind.REQUEST_DOCUMENT: AutonomyTier.COLLABORATOR,
    CardActionKind.MARK_RESOLVED: AutonomyTier.OPERATOR,
    CardActionKind.LOG_DECISION: AutonomyTier.OPERATOR,
    CardActionKind.HANDOFF_TO_TEAMMATE: AutonomyTier.OPERATOR,
    # Booking lifecycle — material consequences
    CardActionKind.HOLD_FOR_REVIEW: AutonomyTier.COLLABORATOR,
    CardActionKind.BLOCK_DATE: AutonomyTier.COLLABORATOR,
    CardActionKind.CONFIRM_BOOKING: AutonomyTier.APPROVER,
    CardActionKind.CANCEL_BOOKING: AutonomyTier.APPROVER,
    # Pricing / negotiation
    CardActionKind.APPLY_DISCOUNT: AutonomyTier.CONSULTANT,
    CardActionKind.COUNTER_OFFER: AutonomyTier.CONSULTANT,
    # Operations / dispatch
    CardActionKind.DISPATCH_VENDOR: AutonomyTier.CONSULTANT,
    # Financial / security-sensitive — must be approved
    CardActionKind.CHARGE_FEE: AutonomyTier.APPROVER,
    CardActionKind.ISSUE_REFUND: AutonomyTier.APPROVER,
    CardActionKind.RELEASE_CODE: AutonomyTier.APPROVER,
    CardActionKind.ESCALATE: AutonomyTier.APPROVER,
}


class TierPolicy:
    """Look up the ceiling tier authorised for an action kind.

    The default policy seeds the conservative mapping above; tenants
    may override per action via :meth:`override`.  ``ceiling_for``
    raises :class:`KeyError` for action kinds without an explicit
    mapping so misconfigurations fail loudly rather than silently
    permitting full autonomy.
    """

    def __init__(
        self,
        defaults: Mapping[CardActionKind, AutonomyTier] = _DEFAULTS,
    ) -> None:
        self._mapping: dict[CardActionKind, AutonomyTier] = dict(
            defaults
        )

    def ceiling_for(self, action_kind: CardActionKind) -> AutonomyTier:
        """Return the highest authorised tier for ``action_kind``."""
        try:
            return self._mapping[action_kind]
        except KeyError as exc:
            raise KeyError(
                f"no tier ceiling configured for {action_kind!r}"
            ) from exc

    def override(
        self,
        action_kind: CardActionKind,
        tier: AutonomyTier,
    ) -> None:
        """Set or replace the ceiling for one action kind."""
        self._mapping[action_kind] = tier

    def known_actions(self) -> tuple[CardActionKind, ...]:
        """Return the action kinds with a configured ceiling."""
        return tuple(self._mapping.keys())


DEFAULT_TIER_POLICY: Final[TierPolicy] = TierPolicy()
