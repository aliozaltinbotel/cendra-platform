"""CityKnowledgeGraph — Per-city and per-property accumulated knowledge.

Stores and retrieves learned knowledge about each city:
- Verified cleaners with scores
- Verified vendors by category
- Average response times
- Local patterns (peak seasons, holidays)
- Maturity level (NEW → LEARNING → MATURE)

Maturity affects cascade behavior:
- NEW (< 10 bookings): full cascade, all 4 levels
- LEARNING (10-30 bookings): skip bad cleaners
- MATURE (30+ bookings): direct call to best, minimal escalation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.smart_engine.scoring_engine import ScoringEngine

logger = logging.getLogger(__name__)


class MaturityLevel:
    """City knowledge maturity levels."""

    NEW = "NEW"          # < 10 bookings
    LEARNING = "LEARNING"  # 10-30 bookings
    MATURE = "MATURE"    # 30+ bookings


@dataclass(slots=True)
class CityProfile:
    """Accumulated knowledge about a city."""

    city: str
    cleaners: list[dict[str, Any]] = field(default_factory=list)
    vendors: list[dict[str, Any]] = field(default_factory=list)
    avg_response_time_hours: float = 0.0
    peak_seasons: list[str] = field(default_factory=list)
    total_bookings: int = 0
    maturity: str = MaturityLevel.NEW
    properties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "cleaners_count": len(self.cleaners),
            "vendors_count": len(self.vendors),
            "avg_response_time_hours": round(self.avg_response_time_hours, 1),
            "peak_seasons": self.peak_seasons,
            "total_bookings": self.total_bookings,
            "maturity": self.maturity,
            "properties_count": len(self.properties),
        }


@dataclass(slots=True)
class PropertyProfile:
    """Accumulated knowledge about a specific property."""

    property_id: str
    city: str = ""
    preferred_cleaner_id: str = ""
    preferred_vendors: dict[str, str] = field(default_factory=dict)  # category → vendor_id
    avg_turnaround_hours: float = 3.0
    total_turnovers: int = 0
    common_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_id": self.property_id,
            "city": self.city,
            "preferred_cleaner": self.preferred_cleaner_id,
            "preferred_vendors": self.preferred_vendors,
            "avg_turnaround_hours": round(self.avg_turnaround_hours, 1),
            "total_turnovers": self.total_turnovers,
            "common_issues": self.common_issues,
        }


class CityKnowledgeGraph:
    """Manages per-city and per-property accumulated knowledge.

    Stored in Redis (fast access) + Qdrant (semantic search).
    Grows with every booking — the system gets smarter.

    Args:
        scoring_engine: For retrieving scored cleaners/vendors.
        redis_client: For persistence.
    """

    def __init__(
        self,
        scoring_engine: ScoringEngine,
        redis_client: Any | None = None,
    ) -> None:
        self._scoring = scoring_engine
        self._redis = redis_client
        self._cities: dict[str, CityProfile] = {}
        self._properties: dict[str, PropertyProfile] = {}

    async def get_city_profile(self, city: str) -> CityProfile:
        """Get full profile for a city."""
        if city in self._cities:
            return self._cities[city]

        profile = CityProfile(city=city)

        # Load cleaners and vendors from scoring engine
        cleaners = await self._scoring.get_ranked("cleaner", city=city)
        vendors = await self._scoring.get_ranked("vendor", city=city)

        profile.cleaners = cleaners
        profile.vendors = vendors
        profile.maturity = self._calculate_maturity(profile)

        self._cities[city] = profile
        return profile

    async def get_property_profile(self, property_id: str) -> PropertyProfile:
        """Get accumulated knowledge for a specific property."""
        if property_id in self._properties:
            return self._properties[property_id]

        profile = PropertyProfile(property_id=property_id)

        # Load best cleaner for this property
        cleaners = await self._scoring.get_ranked(
            "cleaner", property_id=property_id, limit=1,
        )
        if cleaners:
            profile.preferred_cleaner_id = cleaners[0]["entity_id"]

        self._properties[property_id] = profile
        return profile

    async def record_turnover(
        self,
        property_id: str,
        city: str,
        cleaner_id: str = "",
        turnaround_hours: float = 0,
        issues: list[str] | None = None,
    ) -> None:
        """Record a completed turnover for learning.

        Called after each successful check-in.
        Updates city profile and property profile.
        """
        # Update city
        city_profile = await self.get_city_profile(city)
        city_profile.total_bookings += 1
        if property_id not in city_profile.properties:
            city_profile.properties.append(property_id)
        city_profile.maturity = self._calculate_maturity(city_profile)

        # Update property
        prop_profile = await self.get_property_profile(property_id)
        prop_profile.city = city
        prop_profile.total_turnovers += 1

        if cleaner_id:
            prop_profile.preferred_cleaner_id = cleaner_id

        if turnaround_hours > 0:
            # Running average
            n = prop_profile.total_turnovers
            prop_profile.avg_turnaround_hours = (
                (prop_profile.avg_turnaround_hours * (n - 1) + turnaround_hours) / n
            )

        if issues:
            for issue in issues:
                if issue not in prop_profile.common_issues:
                    prop_profile.common_issues.append(issue)

        logger.info(
            "Turnover recorded: %s in %s (maturity=%s, turnovers=%d)",
            property_id, city, city_profile.maturity, prop_profile.total_turnovers,
        )

    def get_cascade_strategy(self, city: str) -> str:
        """Determine cascade strategy based on city maturity.

        Returns:
            "full" — try all 4 levels (NEW city)
            "skip_bad" — skip cleaners with negative scores (LEARNING)
            "direct" — call best cleaner directly (MATURE)
        """
        profile = self._cities.get(city)
        if not profile:
            return "full"

        match profile.maturity:
            case MaturityLevel.NEW:
                return "full"
            case MaturityLevel.LEARNING:
                return "skip_bad"
            case MaturityLevel.MATURE:
                return "direct"
            case _:
                return "full"

    @staticmethod
    def _calculate_maturity(profile: CityProfile) -> str:
        """Determine maturity level based on bookings and resources."""
        bookings = profile.total_bookings
        cleaner_count = len(profile.cleaners)

        if bookings < 10 or cleaner_count < 2:
            return MaturityLevel.NEW
        elif bookings < 30:
            return MaturityLevel.LEARNING
        else:
            return MaturityLevel.MATURE
