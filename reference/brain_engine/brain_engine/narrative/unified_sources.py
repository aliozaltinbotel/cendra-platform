"""Timeline sources backed by the onboarding-api unified GraphQL layer.

This module ships :class:`UnifiedReservationsTimelineSource`, a
:class:`~brain_engine.narrative.sources.TimelineSource` adapter that
turns ``UnifiedReservation`` documents into :class:`TimelineEvent`
items so the timeline composer can blend Cendra's cross-provider
reservation history alongside the in-house stores.

Cendra's GraphQL ``customerId`` denotes a *workspace* (tenant), not a
guest or owner.  It is therefore taken as a constructor argument and
deliberately not derived from the per-fetch ``customer_id`` parameter
which Brain Engine treats as an owner identifier.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Final

import structlog

from brain_engine.integrations.unified_data import (
    RESERVATIONS_LIST_QUERY,
    UnifiedDataError,
    UnifiedDataGraphQLClient,
)
from brain_engine.narrative.errors import TimelineSourceError
from brain_engine.narrative.models import EventKind, TimelineEvent, TimelineRange

__all__ = ["UnifiedReservationsTimelineSource"]


logger = structlog.get_logger(__name__)

_DEFAULT_PAGE_SIZE: Final[int] = 200
_MAX_PAGE_SIZE: Final[int] = 1000


class UnifiedReservationsTimelineSource:
    """Adapter over the unified GraphQL ``reservations`` query.

    The ``reservations`` field accepts an optional ``propertyChannelId``
    argument that narrows results to a single property on the server
    side.  The adapter forwards the caller's ``property_id`` into that
    variable whenever it is non-empty.  A client-side match against
    ``data.propertyChannelId`` / ``data.propertyPmsId`` / the top-level
    ``channelEntityId`` is kept as a defensive guard — it lets the
    adapter serve callers that know the property by a different id
    (e.g. PMS id rather than channel id) and protects against any
    upstream regression that loosens the server-side filter.

    Args:
        client: Pre-configured :class:`UnifiedDataGraphQLClient`.  The
            adapter never closes the client — lifetime ownership stays
            with the construction site.
        cendra_customer_id: Cendra workspace identifier.  Required by
            the GraphQL schema.
        cendra_org_id: Optional organisation filter narrowing the
            workspace down to one tenant org.
        provider_type: Optional :class:`ProviderType` enum value
            (``HOSTAWAY``, ``GUESTY``, …) restricting results to a
            single PMS provider.
        page_size: Per-request ``limit`` passed to GraphQL.  Clamped
            into ``[1, _MAX_PAGE_SIZE]``.
    """

    name: Final[str] = "unified_reservations"

    def __init__(
        self,
        client: UnifiedDataGraphQLClient,
        *,
        cendra_customer_id: str,
        cendra_org_id: str | None = None,
        provider_type: str | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> None:
        if not cendra_customer_id:
            raise ValueError("cendra_customer_id is required")
        if page_size < 1:
            page_size = 1
        if page_size > _MAX_PAGE_SIZE:
            page_size = _MAX_PAGE_SIZE
        self._client = client
        self._customer_id = cendra_customer_id
        self._org_id = cendra_org_id or None
        self._provider_type = provider_type or None
        self._page_size = int(page_size)

    async def fetch(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        limit: int = 200,
    ) -> list[TimelineEvent]:
        del customer_id, guest_id  # not used by this adapter
        page_limit = min(int(limit), self._page_size) if limit else self._page_size
        variables: dict[str, Any] = {
            "customerId": self._customer_id,
            "limit": page_limit,
        }
        if self._org_id:
            variables["orgId"] = self._org_id
        if self._provider_type:
            variables["providerType"] = self._provider_type
        if property_id:
            variables["propertyChannelId"] = property_id

        try:
            data = await self._client.execute(
                RESERVATIONS_LIST_QUERY,
                variables,
                operation_name="Reservations",
            )
        except UnifiedDataError as exc:
            raise TimelineSourceError(
                self.name,
                "GraphQL reservations query failed",
                property_id=property_id,
            ) from exc

        documents = data.get("reservations") or []
        return list(
            _documents_to_events(
                documents,
                property_id=property_id,
                reservation_id=reservation_id,
                source_name=self.name,
                range=range,
            )
        )


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


_CANCELLED_STATUSES: Final[frozenset[str]] = frozenset(
    {"cancelled", "canceled", "cancellation", "cnl"}
)


def _documents_to_events(
    documents: Iterable[Any],
    *,
    property_id: str,
    reservation_id: str | None,
    source_name: str,
    range: TimelineRange,
) -> Iterable[TimelineEvent]:
    """Yield timeline events for documents matching the requested scope.

    Out-of-range items are still yielded — the composer is the single
    place that clips against ``range`` so dedupe and ordering work
    against the full set first.  ``range`` is therefore unused here
    but kept in the signature for forward compatibility.
    """
    del range  # composer-side clipping
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        payload = doc.get("data")
        if not isinstance(payload, dict):
            continue
        if property_id and not _matches_property(doc, payload, property_id):
            continue
        if reservation_id and not _matches_reservation(doc, payload, reservation_id):
            continue
        when = _parse_iso(payload.get("createdAt"))
        if when is None:
            continue
        yield TimelineEvent(
            occurred_at=when,
            kind=EventKind.BOOKING,
            summary=_summary(payload),
            source=source_name,
            native_id=str(doc.get("id") or payload.get("pmsId") or ""),
            property_id=str(
                payload.get("propertyChannelId")
                or payload.get("propertyPmsId")
                or doc.get("channelEntityId")
                or ""
            ),
            details={
                "status": payload.get("status"),
                "arrival_date": payload.get("arrivalDate"),
                "departure_date": payload.get("departureDate"),
                "cancellation_date": payload.get("cancellationDate"),
                "nights_count": payload.get("nightsCount"),
                "guests_count": payload.get("guestsCount"),
                "amount": payload.get("amount"),
                "currency": payload.get("currency"),
                "ota_name": payload.get("otaName"),
                "ota_reservation_code": payload.get("otaReservationCode"),
                "confirmation_code": payload.get("confirmationCode"),
                "channel_booking_id": payload.get("channelBookingId"),
                "guest_name": _guest_name(payload),
                "provider_type": doc.get("providerType"),
                "channel_entity_id": doc.get("channelEntityId"),
                "transformed_at": doc.get("transformedAt"),
            },
        )


def _matches_property(doc: Any, payload: Any, property_id: str) -> bool:
    candidates = (
        payload.get("propertyChannelId"),
        payload.get("propertyPmsId"),
        doc.get("channelEntityId") if isinstance(doc, dict) else None,
    )
    return any(str(value) == property_id for value in candidates if value)


def _matches_reservation(doc: Any, payload: Any, reservation_id: str) -> bool:
    candidates = (
        doc.get("id") if isinstance(doc, dict) else None,
        doc.get("pmsId") if isinstance(doc, dict) else None,
        payload.get("pmsId"),
        payload.get("channelBookingId"),
        payload.get("otaReservationCode"),
        payload.get("confirmationCode"),
    )
    return any(str(value) == reservation_id for value in candidates if value)


def _summary(payload: Any) -> str:
    status = str(payload.get("status") or "booked").lower()
    if status in _CANCELLED_STATUSES:
        verb = "cancelled"
    else:
        verb = "booked"
    arrival = _format_date(payload.get("arrivalDate"))
    departure = _format_date(payload.get("departureDate"))
    nights = payload.get("nightsCount")
    guest = _guest_name(payload)
    parts: list[str] = []
    if guest:
        parts.append(f"{guest} {verb}")
    else:
        parts.append(verb.capitalize())
    if arrival and departure:
        parts.append(f"{arrival} → {departure}")
    elif arrival:
        parts.append(f"from {arrival}")
    if isinstance(nights, int) and nights > 0:
        parts.append(f"{nights} nights")
    ota = payload.get("otaName")
    if ota:
        parts.append(f"via {ota}")
    return " · ".join(parts)


def _guest_name(payload: Any) -> str:
    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return ""
    name = customer.get("nameSurname") or ""
    return str(name).strip()


def _format_date(value: Any) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return ""
    return parsed.date().isoformat()


def _parse_iso(value: Any) -> datetime | None:
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
