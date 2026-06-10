"""Escalation tier vocabulary + policy.

The CEO V2 directive (2026-04-20) requires a 24/7 chain so a guest
incident at 03:00 always has a named responder.  Five tiers cover
the full ladder from the engine itself up to a 24/7 partner; each
tier names the team role to page and the response window the SLA
promises.

Tiers are ordered: ``TIER_0_AI`` < ``TIER_1_PRIMARY_PM`` < … <
``TIER_4_PARTNER_24_7``.  ``rank`` exposes that ordering for the
dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from brain_engine.team.models import TeamRole


__all__ = [
    "DEFAULT_ESCALATION_POLICY",
    "EscalationLevel",
    "EscalationPolicy",
    "EscalationTier",
    "tier_rank",
]


class EscalationTier(StrEnum):
    """Five canonical 24/7 escalation tiers."""

    TIER_0_AI = "tier_0_ai"
    TIER_1_PRIMARY_PM = "tier_1_primary_pm"
    TIER_2_SECONDARY_PM = "tier_2_secondary_pm"
    TIER_3_OWNER = "tier_3_owner"
    TIER_4_PARTNER_24_7 = "tier_4_partner_24_7"


_TIER_ORDER: Final[tuple[EscalationTier, ...]] = (
    EscalationTier.TIER_0_AI,
    EscalationTier.TIER_1_PRIMARY_PM,
    EscalationTier.TIER_2_SECONDARY_PM,
    EscalationTier.TIER_3_OWNER,
    EscalationTier.TIER_4_PARTNER_24_7,
)


def tier_rank(tier: EscalationTier) -> int:
    """Return the numeric rank for ``tier`` (0 = AI, 4 = 24/7 partner)."""
    return _TIER_ORDER.index(tier)


@dataclass(frozen=True, slots=True)
class EscalationLevel:
    """Definition of a single tier in an :class:`EscalationPolicy`.

    Attributes:
        tier: Which canonical tier this level represents.
        target_role: Team role the dispatcher should resolve via
            :class:`brain_engine.team.TeamRoster`.  ``TIER_0_AI``
            uses ``None`` because the engine itself owns the
            response.
        response_window_minutes: SLA promise for first response.
        fallback_tier: Next tier the dispatcher falls back to when
            the resolved roster is empty or off-duty.  ``None``
            means "stop here" — there is no further escalation.
    """

    tier: EscalationTier
    target_role: TeamRole | None
    response_window_minutes: int
    fallback_tier: EscalationTier | None = None

    @property
    def rank(self) -> int:
        """Numeric tier rank for ordering."""
        return tier_rank(self.tier)


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    """Ordered set of :class:`EscalationLevel` definitions.

    The policy is a simple lookup table — given a tier, return the
    matching level (or ``None`` when the tier is not configured).
    Each level may reference a fallback tier; the dispatcher walks
    that chain when the active level cannot field a responder.
    """

    levels: tuple[EscalationLevel, ...] = ()

    @property
    def by_tier(self) -> dict[EscalationTier, EscalationLevel]:
        """Lookup map keyed by tier (computed lazily; cheap)."""
        return {level.tier: level for level in self.levels}

    def level_for(
        self,
        tier: EscalationTier,
    ) -> EscalationLevel | None:
        """Return the level definition for ``tier`` or ``None``."""
        return self.by_tier.get(tier)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


DEFAULT_ESCALATION_POLICY: Final[EscalationPolicy] = EscalationPolicy(
    levels=(
        EscalationLevel(
            tier=EscalationTier.TIER_0_AI,
            target_role=None,
            response_window_minutes=1,
            fallback_tier=EscalationTier.TIER_1_PRIMARY_PM,
        ),
        EscalationLevel(
            tier=EscalationTier.TIER_1_PRIMARY_PM,
            target_role=TeamRole.PM,
            response_window_minutes=10,
            fallback_tier=EscalationTier.TIER_2_SECONDARY_PM,
        ),
        EscalationLevel(
            tier=EscalationTier.TIER_2_SECONDARY_PM,
            target_role=TeamRole.PM,
            response_window_minutes=20,
            fallback_tier=EscalationTier.TIER_3_OWNER,
        ),
        EscalationLevel(
            tier=EscalationTier.TIER_3_OWNER,
            target_role=TeamRole.OWNER,
            response_window_minutes=30,
            fallback_tier=EscalationTier.TIER_4_PARTNER_24_7,
        ),
        EscalationLevel(
            tier=EscalationTier.TIER_4_PARTNER_24_7,
            target_role=TeamRole.ACCOUNT_MANAGER,
            response_window_minutes=60,
            fallback_tier=None,
        ),
    ),
)
