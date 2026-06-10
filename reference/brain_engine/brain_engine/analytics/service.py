"""Analytics service — aggregated metrics for the Cendra dashboard.

Provides three analytics capabilities:

- **Sentiment aggregation**: Average guest sentiment over a time window,
  derived from DecisionCase outcomes and business flag classifications.
- **Escalation breakdown**: Count of escalated conversations grouped by
  scenario category (Availability, Booking Modification, Complaint, etc.).
- **AI accuracy per property**: Percentage of AI responses that were
  accepted without PM override, computed from DecisionCase outcomes.

All methods are async because they query the DecisionCaseStore.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from brain_engine.patterns.models import DecisionCase, Scenario
from brain_engine.patterns.store import DecisionCaseStore

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_WINDOW_DAYS: Final[int] = 90

# Sentiment scoring: map scenario outcomes to sentiment impact.
# Positive outcome = 7-10, neutral = 4-6, negative = 1-3.
_POSITIVE_SENTIMENT: Final[float] = 8.0
_NEUTRAL_SENTIMENT: Final[float] = 5.0
_NEGATIVE_SENTIMENT: Final[float] = 2.5

# Scenario → dashboard category mapping (matches Cendra dashboard).
_SCENARIO_CATEGORY: Final[dict[Scenario, str]] = {
    Scenario.GUEST_COUNT_MISMATCH: "Booking Modification",
    Scenario.DISCOUNT_REQUEST: "Discount Request",
    Scenario.AMENITY_EXCEPTION: "Property Info",
    Scenario.ORPHAN_NIGHT_EXCEPTION: "Availability Request",
    Scenario.ACCESS_CODE_RELEASE: "Check-in/Check-out",
    Scenario.EARLY_CHECKIN: "Check-in/Check-out",
    Scenario.LATE_CHECKOUT: "Check-in/Check-out",
    Scenario.MAINTENANCE_REQUEST: "Operational Task",
    Scenario.COMPLAINT_COMPENSATION: "Complaint",
    Scenario.MIN_STAY_EXCEPTION: "Availability Request",
    Scenario.EXTRA_BED_REQUEST: "Extra Service Request",
    Scenario.PET_POLICY_EXCEPTION: "Policy Request",
    Scenario.PARKING_REQUEST: "Property Info",
    Scenario.BOOKING_EXTENSION: "Booking Modification",
    Scenario.CANCELLATION_REQUEST: "Booking Modification",
    Scenario.PRICE_NEGOTIATION: "Discount Request",
    Scenario.SPECIAL_REQUEST: "Extra Service Request",
    Scenario.DAMAGE_REPORT: "Complaint",
    Scenario.NOISE_COMPLAINT: "Escalated Complaint",
    Scenario.LOST_ITEM: "Operational Task",
    Scenario.GENERAL: "Other",
}


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SentimentResult:
    """Aggregated guest sentiment over a time window.

    Attributes:
        score: Average sentiment score (1.0–10.0).
        label: Human-readable label (Negative/Neutral/Positive/Very Positive).
        description: Tone description for the dashboard.
        total_cases: Number of cases analyzed.
        positive_count: Cases with positive outcomes.
        negative_count: Cases with negative outcomes.
        neutral_count: Cases with neutral/unknown outcomes.
    """

    score: float = 5.0
    label: str = "Neutral"
    description: str = ""
    total_cases: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "score": round(self.score, 1),
            "label": self.label,
            "description": self.description,
            "total_cases": self.total_cases,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "neutral_count": self.neutral_count,
        }


@dataclass(frozen=True, slots=True)
class EscalationBreakdown:
    """Escalation counts grouped by category.

    Attributes:
        categories: Dict of category name → count.
        total_escalated: Total number of escalated cases.
        total_auto_resolved: Cases resolved without escalation.
        escalation_rate: Fraction of cases that were escalated.
    """

    categories: dict[str, int] = field(default_factory=dict)
    total_escalated: int = 0
    total_auto_resolved: int = 0
    escalation_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "categories": self.categories,
            "total_escalated": self.total_escalated,
            "total_auto_resolved": self.total_auto_resolved,
            "escalation_rate": round(self.escalation_rate, 3),
        }


@dataclass(frozen=True, slots=True)
class AccuracyResult:
    """AI accuracy metrics for a property.

    Attributes:
        property_id: Property identifier.
        accuracy_pct: Percentage of AI responses accepted without override.
        total_decisions: Total decisions evaluated.
        accepted_count: Decisions accepted without PM override.
        overridden_count: Decisions where PM intervened.
        escalated_count: Decisions that required escalation.
    """

    property_id: str = ""
    accuracy_pct: float = 0.0
    total_decisions: int = 0
    accepted_count: int = 0
    overridden_count: int = 0
    escalated_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "property_id": self.property_id,
            "accuracy_pct": round(self.accuracy_pct, 1),
            "total_decisions": self.total_decisions,
            "accepted_count": self.accepted_count,
            "overridden_count": self.overridden_count,
            "escalated_count": self.escalated_count,
        }


# ---------------------------------------------------------------------------
# AnalyticsService
# ---------------------------------------------------------------------------

class AnalyticsService:
    """Computes aggregated analytics from DecisionCase data.

    Stateless service — all state lives in the DecisionCaseStore.

    Attributes:
        _store: DecisionCase persistence.
        _log: Bound structured logger.
    """

    def __init__(self, store: DecisionCaseStore) -> None:
        self._store = store
        self._log = logger.bind(component="analytics_service")

    async def compute_sentiment(
        self,
        *,
        property_id: str | None = None,
        days: int = _DEFAULT_WINDOW_DAYS,
    ) -> SentimentResult:
        """Compute aggregated guest sentiment over a time window.

        Analyzes DecisionCase outcomes: positive outcomes raise sentiment,
        negative outcomes lower it, and neutral outcomes contribute a
        baseline score.

        Args:
            property_id: Optional property filter (None = all properties).
            days: Number of days to look back.

        Returns:
            SentimentResult with aggregated score and breakdown.
        """
        cases = await self._store.search(
            property_id=property_id,
            limit=500,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [c for c in cases if c.created_at >= cutoff]

        if not recent:
            return SentimentResult(
                description="No data available for this period.",
            )

        positive = 0
        negative = 0
        neutral = 0
        total_score = 0.0

        for case in recent:
            if not case.has_outcome:
                neutral += 1
                total_score += _NEUTRAL_SENTIMENT
                continue

            if case.outcome.is_positive_signal:
                positive += 1
                total_score += _POSITIVE_SENTIMENT
            elif case.outcome.is_negative_signal:
                negative += 1
                total_score += _NEGATIVE_SENTIMENT
            else:
                neutral += 1
                total_score += _NEUTRAL_SENTIMENT

        avg_score = total_score / len(recent)
        label, description = _classify_sentiment(avg_score)

        return SentimentResult(
            score=round(avg_score, 1),
            label=label,
            description=description,
            total_cases=len(recent),
            positive_count=positive,
            negative_count=negative,
            neutral_count=neutral,
        )

    async def compute_escalation_breakdown(
        self,
        *,
        property_id: str | None = None,
        days: int = _DEFAULT_WINDOW_DAYS,
    ) -> EscalationBreakdown:
        """Compute escalation counts grouped by dashboard category.

        Maps each DecisionCase scenario to a Cendra dashboard category
        and counts how many cases in each category required PM escalation.

        Args:
            property_id: Optional property filter.
            days: Number of days to look back.

        Returns:
            EscalationBreakdown with per-category counts.
        """
        cases = await self._store.search(
            property_id=property_id,
            limit=500,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [c for c in cases if c.created_at >= cutoff]

        if not recent:
            return EscalationBreakdown()

        category_counts: Counter[str] = Counter()
        escalated = 0
        auto_resolved = 0

        for case in recent:
            was_escalated = (
                case.has_outcome
                and case.outcome.approval_required
            )
            was_overridden = (
                case.has_outcome
                and case.outcome.human_overrode
            )

            if was_escalated or was_overridden:
                escalated += 1
                category = _SCENARIO_CATEGORY.get(
                    case.scenario, "Other",
                )
                category_counts[category] += 1
            else:
                auto_resolved += 1

        total = escalated + auto_resolved
        rate = escalated / total if total > 0 else 0.0

        return EscalationBreakdown(
            categories=dict(category_counts.most_common()),
            total_escalated=escalated,
            total_auto_resolved=auto_resolved,
            escalation_rate=rate,
        )

    async def compute_accuracy(
        self,
        *,
        property_id: str,
        days: int = _DEFAULT_WINDOW_DAYS,
    ) -> AccuracyResult:
        """Compute AI accuracy percentage for a specific property.

        Accuracy = (decisions accepted without PM override) / total.
        This matches the "AI Accuracy" column in Cendra's Knowledge Base
        property list.

        Args:
            property_id: Property identifier.
            days: Number of days to look back.

        Returns:
            AccuracyResult with accuracy percentage and breakdown.
        """
        cases = await self._store.search(
            property_id=property_id,
            limit=500,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [c for c in cases if c.created_at >= cutoff and c.has_outcome]

        if not recent:
            return AccuracyResult(property_id=property_id)

        accepted = 0
        overridden = 0
        escalated = 0

        for case in recent:
            if case.outcome.human_overrode:
                overridden += 1
            elif case.outcome.approval_required:
                escalated += 1
            else:
                accepted += 1

        total = len(recent)
        accuracy = (accepted / total * 100) if total > 0 else 0.0

        return AccuracyResult(
            property_id=property_id,
            accuracy_pct=round(accuracy, 1),
            total_decisions=total,
            accepted_count=accepted,
            overridden_count=overridden,
            escalated_count=escalated,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_sentiment(score: float) -> tuple[str, str]:
    """Classify a sentiment score into label and description.

    Args:
        score: Average sentiment score (1.0–10.0).

    Returns:
        Tuple of (label, description).
    """
    if score >= 8.0:
        return "Very Positive", "Warm, appreciative tone with high guest satisfaction."
    if score >= 6.5:
        return "Positive", "Friendly and engaged, generally satisfied guests."
    if score >= 4.5:
        return "Neutral", "Factual tone, polite but unemotional, minimal engagement."
    if score >= 3.0:
        return "Negative", "Dissatisfied guests, complaints or frustration detected."
    return "Very Negative", "Strongly negative sentiment, urgent issues present."
