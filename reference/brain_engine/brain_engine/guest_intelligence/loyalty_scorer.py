"""LoyaltyScorer — Computes a loyalty score (0-100) for each guest.

Scoring factors:
- Number of stays (more = better)
- Damage history (less = better)
- Review ratings
- Complaint frequency
- Late checkout behavior
- Repeat bookings
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from brain_engine.guest_intelligence.profile_builder import GuestProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuestScore:
    """Computed loyalty score with breakdown.

    Attributes:
        guest_id: Guest identifier.
        total_score: Overall score (0-100).
        stay_score: Points from number of stays (0-30).
        behavior_score: Points from behavior (0-30).
        review_score: Points from reviews (0-20).
        longevity_score: Points from being a long-term guest (0-20).
        factors: Human-readable breakdown of scoring factors.
        tier: Loyalty tier (bronze, silver, gold, platinum).
    """

    guest_id: str
    total_score: int
    stay_score: int
    behavior_score: int
    review_score: int
    longevity_score: int
    factors: list[str]
    tier: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "guest_id": self.guest_id,
            "total_score": self.total_score,
            "stay_score": self.stay_score,
            "behavior_score": self.behavior_score,
            "review_score": self.review_score,
            "longevity_score": self.longevity_score,
            "factors": self.factors,
            "tier": self.tier,
        }


# Tier thresholds
TIER_THRESHOLDS: dict[str, int] = {
    "platinum": 80,
    "gold": 60,
    "silver": 40,
    "bronze": 0,
}


class LoyaltyScorer:
    """Computes guest loyalty scores from profile data.

    Scoring is deterministic and based on observable data.
    No machine learning — pure rule-based scoring that the owner can understand.
    """

    def score(self, profile: GuestProfile) -> GuestScore:
        """Compute loyalty score for a guest profile.

        Args:
            profile: Complete guest profile.

        Returns:
            GuestScore with total score and breakdown.
        """
        factors: list[str] = []

        # Stay score (0-30): more stays = more loyalty
        stay_score = self._score_stays(profile, factors)

        # Behavior score (0-30): no damage, no complaints
        behavior_score = self._score_behavior(profile, factors)

        # Review score (0-20): positive reviews
        review_score = self._score_reviews(profile, factors)

        # Longevity score (0-20): long-term relationship
        longevity_score = self._score_longevity(profile, factors)

        total = min(stay_score + behavior_score + review_score + longevity_score, 100)
        tier = self._determine_tier(total)

        logger.info(
            "Guest %s score: %d (%s) — stays=%d, behavior=%d, reviews=%d, longevity=%d",
            profile.guest_id, total, tier,
            stay_score, behavior_score, review_score, longevity_score,
        )

        return GuestScore(
            guest_id=profile.guest_id,
            total_score=total,
            stay_score=stay_score,
            behavior_score=behavior_score,
            review_score=review_score,
            longevity_score=longevity_score,
            factors=factors,
            tier=tier,
        )

    @staticmethod
    def _score_stays(profile: GuestProfile, factors: list[str]) -> int:
        """Score based on number of stays (0-30)."""
        stays = profile.total_stays
        if stays == 0:
            factors.append("New guest (first stay)")
            return 5  # Base score for new guests
        if stays == 1:
            factors.append("1 previous stay")
            return 10
        if stays <= 3:
            factors.append(f"{stays} stays — returning guest")
            return 15
        if stays <= 5:
            factors.append(f"{stays} stays — regular guest")
            return 22
        factors.append(f"{stays} stays — loyal guest")
        return 30

    @staticmethod
    def _score_behavior(profile: GuestProfile, factors: list[str]) -> int:
        """Score based on guest behavior (0-30)."""
        score = 30  # Start with full score, deduct for issues

        # Deduct for damage
        if profile.damage_incidents > 0:
            deduction = min(profile.damage_incidents * 8, 20)
            score -= deduction
            factors.append(
                f"-{deduction} pts: {profile.damage_incidents} damage incident(s)"
            )

        # Deduct for complaints
        if profile.complaints > 0:
            deduction = min(profile.complaints * 5, 10)
            score -= deduction
            factors.append(f"-{deduction} pts: {profile.complaints} complaint(s)")

        # Bonus for clean record with multiple stays
        if (
            profile.total_stays >= 3
            and profile.damage_incidents == 0
            and profile.complaints == 0
        ):
            factors.append("+5 pts: clean record across multiple stays")
            score = min(score + 5, 30)

        return max(score, 0)

    @staticmethod
    def _score_reviews(profile: GuestProfile, factors: list[str]) -> int:
        """Score based on review history (0-20)."""
        score = 10  # Base score

        if profile.positive_reviews > 0:
            bonus = min(profile.positive_reviews * 3, 10)
            score += bonus
            factors.append(
                f"+{bonus} pts: {profile.positive_reviews} positive review(s)"
            )

        if profile.negative_reviews > 0:
            deduction = min(profile.negative_reviews * 5, 15)
            score -= deduction
            factors.append(
                f"-{deduction} pts: {profile.negative_reviews} negative review(s)"
            )

        if profile.average_review_rating >= 4.5:
            factors.append("+3 pts: excellent average rating")
            score = min(score + 3, 20)

        return max(score, 0)

    @staticmethod
    def _score_longevity(profile: GuestProfile, factors: list[str]) -> int:
        """Score based on long-term guest relationship (0-20)."""
        if not profile.first_stay_date:
            return 5  # Base for new guests

        # Multi-property loyalty
        property_count = len(profile.properties_stayed)
        if property_count >= 3:
            factors.append(f"+5 pts: stayed at {property_count} different properties")
            return 20
        if property_count >= 2:
            factors.append(f"+3 pts: stayed at {property_count} properties")
            return 15

        if profile.total_stays >= 2:
            factors.append("Repeat booker")
            return 12

        return 5

    @staticmethod
    def _determine_tier(total_score: int) -> str:
        """Determine loyalty tier from total score."""
        for tier, threshold in TIER_THRESHOLDS.items():
            if total_score >= threshold:
                return tier
        return "bronze"
