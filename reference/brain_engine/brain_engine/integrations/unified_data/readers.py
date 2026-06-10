"""Read-side adapters for the Cendra onboarding-api GraphQL layer.

Each reader owns one unified entity (property, rate plan, review) and
returns a small frozen dataclass that downstream Brain Engine modules
can depend on without reaching into raw GraphQL payloads.

Design notes:

- The ``properties`` / ``ratePlans`` / ``reviews`` list queries do
  **not** accept a server-side property filter (verified
  2026-04-24). Callers that need to narrow by property_id pass the
  id to ``list_for_property`` / ``get_detail`` and the reader filters
  client-side against the candidate identifier fields each unified
  document carries.
- Transport failures raised by :class:`UnifiedDataGraphQLClient` are
  wrapped in :class:`UnifiedDataReaderError` so the calling layer
  (profile harvester, knowledge endpoint) never needs to import the
  low-level transport exception taxonomy.
- Everything is read-only.  The readers never mutate the client, the
  cache, or any store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final

import structlog

from brain_engine.integrations.unified_data.client import (
    UnifiedDataGraphQLClient,
)
from brain_engine.integrations.unified_data.errors import UnifiedDataError
from brain_engine.integrations.unified_data.queries import (
    PROPERTIES_LIST_QUERY,
    PROPERTY_DETAIL_QUERY,
    RATE_PLANS_LIST_QUERY,
    RATE_PLANS_WITH_CALENDAR_QUERY,
    REVIEWS_LIST_QUERY,
)

__all__ = [
    "CalendarDay",
    "OccupancyOption",
    "PropertyDetail",
    "PropertySummary",
    "RatePlanSummary",
    "RatePlanWithCalendar",
    "ReviewSummary",
    "UnifiedDataReaderError",
    "UnifiedPropertyReader",
    "UnifiedRatePlanReader",
    "UnifiedReviewReader",
]


logger = structlog.get_logger(__name__)


_DEFAULT_LIST_LIMIT: Final[int] = 50
_MAX_LIST_LIMIT: Final[int] = 1000


class UnifiedDataReaderError(UnifiedDataError):
    """Domain error raised by every reader in this module.

    Wraps any transport / GraphQL failure so downstream callers can
    catch a single exception type regardless of which reader they
    invoked.
    """

    def __init__(self, reader: str, message: str) -> None:
        super().__init__(f"{reader}: {message}")
        self.reader = reader


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PropertySummary:
    """Slim listing row for the property picker.

    Mirrors the fields returned by :data:`PROPERTIES_LIST_QUERY`;
    matches the shape of Mümin's ``ListProperties`` reference query.
    """

    channel_entity_id: str
    pms_id: str
    title: str
    is_active: bool
    city: str
    country: str
    property_type: str
    max_occupancy: int
    bedrooms: int
    bathrooms: float
    base_price: float
    base_currency: str
    listing_id: str


@dataclass(frozen=True, slots=True)
class PropertyDetail:
    """Full static profile for a single property.

    Composed of the top-level wrapper identifiers plus the unified
    ``UnifiedProperty`` payload, with amenities / images / rooms /
    descriptions flattened to JSON-safe tuples.
    """

    channel_entity_id: str
    pms_id: str
    transformed_at: datetime | None
    title: str
    is_active: bool
    city: str
    country: str
    country_code: str
    zip_code: str
    address: str
    street: str
    latitude: float | None
    longitude: float | None
    time_zone: str
    property_type: str
    bedrooms: int
    bathrooms: float
    beds: int
    max_occupancy: int
    area_square_feet: float | None
    base_currency: str
    base_price: float
    cleaning_fee: float | None
    pet_fee: float | None
    security_deposit_fee: float | None
    check_in_time: str
    check_out_time: str
    min_nights: int
    max_nights: int
    instant_bookable: bool
    pets_allowed: bool
    has_parking: bool
    has_wifi: bool
    wifi_network: str
    wifi_password: str
    door_code: str
    license_code: str
    host_name: str
    listing_id: str
    status: str
    knowledge_percentage: float
    amenities: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    images: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    rooms: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    descriptions: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RatePlanSummary:
    """One rate-plan row scoped to a single property.

    Per-day calendar is intentionally not exposed here; consumers that
    need availability rows should query ``propertyRestrictions``
    directly.
    """

    rate_plan_id: str
    property_channel_id: str
    property_pms_id: str
    name: str
    title: str
    currency: str
    sell_mode: str
    rate_mode: str
    meal_type: str
    is_active: bool
    parent_rate_plan_id: str
    children_fee: float
    infant_fee: float


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """One day of a rate plan's calendar.

    Mirrors the ``calendar(from: to:)`` sub-selection exposed by the
    ``ratePlans`` resolver.  ``date`` is the ISO-8601 calendar date
    (no time component); ``price`` is nominal sell price in the rate
    plan's currency.  ``note`` carries manager overrides (e.g. ``"VIP
    block"``) and is frequently empty.
    """

    date: str
    note: str
    stop_sell: bool
    count_available_units: int
    price: float


@dataclass(frozen=True, slots=True)
class OccupancyOption:
    """One occupancy-tier pricing row for a rate plan."""

    occupancy: int
    is_primary: bool
    rate: float


@dataclass(frozen=True, slots=True)
class RatePlanWithCalendar:
    """Rate plan enriched with calendar window and occupancy options.

    Used by the property-detail UI where the caller must supply an
    explicit ``from``/``to`` date window.  The inner slim identity
    fields mirror :class:`RatePlanSummary` so consumers can share the
    picker widgets across both shapes.
    """

    rate_plan_id: str
    channel_entity_id: str
    property_channel_id: str
    property_pms_id: str
    name: str
    title: str
    currency: str
    rate_mode: str
    is_active: bool
    calendar: tuple[CalendarDay, ...] = field(default_factory=tuple)
    occupancy_options: tuple[OccupancyOption, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    """One review row scoped to a single property."""

    review_id: str
    property_channel_id: str
    reservation_channel_id: str
    ota_reservation_id: str
    created_at: datetime | None
    review_date: datetime | None
    guest_name: str
    content: str
    public_review: str
    comment: str
    response: str
    review_type: str
    overall_rating: float | None
    ota: str
    source: str
    is_hidden: bool
    is_replied: bool
    is_expired: bool


# ---------------------------------------------------------------------------
# Base class — shared scope + error translation
# ---------------------------------------------------------------------------


class _BaseUnifiedReader:
    """Common scope handling and error wrapping for every reader.

    Args:
        client: Pre-configured :class:`UnifiedDataGraphQLClient`;
            ownership stays with the construction site — readers
            never close the client.
        cendra_customer_id: Cendra workspace identifier (required by
            every list query).
        cendra_org_id: Optional organisation id narrowing the
            workspace down to a tenant org.
        provider_type: Optional :class:`ProviderType` enum value
            (``HOSTAWAY``, ``GUESTY`` …) restricting results to a
            single PMS provider.
    """

    name: str = "unified_reader"

    def __init__(
        self,
        client: UnifiedDataGraphQLClient,
        *,
        cendra_customer_id: str,
        cendra_org_id: str | None = None,
        provider_type: str | None = None,
    ) -> None:
        if not cendra_customer_id:
            raise ValueError("cendra_customer_id is required")
        self._client = client
        self._customer_id = cendra_customer_id
        self._org_id = cendra_org_id or None
        self._provider_type = provider_type or None
        self._log = logger.bind(reader=self.name)

    # -- helpers --------------------------------------------------------

    def _effective_tenant(self) -> tuple[str, str | None, str | None]:
        """Return ``(customer_id, org_id, provider_type)`` for this call.

        Phase 3 resolution order:

        * Active :func:`current_tenant` context (set by
          :class:`TenantResolverMiddleware`) is authoritative when
          present — including ``org_id=None``, which is the
          documented "drop the optional GraphQL filter" signal
          (see ``project_phase3_tenant_registry_spec.md``).
        * Constructor-baked values are used only when no middleware
          has bound a tenant to the current request (background
          tasks, CLI scripts, the legacy single-tenant pod path).
        """
        from brain_engine.tenants import current_tenant

        context = current_tenant()
        if context is None:
            return self._customer_id, self._org_id, self._provider_type
        customer_id = context.customer_id or self._customer_id
        provider_type = context.provider_type or self._provider_type
        return customer_id, context.org_id, provider_type

    def _base_scope_variables(self) -> dict[str, Any]:
        """Return the ``customerId``/``orgId``/``providerType`` block."""
        customer_id, org_id, provider_type = self._effective_tenant()
        variables: dict[str, Any] = {"customerId": customer_id}
        if org_id:
            variables["orgId"] = org_id
        if provider_type:
            variables["providerType"] = provider_type
        return variables

    async def _execute(
        self,
        query: str,
        variables: Mapping[str, Any],
        *,
        operation_name: str,
    ) -> Mapping[str, Any]:
        """Run one GraphQL call and translate transport errors."""
        try:
            return await self._client.execute(
                query,
                dict(variables),
                operation_name=operation_name,
            )
        except UnifiedDataError as exc:
            raise UnifiedDataReaderError(
                self.name,
                f"{operation_name} failed: {exc}",
            ) from exc


# ---------------------------------------------------------------------------
# Property reader
# ---------------------------------------------------------------------------


class UnifiedPropertyReader(_BaseUnifiedReader):
    """Read :class:`UnifiedProperty` documents via the onboarding-api."""

    name: Final[str] = "unified_property_reader"

    async def list_summaries(
        self,
        *,
        limit: int = _DEFAULT_LIST_LIMIT,
        skip: int = 0,
    ) -> list[PropertySummary]:
        """Return one page of slim property rows for the picker UI."""
        variables = self._base_scope_variables()
        variables["limit"] = _clamp_limit(limit)
        variables["skip"] = max(0, int(skip))
        payload = await self._execute(
            PROPERTIES_LIST_QUERY,
            variables,
            operation_name="ListProperties",
        )
        rows = _coerce_list(payload.get("properties"))
        return [
            _parse_property_summary(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    async def get_detail(
        self,
        *,
        channel_entity_id: str,
    ) -> PropertyDetail | None:
        """Return the rich static profile for one property.

        Requires an ``org_id`` and a ``provider_type`` because
        :data:`PROPERTY_DETAIL_QUERY` wraps the single
        ``property(..)`` resolver whose signature marks those fields
        as non-null.  Both are resolved via
        :meth:`_effective_tenant` so a middleware-bound
        :class:`TenantContext` can satisfy the requirement even when
        the reader was constructed without them (Phase 3 path).
        """
        if not channel_entity_id:
            raise ValueError("channel_entity_id is required")
        customer_id, org_id, provider_type = self._effective_tenant()
        if not org_id:
            raise ValueError("cendra_org_id is required for get_detail")
        if not provider_type:
            raise ValueError("provider_type is required for get_detail")
        variables: dict[str, Any] = {
            "customerId": customer_id,
            "orgId": org_id,
            "providerType": provider_type,
            "channelEntityId": channel_entity_id,
        }
        payload = await self._execute(
            PROPERTY_DETAIL_QUERY,
            variables,
            operation_name="PropertyDetail",
        )
        document = payload.get("property")
        if not isinstance(document, Mapping):
            return None
        return _parse_property_detail(document)


# ---------------------------------------------------------------------------
# Rate plan reader
# ---------------------------------------------------------------------------


class UnifiedRatePlanReader(_BaseUnifiedReader):
    """Read :class:`UnifiedRatePlan` documents for a given property."""

    name: Final[str] = "unified_rate_plan_reader"

    async def list_for_property(
        self,
        *,
        property_channel_id: str,
        limit: int = _DEFAULT_LIST_LIMIT,
        skip: int = 0,
    ) -> list[RatePlanSummary]:
        """Return rate plans whose ``propertyChannelId`` matches.

        Filtering is client-side because the ``ratePlans`` query does
        not accept a server-side property argument (verified
        2026-04-24).
        """
        variables = self._base_scope_variables()
        variables["limit"] = _clamp_limit(limit)
        variables["skip"] = max(0, int(skip))
        payload = await self._execute(
            RATE_PLANS_LIST_QUERY,
            variables,
            operation_name="RatePlans",
        )
        rows = _coerce_list(payload.get("ratePlans"))
        return [
            _parse_rate_plan_summary(row)
            for row in _filter_by_property(rows, property_channel_id)
        ]

    async def list_with_calendar(
        self,
        *,
        property_channel_id: str,
        date_from: str,
        date_to: str,
        limit: int = _DEFAULT_LIST_LIMIT,
        skip: int = 0,
    ) -> list[RatePlanWithCalendar]:
        """Return rate plans enriched with calendar + occupancy rows.

        Args:
            property_channel_id: ``channelEntityId`` of the property
                the caller wants to narrow down to.  Filtering is
                client-side — same as :meth:`list_for_property`.
            date_from: ISO-8601 ``YYYY-MM-DD`` lower window bound,
                inclusive.  Forwarded verbatim to the GraphQL
                ``calendar(from: to:)`` sub-resolver.
            date_to: ISO-8601 ``YYYY-MM-DD`` upper window bound,
                inclusive.  Window size is validated by the caller
                (the HTTP layer clamps to the project-wide maximum).
            limit: Max rate plans per page; clamped to the reader's
                list limit.
            skip: Row offset.

        Raises:
            ValueError: When ``property_channel_id`` is empty or either
                date bound is missing.
        """
        if not property_channel_id:
            raise ValueError("property_channel_id is required")
        if not date_from or not date_to:
            raise ValueError("date_from and date_to are required")
        variables = self._base_scope_variables()
        variables["limit"] = _clamp_limit(limit)
        variables["skip"] = max(0, int(skip))
        variables["from"] = date_from
        variables["to"] = date_to
        payload = await self._execute(
            RATE_PLANS_WITH_CALENDAR_QUERY,
            variables,
            operation_name="RatePlansWithCalendar",
        )
        rows = _coerce_list(payload.get("ratePlans"))
        return [
            _parse_rate_plan_with_calendar(row)
            for row in _filter_by_property(rows, property_channel_id)
        ]


# ---------------------------------------------------------------------------
# Review reader
# ---------------------------------------------------------------------------


class UnifiedReviewReader(_BaseUnifiedReader):
    """Read :class:`UnifiedReview` documents for a given property."""

    name: Final[str] = "unified_review_reader"

    async def list_for_property(
        self,
        *,
        property_channel_id: str,
        limit: int = _DEFAULT_LIST_LIMIT,
        skip: int = 0,
    ) -> list[ReviewSummary]:
        """Return reviews whose ``propertyChannelId`` matches."""
        variables = self._base_scope_variables()
        variables["limit"] = _clamp_limit(limit)
        variables["skip"] = max(0, int(skip))
        payload = await self._execute(
            REVIEWS_LIST_QUERY,
            variables,
            operation_name="Reviews",
        )
        rows = _coerce_list(payload.get("reviews"))
        return [
            _parse_review_summary(row)
            for row in _filter_by_property(rows, property_channel_id)
        ]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_property_summary(row: Mapping[str, Any]) -> PropertySummary:
    """Turn a :data:`PROPERTIES_LIST_QUERY` row into a summary."""
    data = _as_mapping(row.get("data"))
    return PropertySummary(
        channel_entity_id=_str(row.get("channelEntityId")),
        pms_id=_str(data.get("pmsId") or row.get("pmsId")),
        title=_str(data.get("title") or data.get("name")),
        is_active=_bool(data.get("isActive")),
        city=_str(data.get("city")),
        country=_str(data.get("country")),
        property_type=_str(data.get("propertyType")),
        max_occupancy=_int(data.get("maxOccupancy")),
        bedrooms=_int(data.get("bedrooms")),
        bathrooms=_float(data.get("bathrooms")),
        base_price=_float(data.get("basePrice")),
        base_currency=_str(data.get("baseCurrency")),
        listing_id=_str(data.get("listingId")),
    )


def _parse_property_detail(row: Mapping[str, Any]) -> PropertyDetail:
    """Turn a :data:`PROPERTY_DETAIL_QUERY` row into a detail record."""
    data = _as_mapping(row.get("data"))
    return PropertyDetail(
        channel_entity_id=_str(row.get("channelEntityId")),
        pms_id=_str(data.get("pmsId") or row.get("pmsId")),
        transformed_at=_parse_iso(row.get("transformedAt")),
        title=_str(data.get("title") or data.get("name")),
        is_active=_bool(data.get("isActive")),
        city=_str(data.get("city")),
        country=_str(data.get("country")),
        country_code=_str(data.get("countryCode")),
        zip_code=_str(data.get("zipCode")),
        address=_str(data.get("address")),
        street=_str(data.get("street")),
        latitude=_optional_float(data.get("latitude")),
        longitude=_optional_float(data.get("longitude")),
        time_zone=_str(data.get("timeZone")),
        property_type=_str(data.get("propertyType")),
        bedrooms=_int(data.get("bedrooms")),
        bathrooms=_float(data.get("bathrooms")),
        beds=_int(data.get("beds")),
        max_occupancy=_int(data.get("maxOccupancy")),
        area_square_feet=_optional_float(data.get("areaSquareFeet")),
        base_currency=_str(data.get("baseCurrency")),
        base_price=_float(data.get("basePrice")),
        cleaning_fee=_optional_float(data.get("cleaningFee")),
        pet_fee=_optional_float(data.get("petFee")),
        security_deposit_fee=_optional_float(data.get("securityDepositFee")),
        check_in_time=_str(data.get("checkInTime")),
        check_out_time=_str(data.get("checkOutTime")),
        min_nights=_int(data.get("minNights")),
        max_nights=_int(data.get("maxNights")),
        instant_bookable=_bool(data.get("instantBookable")),
        pets_allowed=_bool(data.get("petsAllowed")),
        has_parking=_bool(data.get("hasParking")),
        has_wifi=_bool(data.get("hasWifi")),
        wifi_network=_str(data.get("wifiNetwork")),
        wifi_password=_str(data.get("wifiPassword")),
        door_code=_str(data.get("doorCode")),
        license_code=_str(data.get("licenseCode")),
        host_name=_str(data.get("hostName")),
        listing_id=_str(data.get("listingId")),
        status=_str(data.get("status")),
        knowledge_percentage=_float(data.get("knowledgePercentage")),
        amenities=_tuple_of_mappings(data.get("amenities")),
        images=_tuple_of_mappings(data.get("images")),
        rooms=_tuple_of_mappings(data.get("rooms")),
        descriptions=_tuple_of_mappings(data.get("descriptions")),
    )


def _parse_rate_plan_summary(row: Mapping[str, Any]) -> RatePlanSummary:
    """Turn a :data:`RATE_PLANS_LIST_QUERY` row into a summary."""
    data = _as_mapping(row.get("data"))
    return RatePlanSummary(
        rate_plan_id=_str(row.get("id") or data.get("pmsId")),
        property_channel_id=_str(data.get("propertyChannelId")),
        property_pms_id=_str(data.get("propertyPmsId")),
        name=_str(data.get("name")),
        title=_str(data.get("title")),
        currency=_str(data.get("currency")),
        sell_mode=_str(data.get("sellMode")),
        rate_mode=_str(data.get("rateMode")),
        meal_type=_str(data.get("mealType")),
        is_active=_bool(data.get("isActive")),
        parent_rate_plan_id=_str(data.get("parentRatePlanId")),
        children_fee=_float(data.get("childrenFee")),
        infant_fee=_float(data.get("infantFee")),
    )


def _parse_rate_plan_with_calendar(
    row: Mapping[str, Any],
) -> RatePlanWithCalendar:
    """Turn a :data:`RATE_PLANS_WITH_CALENDAR_QUERY` row into the model."""
    data = _as_mapping(row.get("data"))
    return RatePlanWithCalendar(
        rate_plan_id=_str(row.get("id")),
        channel_entity_id=_str(row.get("channelEntityId")),
        property_channel_id=_str(data.get("propertyChannelId")),
        property_pms_id=_str(data.get("propertyPmsId")),
        name=_str(data.get("name")),
        title=_str(data.get("title")),
        currency=_str(data.get("currency")),
        rate_mode=_str(data.get("rateMode")),
        is_active=_bool(data.get("isActive")),
        calendar=_parse_calendar(data.get("calendar")),
        occupancy_options=_parse_occupancy_options(data.get("occupancyOptions")),
    )


def _parse_calendar(value: Any) -> tuple[CalendarDay, ...]:
    """Normalise the ``calendar`` sub-selection into a tuple."""
    if not isinstance(value, list):
        return ()
    parsed: list[CalendarDay] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        parsed.append(
            CalendarDay(
                date=_date_only(entry.get("date")),
                note=_str(entry.get("note")),
                stop_sell=_bool(entry.get("stopSell")),
                count_available_units=_int(entry.get("countAvailableUnits")),
                price=_float(entry.get("price")),
            )
        )
    return tuple(parsed)


def _parse_occupancy_options(value: Any) -> tuple[OccupancyOption, ...]:
    """Normalise the ``occupancyOptions`` sub-selection into a tuple."""
    if not isinstance(value, list):
        return ()
    parsed: list[OccupancyOption] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        parsed.append(
            OccupancyOption(
                occupancy=_int(entry.get("occupancy")),
                is_primary=_bool(entry.get("isPrimary")),
                rate=_float(entry.get("rate")),
            )
        )
    return tuple(parsed)


def _parse_review_summary(row: Mapping[str, Any]) -> ReviewSummary:
    """Turn a :data:`REVIEWS_LIST_QUERY` row into a summary."""
    data = _as_mapping(row.get("data"))
    return ReviewSummary(
        review_id=_str(row.get("id") or data.get("pmsId")),
        property_channel_id=_str(data.get("propertyChannelId")),
        reservation_channel_id=_str(data.get("reservationChannelId")),
        ota_reservation_id=_str(data.get("otaReservationId")),
        created_at=_parse_iso(data.get("createdAt")),
        review_date=_parse_iso(data.get("reviewDate")),
        guest_name=_str(data.get("guestName")),
        content=_str(data.get("content")),
        public_review=_str(data.get("publicReview")),
        comment=_str(data.get("comment")),
        response=_str(data.get("response")),
        review_type=_str(data.get("type")),
        overall_rating=_optional_float(data.get("overallRating")),
        ota=_str(data.get("ota")),
        source=_str(data.get("source")),
        is_hidden=_bool(data.get("isHidden")),
        is_replied=_bool(data.get("isReplied")),
        is_expired=_bool(data.get("isExpired")),
    )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _coerce_list(value: Any) -> list[Any]:
    """Return ``value`` as a list or an empty list when absent."""
    if isinstance(value, list):
        return value
    return []


def _filter_by_property(
    rows: Iterable[Any],
    property_channel_id: str,
) -> Iterable[Mapping[str, Any]]:
    """Yield rows whose property identifier fields match."""
    if not property_channel_id:
        return (row for row in rows if isinstance(row, Mapping))
    return (
        row
        for row in rows
        if isinstance(row, Mapping)
        and _row_matches_property(row, property_channel_id)
    )


def _row_matches_property(
    row: Mapping[str, Any],
    property_channel_id: str,
) -> bool:
    """Match ``property_channel_id`` against the usual candidates."""
    data = row.get("data")
    candidates: list[Any] = [row.get("channelEntityId")]
    if isinstance(data, Mapping):
        candidates.append(data.get("propertyChannelId"))
        candidates.append(data.get("propertyPmsId"))
    return any(str(c) == property_channel_id for c in candidates if c)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, otherwise an empty dict."""
    return value if isinstance(value, Mapping) else {}


def _tuple_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Normalise an optional embedded list into a tuple of dicts."""
    if not isinstance(value, list):
        return ()
    return tuple(dict(v) for v in value if isinstance(v, Mapping))


def _clamp_limit(value: int) -> int:
    """Clamp ``value`` into ``[1, _MAX_LIST_LIMIT]``."""
    if value < 1:
        return 1
    if value > _MAX_LIST_LIMIT:
        return _MAX_LIST_LIMIT
    return int(value)


def _str(value: Any) -> str:
    """Return ``str(value)`` or an empty string when absent."""
    return "" if value is None else str(value)


def _date_only(value: Any) -> str:
    """Return the ``YYYY-MM-DD`` prefix of an ISO-8601 date/datetime.

    The onboarding-api GraphQL resolver serialises ``calendar.date`` as
    a full ISO-8601 ``DateTime`` (e.g. ``2026-04-24T00:00:00.000Z``) even
    though the underlying model is calendar-day granularity.  Callers
    downstream expect the plain ``YYYY-MM-DD`` form, so we trim the time
    portion deterministically instead of round-tripping through
    ``datetime`` (which would discard the original timezone context).
    """
    raw = _str(value)
    if not raw:
        return ""
    return raw.split("T", 1)[0]


def _int(value: Any) -> int:
    """Coerce ``value`` into an ``int``; default to zero when missing."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    """Coerce ``value`` into a ``float``; default to zero when missing."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    """Coerce ``value`` into ``float`` or ``None`` when absent."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    """Coerce ``value`` into ``bool``; default to ``False`` when absent."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 datetime; return ``None`` for unparseable input."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
