"""Build :class:`PropertyProfile` snapshots from the unified GraphQL layer.

The harvester is the bridge between Cendra's onboarding GraphQL
endpoint and Brain Engine's "what Brain knows" surface: it pulls the
rich :class:`UnifiedProperty` payload plus per-property rate plans
and reviews, folds them into a single aggregate
:class:`PropertyProfile`, and upserts it into a
:class:`PropertyProfileStore`.

Design notes:

- The harvester is **read-only** with respect to everything except
  its own profile store.  It never mutates GraphQL, never writes to
  the decision-case or pattern stores; that is still the bootstrap
  pipeline's job.
- Counts for reservations / conversations are injected from the
  caller (the bootstrap pipeline has already iterated them) so the
  harvester does not duplicate fetches.  When the caller has no
  counts yet it can pass zeros and a later harvest will overwrite
  them.
- Transport failures from the underlying readers bubble up as
  :class:`UnifiedDataReaderError`; the harvester itself does not
  translate them further so the bootstrap pipeline can decide
  whether to treat the failure as fatal for the whole property.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from brain_engine.integrations.unified_data.readers import (
    PropertyDetail,
    RatePlanSummary,
    ReviewSummary,
    UnifiedDataReaderError,
    UnifiedPropertyReader,
    UnifiedRatePlanReader,
    UnifiedReviewReader,
)
from brain_engine.profiles.models import (
    KnowledgeSection,
    PropertyProfile,
    ReviewAggregate,
)
from brain_engine.profiles.store import PropertyProfileStore

__all__ = [
    "HarvestCounts",
    "HarvestResult",
    "PropertyProfileHarvester",
]


logger = structlog.get_logger(__name__)


_DEFAULT_LIST_LIMIT: Final[int] = 200


@dataclass(frozen=True, slots=True)
class HarvestCounts:
    """Volumetrics the harvester cannot compute on its own.

    Attributes:
        reservation_count: Reservations already ingested by the
            bootstrap pipeline for this property.
        conversation_count: Conversations already ingested.
        last_reservation_at: Timestamp of the most recent reservation,
            or ``None`` when nothing has been ingested yet.
        last_conversation_at: Timestamp of the most recent
            conversation, or ``None`` when nothing has been ingested
            yet.
        unanswered_thread_count: Guest threads whose final message is
            a guest message waiting on a PM reply (Mümin's step 12).
    """

    reservation_count: int = 0
    conversation_count: int = 0
    last_reservation_at: datetime | None = None
    last_conversation_at: datetime | None = None
    unanswered_thread_count: int = 0


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """Outcome of one harvest call."""

    profile: PropertyProfile
    rate_plans: tuple[RatePlanSummary, ...]
    reviews: tuple[ReviewSummary, ...]


class PropertyProfileHarvester:
    """Fetch + aggregate + persist a :class:`PropertyProfile`.

    Args:
        property_reader: Pulls the :class:`PropertyDetail` payload.
        rate_plan_reader: Pulls rate plans for the property.
        review_reader: Pulls reviews for the property.
        profile_store: Persistence for the resulting snapshot.
        list_limit: Page size used when reading rate plans / reviews;
            defaults to 200 which is enough for the overwhelming
            majority of single-property tenants and still caps the
            blast radius of a runaway query.
    """

    def __init__(
        self,
        *,
        property_reader: UnifiedPropertyReader,
        rate_plan_reader: UnifiedRatePlanReader,
        review_reader: UnifiedReviewReader,
        profile_store: PropertyProfileStore,
        list_limit: int = _DEFAULT_LIST_LIMIT,
        es_reader: Any = None,
    ) -> None:
        self._properties = property_reader
        self._rate_plans = rate_plan_reader
        self._reviews = review_reader
        self._store = profile_store
        self._list_limit = max(1, int(list_limit))
        # Optional direct-Elasticsearch property reader.  When wired, the
        # harvester reads the property detail from the canonical
        # ``unified_properties`` index FIRST (every property, no GraphQL
        # customer/org scoping); the GraphQL reader is the fallback.
        # ``None`` keeps the GraphQL-only behaviour byte-for-byte.
        self._es_reader = es_reader
        self._log = logger.bind(component="property_profile_harvester")

    async def harvest(
        self,
        *,
        property_channel_id: str,
        customer_id: str,
        org_id: str,
        provider_type: str,
        owner_id: str = "",
        counts: HarvestCounts | None = None,
    ) -> HarvestResult | None:
        """Build + persist one profile.

        Returns ``None`` when the property is unknown to the
        onboarding-api (``property(..)`` resolver returned null);
        otherwise returns the full :class:`HarvestResult` so the
        bootstrap pipeline can surface rate plans / reviews in its
        report without re-querying.

        Args:
            property_channel_id: ``channelEntityId`` of the property to
                harvest.
            customer_id: Cendra workspace id.
            org_id: Cendra organisation id.
            provider_type: ``ProviderType`` enum string.
            owner_id: Cendra ``ownerId`` for the property owner.
                Defaults to ``""`` because the unified GraphQL layer
                does not yet expose ``ownerId``; callers that already
                know the value (e.g. via Cendra MCP) can supply it so
                the §10 priority chain reaches the owner-flexibility
                preference tier without falling back to ``customer_id``.
            counts: Reservation / conversation volumetrics the
                bootstrap pipeline already computed.
        """
        if not property_channel_id:
            raise ValueError("property_channel_id is required")

        # Property detail comes from the canonical ES ``unified_properties``
        # index FIRST (every property, no GraphQL customer/org scoping), so
        # any selectable property builds a profile.  The GraphQL reader is
        # the fallback when ES is disabled / unreachable / has no doc.
        detail = await self._es_get_detail(property_channel_id)
        if detail is None:
            detail = await self._properties.get_detail(
                channel_entity_id=property_channel_id,
            )
        if detail is None:
            self._log.warning(
                "profile.detail_missing",
                property_channel_id=property_channel_id,
            )
            return None

        rate_plans = await self._safe_list(
            self._rate_plans.list_for_property,
            property_channel_id=property_channel_id,
            domain="rate_plans",
        )
        reviews = await self._safe_list(
            self._reviews.list_for_property,
            property_channel_id=property_channel_id,
            domain="reviews",
        )

        now = datetime.now(UTC)
        harvest_counts = counts or HarvestCounts()
        profile = _build_profile(
            detail=detail,
            customer_id=customer_id,
            org_id=org_id,
            provider_type=provider_type,
            owner_id=owner_id,
            rate_plans=rate_plans,
            reviews=reviews,
            counts=harvest_counts,
            built_at=now,
        )
        await self._store.put(profile)
        self._log.info(
            "profile.built",
            property_channel_id=property_channel_id,
            rate_plans=len(rate_plans),
            reviews=len(reviews),
            reservations=harvest_counts.reservation_count,
            conversations=harvest_counts.conversation_count,
            knowledge_percentage=detail.knowledge_percentage,
            owner_id=owner_id,
        )
        return HarvestResult(
            profile=profile,
            rate_plans=tuple(rate_plans),
            reviews=tuple(reviews),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _safe_list(
        self,
        fetch: Any,
        *,
        property_channel_id: str,
        domain: str,
    ) -> list[Any]:
        """Call a reader's ``list_for_property``; log + swallow reader errors.

        Rate plan / review feeds are known to be sparse on dev — a
        missing domain is not a reason to fail the whole harvest.
        """
        try:
            return await fetch(
                property_channel_id=property_channel_id,
                limit=self._list_limit,
                skip=0,
            )
        except UnifiedDataReaderError as exc:
            self._log.warning(
                "profile.domain_fetch_failed",
                property_channel_id=property_channel_id,
                domain=domain,
                reason=str(exc),
            )
            return []

    async def _es_get_detail(
        self,
        property_channel_id: str,
    ) -> PropertyDetail | None:
        """Read the property detail from ES ``unified_properties``,
        failing open.

        Returns ``None`` when no ES reader is wired, the property is
        absent from the index, or any ES error occurs — the harvester
        then falls back to the GraphQL property reader.
        """
        if self._es_reader is None:
            return None
        try:
            return await self._es_reader.get_detail(property_channel_id)
        except Exception as exc:  # fail open — ES must never break harvest
            self._log.warning(
                "profile.es_detail_failed",
                property_channel_id=property_channel_id,
                reason=str(exc),
            )
            return None


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _build_profile(
    *,
    detail: PropertyDetail,
    customer_id: str,
    org_id: str,
    provider_type: str,
    owner_id: str,
    rate_plans: list[RatePlanSummary],
    reviews: list[ReviewSummary],
    counts: HarvestCounts,
    built_at: datetime,
) -> PropertyProfile:
    """Fold reader outputs + volumetrics into a :class:`PropertyProfile`."""
    review_aggregate = _aggregate_reviews(reviews)
    latest_rate_plan_at = built_at if rate_plans else None
    latest_review_at = review_aggregate.latest_review_at

    description_languages = tuple(
        sorted({
            str(desc.get("language", "")).strip()
            for desc in detail.descriptions
            if _as_mapping(desc).get("language")
        })
    )
    amenity_codes = tuple(
        sorted({
            str(amenity.get("code", "")).strip()
            for amenity in detail.amenities
            if _as_mapping(amenity).get("code")
        })
    )

    reservations_notes = ""
    conversations_notes = (
        f"{counts.unanswered_thread_count} unanswered guest threads"
        if counts.unanswered_thread_count
        else ""
    )

    return PropertyProfile(
        property_channel_id=detail.channel_entity_id,
        pms_id=detail.pms_id,
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        title=detail.title,
        is_active=detail.is_active,
        city=detail.city,
        country=detail.country,
        property_type=detail.property_type,
        max_occupancy=detail.max_occupancy,
        bedrooms=detail.bedrooms,
        bathrooms=detail.bathrooms,
        base_currency=detail.base_currency,
        base_price=detail.base_price,
        knowledge_percentage=detail.knowledge_percentage,
        amenity_codes=amenity_codes,
        image_count=len(detail.images),
        room_count=len(detail.rooms),
        description_languages=description_languages,
        reservations=KnowledgeSection(
            name="reservations",
            item_count=counts.reservation_count,
            last_ingested_at=counts.last_reservation_at,
            notes=reservations_notes,
        ),
        conversations=KnowledgeSection(
            name="conversations",
            item_count=counts.conversation_count,
            last_ingested_at=counts.last_conversation_at,
            notes=conversations_notes,
        ),
        rate_plans=KnowledgeSection(
            name="rate_plans",
            item_count=len(rate_plans),
            last_ingested_at=latest_rate_plan_at,
        ),
        reviews=KnowledgeSection(
            name="reviews",
            item_count=len(reviews),
            last_ingested_at=latest_review_at,
        ),
        review_aggregate=review_aggregate,
        owner_id=owner_id,
        static_payload=_build_static_payload(detail),
        built_at=built_at,
    )


def _aggregate_reviews(reviews: list[ReviewSummary]) -> ReviewAggregate:
    """Reduce review rows to the aggregate block stored on the profile."""
    ratings: list[float] = [
        review.overall_rating
        for review in reviews
        if review.overall_rating is not None
    ]
    timestamps: list[datetime] = [
        review.review_date or review.created_at
        for review in reviews
        if (review.review_date or review.created_at) is not None
    ]
    average = sum(ratings) / len(ratings) if ratings else None
    latest = max(timestamps) if timestamps else None
    return ReviewAggregate(
        total=len(reviews),
        with_rating=len(ratings),
        average_rating=average,
        latest_review_at=latest,
    )


def _build_static_payload(detail: PropertyDetail) -> Mapping[str, Any]:
    """Return a JSON-friendly snapshot of the unified-property payload."""
    return {
        "title": detail.title,
        "is_active": detail.is_active,
        "city": detail.city,
        "country": detail.country,
        "country_code": detail.country_code,
        "zip_code": detail.zip_code,
        "address": detail.address,
        "street": detail.street,
        "latitude": detail.latitude,
        "longitude": detail.longitude,
        "time_zone": detail.time_zone,
        "property_type": detail.property_type,
        "bedrooms": detail.bedrooms,
        "bathrooms": detail.bathrooms,
        "beds": detail.beds,
        "max_occupancy": detail.max_occupancy,
        "area_square_feet": detail.area_square_feet,
        "base_currency": detail.base_currency,
        "base_price": detail.base_price,
        "cleaning_fee": detail.cleaning_fee,
        "pet_fee": detail.pet_fee,
        "security_deposit_fee": detail.security_deposit_fee,
        "check_in_time": detail.check_in_time,
        "check_out_time": detail.check_out_time,
        "min_nights": detail.min_nights,
        "max_nights": detail.max_nights,
        "instant_bookable": detail.instant_bookable,
        "pets_allowed": detail.pets_allowed,
        "has_parking": detail.has_parking,
        "has_wifi": detail.has_wifi,
        "wifi_network": detail.wifi_network,
        "wifi_password": detail.wifi_password,
        "door_code": detail.door_code,
        "license_code": detail.license_code,
        "host_name": detail.host_name,
        "listing_id": detail.listing_id,
        "status": detail.status,
        "knowledge_percentage": detail.knowledge_percentage,
        "amenities": [dict(a) for a in detail.amenities],
        "images": [dict(i) for i in detail.images],
        "rooms": [dict(r) for r in detail.rooms],
        "descriptions": [dict(d) for d in detail.descriptions],
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or an empty dict."""
    if isinstance(value, Mapping):
        return value
    return {}
