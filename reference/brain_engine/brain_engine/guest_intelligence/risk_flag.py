"""RiskFlagSystem — Flags problematic guests for owner awareness.

Automatically detects risk indicators from guest history:
- Repeated damage claims
- Frequent complaints
- Rule violations
- Payment issues

Generates warnings visible to the owner when a flagged guest makes a new booking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from brain_engine.guest_intelligence.profile_builder import GuestProfile

logger = logging.getLogger(__name__)


class RiskLevel(StrEnum):
    """Risk level classification for guests."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RiskFlag:
    """A single risk flag for a guest.

    Attributes:
        flag_type: Type of risk indicator.
        severity: How serious this flag is.
        description: Human-readable description.
        evidence: Data supporting this flag.
    """

    flag_type: str
    severity: RiskLevel
    description: str
    evidence: str = ""


@dataclass(slots=True)
class RiskAssessment:
    """Complete risk assessment for a guest.

    Attributes:
        guest_id: Guest identifier.
        guest_name: Guest display name.
        risk_level: Overall risk level.
        flags: Individual risk flags.
        recommendation: What the owner should know/do.
        allow_booking: Whether to recommend accepting the booking.
    """

    guest_id: str
    guest_name: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    flags: list[RiskFlag] = field(default_factory=list)
    recommendation: str = ""
    allow_booking: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "guest_id": self.guest_id,
            "guest_name": self.guest_name,
            "risk_level": self.risk_level.value,
            "flags": [
                {
                    "flag_type": f.flag_type,
                    "severity": f.severity.value,
                    "description": f.description,
                    "evidence": f.evidence,
                }
                for f in self.flags
            ],
            "recommendation": self.recommendation,
            "allow_booking": self.allow_booking,
        }


class RiskFlagSystem:
    """Evaluates guest risk based on historical behavior.

    Applies rule-based risk detection to guest profiles.
    The owner gets a clear assessment with actionable flags.
    """

    def assess(self, profile: GuestProfile) -> RiskAssessment:
        """Run risk assessment on a guest profile.

        Args:
            profile: Complete guest profile.

        Returns:
            RiskAssessment with flags and recommendation.
        """
        assessment = RiskAssessment(
            guest_id=profile.guest_id,
            guest_name=profile.guest_name,
        )

        # Check each risk factor
        self._check_damage_history(profile, assessment)
        self._check_complaints(profile, assessment)
        self._check_incident_rate(profile, assessment)
        self._check_negative_reviews(profile, assessment)

        # Determine overall risk level from flags
        self._compute_overall_risk(assessment)

        # Generate recommendation
        assessment.recommendation = self._build_recommendation(assessment)
        assessment.allow_booking = assessment.risk_level != RiskLevel.CRITICAL

        logger.info(
            "Risk assessment for guest %s: %s (%d flags)",
            profile.guest_id, assessment.risk_level, len(assessment.flags),
        )
        return assessment

    @staticmethod
    def _check_damage_history(
        profile: GuestProfile,
        assessment: RiskAssessment,
    ) -> None:
        """Flag guests with damage history."""
        if profile.damage_incidents == 0:
            return

        if profile.damage_incidents >= 3:
            assessment.flags.append(RiskFlag(
                flag_type="repeated_damage",
                severity=RiskLevel.CRITICAL,
                description="Multiple damage incidents across stays",
                evidence=f"{profile.damage_incidents} damage incidents in {profile.total_stays} stays",
            ))
        elif profile.damage_incidents >= 2:
            assessment.flags.append(RiskFlag(
                flag_type="damage_history",
                severity=RiskLevel.HIGH,
                description="Guest has caused damage in previous stays",
                evidence=f"{profile.damage_incidents} damage incidents",
            ))
        elif profile.damage_rate > 0.5:
            assessment.flags.append(RiskFlag(
                flag_type="high_damage_rate",
                severity=RiskLevel.MEDIUM,
                description="High proportion of stays with damage",
                evidence=f"Damage in {profile.damage_rate:.0%} of stays",
            ))

    @staticmethod
    def _check_complaints(
        profile: GuestProfile,
        assessment: RiskAssessment,
    ) -> None:
        """Flag guests with complaint history."""
        if profile.complaints == 0:
            return

        if profile.complaints >= 3:
            assessment.flags.append(RiskFlag(
                flag_type="frequent_complaints",
                severity=RiskLevel.HIGH,
                description="Guest frequently files complaints",
                evidence=f"{profile.complaints} complaints filed",
            ))
        elif profile.complaints >= 1:
            assessment.flags.append(RiskFlag(
                flag_type="has_complaints",
                severity=RiskLevel.MEDIUM,
                description="Guest has filed complaints in the past",
                evidence=f"{profile.complaints} complaint(s)",
            ))

    @staticmethod
    def _check_incident_rate(
        profile: GuestProfile,
        assessment: RiskAssessment,
    ) -> None:
        """Flag guests with high incident rates."""
        if profile.total_stays < 2:
            return

        if profile.incident_rate >= 0.7:
            assessment.flags.append(RiskFlag(
                flag_type="high_incident_rate",
                severity=RiskLevel.HIGH,
                description="Most stays involve some kind of incident",
                evidence=f"Incidents in {profile.incident_rate:.0%} of stays",
            ))

    @staticmethod
    def _check_negative_reviews(
        profile: GuestProfile,
        assessment: RiskAssessment,
    ) -> None:
        """Flag guests with predominantly negative reviews."""
        total_reviews = profile.positive_reviews + profile.negative_reviews
        if total_reviews == 0:
            return

        negative_ratio = profile.negative_reviews / total_reviews
        if negative_ratio >= 0.5 and profile.negative_reviews >= 2:
            assessment.flags.append(RiskFlag(
                flag_type="negative_reviews",
                severity=RiskLevel.MEDIUM,
                description="Majority of host reviews are negative",
                evidence=(
                    f"{profile.negative_reviews} negative / "
                    f"{total_reviews} total reviews"
                ),
            ))

    @staticmethod
    def _compute_overall_risk(assessment: RiskAssessment) -> None:
        """Determine overall risk level from individual flags."""
        if not assessment.flags:
            assessment.risk_level = RiskLevel.LOW
            return

        severities = [f.severity for f in assessment.flags]

        if RiskLevel.CRITICAL in severities:
            assessment.risk_level = RiskLevel.CRITICAL
        elif severities.count(RiskLevel.HIGH) >= 2:
            assessment.risk_level = RiskLevel.CRITICAL
        elif RiskLevel.HIGH in severities:
            assessment.risk_level = RiskLevel.HIGH
        elif severities.count(RiskLevel.MEDIUM) >= 2:
            assessment.risk_level = RiskLevel.HIGH
        elif RiskLevel.MEDIUM in severities:
            assessment.risk_level = RiskLevel.MEDIUM
        else:
            assessment.risk_level = RiskLevel.LOW

    @staticmethod
    def _build_recommendation(assessment: RiskAssessment) -> str:
        """Build owner-facing recommendation text."""
        match assessment.risk_level:
            case RiskLevel.LOW:
                return "No concerns. Guest has a clean history."
            case RiskLevel.MEDIUM:
                return (
                    "Minor concerns noted. Consider requiring a security deposit "
                    "or requesting extra photos before/after stay."
                )
            case RiskLevel.HIGH:
                return (
                    "Significant risk indicators. Recommend: higher security deposit, "
                    "strict house rules enforcement, and thorough photo inspection."
                )
            case RiskLevel.CRITICAL:
                return (
                    "CRITICAL: This guest has a history of repeated issues. "
                    "Recommend declining the booking or requiring maximum security deposit "
                    "and strict monitoring."
                )
