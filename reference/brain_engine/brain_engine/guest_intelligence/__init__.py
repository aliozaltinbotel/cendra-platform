"""Guest Intelligence — Guest profiling, loyalty scoring, and risk flagging.

Tracks guest behavior across stays, builds loyalty scores, recommends
bonuses for good guests, and flags problematic guests for the owner.
"""

from brain_engine.guest_intelligence.loyalty_scorer import LoyaltyScorer, GuestScore
from brain_engine.guest_intelligence.profile_builder import GuestProfileBuilder, GuestProfile
from brain_engine.guest_intelligence.benefit_recommender import BenefitRecommender
from brain_engine.guest_intelligence.risk_flag import RiskFlagSystem, RiskLevel

__all__ = [
    "LoyaltyScorer",
    "GuestScore",
    "GuestProfileBuilder",
    "GuestProfile",
    "BenefitRecommender",
    "RiskFlagSystem",
    "RiskLevel",
]
