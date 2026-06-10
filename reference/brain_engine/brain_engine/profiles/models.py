"""Value objects for the property knowledge profile.

A :class:`PropertyProfile` is the "what Brain knows" snapshot that
onboarding step 5 (property-picked) renders into the UI and that the
sandbox interview and example-answer generator consume as grounding
context.

The profile is deliberately *aggregate* data — it stores counts,
coverage ratios and the static property payload rather than the full
raw unified documents, which live in cendra-pg / ES.  Downstream
stores (``DecisionCaseStore``, ``PatternRuleStore``) still own the
granular records; the profile is a read-optimised header that lets
the UI answer "do we know this property yet?" in one trip.

Every object is ``frozen=True, slots=True`` so it crosses thread /
async boundaries safely.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "KnowledgeSection",
    "PropertyProfile",
    "ReviewAggregate",
]


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReviewAggregate:
    """Aggregate review metrics scoped to one property.

    Attributes:
        total: Total review documents observed for the property.
        with_rating: Subset that carry a numeric ``overall_rating``.
        average_rating: Mean of ``overall_rating`` across
            ``with_rating`` rows; ``None`` when ``with_rating == 0``.
        latest_review_at: Timestamp of the most recent review, or
            ``None`` when the property has no reviews on file.
    """

    total: int
    with_rating: int
    average_rating: float | None
    latest_review_at: datetime | None


@dataclass(frozen=True, slots=True)
class KnowledgeSection:
    """Coverage snapshot for one onboarding knowledge domain.

    Sections map loosely to Mümin's 13-step flow — ``reservations``,
    ``conversations``, ``rate_plans``, ``reviews`` — so the UI can
    render progress per domain instead of one monolithic bar.

    Attributes:
        name: Stable domain key (``"reservations"``,
            ``"conversations"``, ``"rate_plans"``, ``"reviews"``).
        item_count: Number of records ingested for the domain.
        last_ingested_at: When the harvester last wrote into the
            domain; ``None`` when nothing has been ingested yet.
        notes: Optional free-form qualifier (e.g. "3 unanswered
            guest threads") surfaced in the UI.
    """

    name: str
    item_count: int
    last_ingested_at: datetime | None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PropertyProfile:
    """Aggregated "Brain knows" snapshot for one property.

    The profile is rebuilt idempotently by
    :class:`PropertyProfileHarvester`; consumers should treat it as a
    cache, not as a source of truth.

    Attributes:
        property_channel_id: ``channelEntityId`` / ``propertyChannelId``
            the property is keyed by across the unified schema.
        pms_id: PMS-native identifier (useful when a caller has a
            PMS id but not the channel id).
        customer_id: Cendra workspace id that owns the profile.
        org_id: Optional organisation id narrowing the workspace.
        owner_id: Cendra ``ownerId`` of the property owner.  Optional
            because the unified GraphQL ``UnifiedProperty.data`` payload
            does not yet expose ``ownerId`` directly — the bootstrap
            harvester leaves it empty and the runtime resolves owner
            scope by falling back to ``customer_id`` (the V1 mapping
            until Cendra ships ``ownerId`` through the unified layer).
            See ``project_auth_boundary_cendra_vs_brain_engine.md`` —
            Brain Engine never *issues* owner identities, it only reads
            them.
        provider_type: :class:`ProviderType` enum string
            (``"HOSTAWAY"``, ``"GUESTY"`` …) the property belongs to.
        title: Display name of the property.
        is_active: Whether the property is marked active in the PMS.
        city: City portion of the property address, when known.
        country: Country name (long form).
        property_type: Unified property-type code (``"apartment"``,
            ``"villa"`` …).
        max_occupancy: Nominal max guest count.
        bedrooms: Bedroom count.
        bathrooms: Bathroom count (may be fractional).
        base_currency: ISO currency code of ``base_price``.
        base_price: Advertised nightly base price.
        knowledge_percentage: 0.0–1.0 coverage metric Cendra computes
            inside ``UnifiedProperty.knowledgePercentage``.
        amenity_codes: Sorted tuple of amenity codes attached to the
            property.
        image_count: Number of images attached to the property.
        room_count: Number of child room records (for multi-room
            listings).
        description_languages: Sorted tuple of language codes that
            have at least one description.
        reservations: Coverage block for the reservations domain.
        conversations: Coverage block for the conversations domain.
        rate_plans: Coverage block for the rate-plans domain.
        reviews: Coverage block for the reviews domain.
        review_aggregate: Aggregate review metrics.
        static_payload: Raw unified-property payload kept for the
            knowledge endpoint; ``MappingProxyType``-friendly, so
            callers must not mutate it.
        built_at: When the harvester produced this snapshot.
    """

    property_channel_id: str
    pms_id: str
    customer_id: str
    org_id: str
    provider_type: str
    title: str
    is_active: bool
    city: str
    country: str
    property_type: str
    max_occupancy: int
    bedrooms: int
    bathrooms: float
    base_currency: str
    base_price: float
    knowledge_percentage: float
    amenity_codes: tuple[str, ...]
    image_count: int
    room_count: int
    description_languages: tuple[str, ...]
    reservations: KnowledgeSection
    conversations: KnowledgeSection
    rate_plans: KnowledgeSection
    reviews: KnowledgeSection
    review_aggregate: ReviewAggregate
    owner_id: str = ""
    static_payload: Mapping[str, Any] = field(default_factory=dict)
    built_at: datetime = field(default_factory=_utc_now)

    @property
    def sections(self) -> tuple[KnowledgeSection, ...]:
        """Return knowledge sections in stable UI order."""
        return (
            self.reservations,
            self.conversations,
            self.rate_plans,
            self.reviews,
        )

    @property
    def coverage_ratio(self) -> float:
        """Return a simple 0.0–1.0 coverage heuristic.

        Defined as the fraction of knowledge sections that have at
        least one ingested record.  It intentionally stays coarse —
        callers that need domain-specific numbers should read
        :attr:`sections` directly.
        """
        filled = sum(1 for section in self.sections if section.item_count > 0)
        total = len(self.sections)
        if total == 0:
            return 0.0
        return filled / total
