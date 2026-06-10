"""BenefitRecommender — Recommends perks and bonuses for loyal guests.

Based on guest loyalty score and tier, suggests benefits like:
- Free late checkout
- Room upgrades
- Welcome gifts
- Discounts on next stay
- Priority booking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.guest_intelligence.loyalty_scorer import GuestScore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Benefit:
    """A recommended benefit for a guest.

    Attributes:
        benefit_type: Type of benefit.
        description: Human-readable description.
        value: Estimated value in dollars.
        auto_applicable: Whether this can be applied automatically.
        requires_approval: Whether owner approval is needed.
    """

    benefit_type: str
    description: str
    value: float = 0.0
    auto_applicable: bool = False
    requires_approval: bool = True


@dataclass(slots=True)
class BenefitRecommendation:
    """Complete recommendation with guest context.

    Attributes:
        guest_id: Guest identifier.
        tier: Guest loyalty tier.
        score: Guest loyalty score.
        benefits: Recommended benefits.
        message_to_owner: Summary message for the owner.
    """

    guest_id: str
    tier: str
    score: int
    benefits: list[Benefit] = field(default_factory=list)
    message_to_owner: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "guest_id": self.guest_id,
            "tier": self.tier,
            "score": self.score,
            "benefits": [
                {
                    "benefit_type": b.benefit_type,
                    "description": b.description,
                    "value": b.value,
                    "auto_applicable": b.auto_applicable,
                    "requires_approval": b.requires_approval,
                }
                for b in self.benefits
            ],
            "message_to_owner": self.message_to_owner,
        }


# Benefit catalog per tier
TIER_BENEFITS: dict[str, list[dict[str, Any]]] = {
    "platinum": [
        {
            "benefit_type": "free_late_checkout",
            "description": "Free late checkout until 3 PM",
            "value": 50.0,
            "auto_applicable": True,
            "requires_approval": False,
        },
        {
            "benefit_type": "welcome_gift",
            "description": "Welcome package (local treats + wine)",
            "value": 30.0,
            "auto_applicable": False,
            "requires_approval": True,
        },
        {
            "benefit_type": "discount_next_stay",
            "description": "15% discount on next booking",
            "value": 0.0,
            "auto_applicable": False,
            "requires_approval": True,
        },
        {
            "benefit_type": "priority_support",
            "description": "Priority response for any requests",
            "value": 0.0,
            "auto_applicable": True,
            "requires_approval": False,
        },
    ],
    "gold": [
        {
            "benefit_type": "free_late_checkout",
            "description": "Free late checkout until 2 PM",
            "value": 30.0,
            "auto_applicable": True,
            "requires_approval": False,
        },
        {
            "benefit_type": "discount_next_stay",
            "description": "10% discount on next booking",
            "value": 0.0,
            "auto_applicable": False,
            "requires_approval": True,
        },
    ],
    "silver": [
        {
            "benefit_type": "late_checkout_discount",
            "description": "50% off late checkout fee",
            "value": 25.0,
            "auto_applicable": True,
            "requires_approval": False,
        },
    ],
    "bronze": [],
}


class BenefitRecommender:
    """Recommends benefits based on guest loyalty tier.

    Uses the tier from LoyaltyScorer to look up applicable benefits
    and generates a recommendation for the property owner.
    """

    def recommend(self, guest_score: GuestScore) -> BenefitRecommendation:
        """Generate benefit recommendations for a guest.

        Args:
            guest_score: The guest's loyalty score.

        Returns:
            BenefitRecommendation with applicable benefits.
        """
        tier = guest_score.tier
        benefit_configs = TIER_BENEFITS.get(tier, [])

        benefits = [
            Benefit(
                benefit_type=cfg["benefit_type"],
                description=cfg["description"],
                value=cfg["value"],
                auto_applicable=cfg["auto_applicable"],
                requires_approval=cfg["requires_approval"],
            )
            for cfg in benefit_configs
        ]

        message = self._build_owner_message(guest_score, benefits)

        recommendation = BenefitRecommendation(
            guest_id=guest_score.guest_id,
            tier=tier,
            score=guest_score.total_score,
            benefits=benefits,
            message_to_owner=message,
        )

        logger.info(
            "Recommended %d benefits for guest %s (tier=%s, score=%d)",
            len(benefits), guest_score.guest_id, tier, guest_score.total_score,
        )
        return recommendation

    @staticmethod
    def _build_owner_message(score: GuestScore, benefits: list[Benefit]) -> str:
        """Build a summary message for the property owner."""
        if not benefits:
            return (
                f"Guest loyalty: {score.tier.upper()} tier "
                f"(score: {score.total_score}/100). "
                "No special benefits recommended at this time."
            )

        benefit_lines = "\n".join(
            f"  - {b.description}" + (" [auto]" if b.auto_applicable else " [needs approval]")
            for b in benefits
        )

        return (
            f"Guest loyalty: {score.tier.upper()} tier "
            f"(score: {score.total_score}/100)\n\n"
            f"Recommended benefits:\n{benefit_lines}\n\n"
            "Auto benefits will be applied automatically. "
            "Others require your approval."
        )
