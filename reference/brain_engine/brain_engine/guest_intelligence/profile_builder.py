"""GuestProfileBuilder — Aggregates guest data into comprehensive profiles.

Combines data from PMS bookings, incident history, episodic memory,
and knowledge graph to build a complete guest profile for decision-making.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GuestProfile:
    """Comprehensive guest profile aggregated from all data sources.

    Attributes:
        guest_id: Unique guest identifier.
        guest_name: Guest display name.
        phone: Phone number.
        email: Email address.
        total_stays: Number of completed stays.
        total_incidents: Number of incidents involving this guest.
        damage_incidents: Number of damage-related incidents.
        late_checkout_requests: Number of late checkout requests.
        average_review_rating: Average rating given by/about this guest.
        complaints: Number of complaints filed.
        positive_reviews: Number of positive reviews from hosts.
        negative_reviews: Number of negative reviews from hosts.
        properties_stayed: List of property IDs the guest stayed at.
        behavior_patterns: Detected behavioral patterns.
        last_stay_date: Date of the most recent stay.
        first_stay_date: Date of the first stay.
        loyalty_score: Computed loyalty score (0-100).
        risk_level: Computed risk level.
        tags: Labels/tags assigned to this guest.
        notes: Free-text notes about the guest.
    """

    guest_id: str = ""
    guest_name: str = ""
    phone: str = ""
    email: str = ""
    total_stays: int = 0
    total_incidents: int = 0
    damage_incidents: int = 0
    late_checkout_requests: int = 0
    average_review_rating: float = 0.0
    complaints: int = 0
    positive_reviews: int = 0
    negative_reviews: int = 0
    properties_stayed: list[str] = field(default_factory=list)
    behavior_patterns: list[str] = field(default_factory=list)
    last_stay_date: str = ""
    first_stay_date: str = ""
    loyalty_score: int = 0
    risk_level: str = "normal"
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "guest_id": self.guest_id,
            "guest_name": self.guest_name,
            "phone": self.phone,
            "email": self.email,
            "total_stays": self.total_stays,
            "total_incidents": self.total_incidents,
            "damage_incidents": self.damage_incidents,
            "late_checkout_requests": self.late_checkout_requests,
            "average_review_rating": self.average_review_rating,
            "complaints": self.complaints,
            "positive_reviews": self.positive_reviews,
            "negative_reviews": self.negative_reviews,
            "properties_stayed": self.properties_stayed,
            "behavior_patterns": self.behavior_patterns,
            "last_stay_date": self.last_stay_date,
            "first_stay_date": self.first_stay_date,
            "loyalty_score": self.loyalty_score,
            "risk_level": self.risk_level,
            "tags": self.tags,
            "notes": self.notes,
        }

    @property
    def is_repeat_guest(self) -> bool:
        """Whether the guest has stayed more than once."""
        return self.total_stays > 1

    @property
    def damage_rate(self) -> float:
        """Proportion of stays involving damage."""
        if self.total_stays == 0:
            return 0.0
        return self.damage_incidents / self.total_stays

    @property
    def incident_rate(self) -> float:
        """Proportion of stays involving any incident."""
        if self.total_stays == 0:
            return 0.0
        return self.total_incidents / self.total_stays


class GuestProfileBuilder:
    """Builds guest profiles from multiple data sources.

    Aggregates data from guest history, episodic memory, knowledge graph,
    and PMS integration to create a complete GuestProfile.

    Args:
        guest_history: GuestHistoryStore from brain_engine.memory.
        episodic: EpisodicMemory for event queries.
        knowledge_graph: KnowledgeGraph for fact/belief queries.
    """

    def __init__(
        self,
        guest_history: Any | None = None,
        episodic: Any | None = None,
        knowledge_graph: Any | None = None,
    ) -> None:
        self._history = guest_history
        self._episodic = episodic
        self._kg = knowledge_graph

    async def build_profile(self, guest_id: str) -> GuestProfile:
        """Build a comprehensive profile for a guest.

        Args:
            guest_id: The guest's unique identifier.

        Returns:
            GuestProfile with all available data aggregated.
        """
        profile = GuestProfile(guest_id=guest_id)

        # Pull data from guest history
        if self._history:
            await self._enrich_from_history(profile)

        # Pull events from episodic memory
        if self._episodic:
            await self._enrich_from_episodic(profile)

        # Pull facts from knowledge graph
        if self._kg:
            await self._enrich_from_knowledge_graph(profile)

        # Detect behavior patterns
        self._detect_patterns(profile)

        logger.info(
            "Built profile for guest %s: %d stays, loyalty=%d, risk=%s",
            guest_id, profile.total_stays, profile.loyalty_score, profile.risk_level,
        )
        return profile

    async def _enrich_from_history(self, profile: GuestProfile) -> None:
        """Enrich profile from GuestHistoryStore."""
        try:
            guest = await self._history.get_guest(profile.guest_id)
            if not guest:
                return

            profile.guest_name = guest.name
            profile.phone = guest.phone or ""
            profile.email = getattr(guest, "email", "")

            bookings = await self._history.get_bookings(profile.guest_id)
            profile.total_stays = len(bookings)
            if bookings:
                dates = [b.check_in for b in bookings if b.check_in]
                if dates:
                    profile.first_stay_date = min(dates)
                    profile.last_stay_date = max(dates)
                profile.properties_stayed = list({
                    b.property_id for b in bookings if b.property_id
                })

            incidents = await self._history.get_incidents(profile.guest_id)
            profile.total_incidents = len(incidents)
            profile.damage_incidents = sum(
                1 for inc in incidents if getattr(inc, "damage_detected", False)
            )
            profile.late_checkout_requests = sum(
                1 for inc in incidents
                if getattr(inc, "late_checkout_time", None)
            )
        except Exception:
            logger.exception(
                "Failed to enrich profile from history for %s",
                profile.guest_id,
            )

    async def _enrich_from_episodic(self, profile: GuestProfile) -> None:
        """Enrich profile from episodic memory events."""
        try:
            episodes = await self._episodic.query(
                entity_id=profile.guest_id,
                limit=100,
            )
            for ep in episodes:
                event = ep.get("event", "")
                if event == "complaint_filed":
                    profile.complaints += 1
                elif event == "positive_review":
                    profile.positive_reviews += 1
                elif event == "negative_review":
                    profile.negative_reviews += 1
        except Exception:
            logger.exception(
                "Failed to enrich profile from episodic for %s",
                profile.guest_id,
            )

    async def _enrich_from_knowledge_graph(self, profile: GuestProfile) -> None:
        """Enrich profile from knowledge graph facts/beliefs."""
        try:
            facts = await self._kg.query_entity(profile.guest_id)
            for fact in facts:
                content = fact.get("content", "")
                tags = fact.get("tags", [])
                if "behavior_pattern" in tags:
                    profile.behavior_patterns.append(content)
                if "guest_tag" in tags:
                    profile.tags.append(content)
        except Exception:
            logger.exception(
                "Failed to enrich profile from KG for %s",
                profile.guest_id,
            )

    @staticmethod
    def _detect_patterns(profile: GuestProfile) -> None:
        """Detect behavioral patterns from aggregated data."""
        if profile.total_stays >= 3:
            profile.behavior_patterns.append("repeat_guest")

        if profile.late_checkout_requests > 0 and profile.total_stays > 0:
            late_rate = profile.late_checkout_requests / profile.total_stays
            if late_rate >= 0.5:
                profile.behavior_patterns.append("frequent_late_checkout")

        if profile.damage_rate >= 0.3:
            profile.behavior_patterns.append("damage_prone")

        if profile.total_stays >= 5 and profile.damage_incidents == 0:
            profile.behavior_patterns.append("careful_guest")

        if profile.positive_reviews >= 3:
            profile.behavior_patterns.append("well_reviewed")
