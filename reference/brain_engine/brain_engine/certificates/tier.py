"""Five-tier autonomy ladder (Feng et al. arXiv:2506.12469).

Brain Engine's existing :class:`brain_engine.autonomy.AutonomyState`
is a three-state machine optimised for the V2 UI Trust Meter
(``OBSERVE`` / ``SEMI_AUTO`` / ``AUTOPILOT``).  The five-tier ladder
here is *complementary*: it is a finer-grained criticality vocabulary
used for per-action-component certificates (Moat #3) so a single
high-level decision can mix tiers — e.g. "send the message
(COLLABORATOR) and issue the refund (APPROVER) and update the
profile (OPERATOR)" all under one card.

Higher rank = more autonomy = less oversight.  Tiers compare via
:func:`tier_rank`; clients should never compare the raw enum values
because StrEnum ordering would alphabetise them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Mapping


__all__ = [
    "TIER_RANK",
    "AutonomyTier",
    "tier_rank",
]


class AutonomyTier(StrEnum):
    """Five-tier autonomy ladder per Feng et al.

    - ``OBSERVER`` (rank 1): agent watches, does not act.
    - ``APPROVER`` (rank 2): agent acts only after human approval.
    - ``CONSULTANT`` (rank 3): agent suggests; human decides.
    - ``COLLABORATOR`` (rank 4): agent acts, human reviews promptly.
    - ``OPERATOR`` (rank 5): agent acts solo; human reads digest only.
    """

    OBSERVER = "observer"
    APPROVER = "approver"
    CONSULTANT = "consultant"
    COLLABORATOR = "collaborator"
    OPERATOR = "operator"


TIER_RANK: Final[Mapping[AutonomyTier, int]] = {
    AutonomyTier.OBSERVER: 1,
    AutonomyTier.APPROVER: 2,
    AutonomyTier.CONSULTANT: 3,
    AutonomyTier.COLLABORATOR: 4,
    AutonomyTier.OPERATOR: 5,
}


def tier_rank(tier: AutonomyTier) -> int:
    """Return the monotonic rank of ``tier``.

    Higher rank = more autonomy.  ``OBSERVER`` is the most
    restrictive (rank 1); ``OPERATOR`` is the most permissive
    (rank 5).
    """
    return TIER_RANK[tier]
