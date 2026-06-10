"""GraphQL-backed :class:`PmsFetcher` adapter.

Replaces the Botel direct-PMS fetcher for the conversation pipeline.
The unified onboarding-api GraphQL layer is the single read source for
reservation data — it already mirrors PMS state into Elasticsearch with
a stable schema, while the direct Botel API is auth-fragile and prone
to per-tenant 401s.

Two responsibilities live here:

1. :class:`GraphqlPmsFetcher` implements the
   :class:`~brain_engine.patterns.pms_fetcher.PmsFetcher` protocol so
   :class:`~brain_engine.conversation.service.ConversationService`
   can build feature dicts for pattern-rule consultation.
2. :func:`fetch_reservation_context` resolves a single reservation by
   id and projects it to the structured
   :class:`~brain_engine.conversation.models.ReservationContext` used
   to ground the system prompt.

The fetcher is *request-scoped* — it carries the ``customer_id`` /
``org_id`` / ``property_channel_id`` of the active turn so the GraphQL
``reservations`` query can be narrowed before client-side filtering
by id.  Construct a fresh instance per turn; never share across
requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import structlog

from brain_engine.conversation.models import (
    CalendarDay,
    ReservationContext,
)
from brain_engine.integrations.unified_data.client import (
    UnifiedDataGraphQLClient,
)
from brain_engine.integrations.unified_data.errors import UnifiedDataError
from brain_engine.integrations.unified_data.queries import (
    RATE_PLANS_WITH_CALENDAR_QUERY,
    RESERVATIONS_LIST_QUERY,
)

__all__ = [
    "GraphqlPmsFetcher",
    "fetch_calendar_window",
    "fetch_reservation_context",
]


logger = structlog.get_logger(__name__)

_DEFAULT_LIST_LIMIT: Final[int] = 200


@dataclass(frozen=True)
class _ReservationDoc:
    """Internal projection of one unified reservation row.

    Holds the candidate identifiers (``id`` / ``channelEntityId`` /
    ``pmsId``) so :meth:`GraphqlPmsFetcher.get_reservation` can match a
    caller-supplied identifier against any of them — different channels
    surface the same booking under different keys.
    """

    candidate_ids: tuple[str, ...]
    payload: dict[str, Any]


class GraphqlPmsFetcher:
    """Read-only :class:`PmsFetcher` backed by onboarding-api GraphQL.

    The fetcher does not hit the PMS directly — it queries the unified
    read layer that already pulls reservation state into ES.  All
    failures (transport / GraphQL errors) collapse to ``None`` so the
    conversation pipeline keeps the protocol's "skip rule consultation
    on missing data" semantics; the failure is logged once at warning.

    Attributes:
        _client: The shared GraphQL client (lifespan-scoped).
        _customer_id: Tenant id used to scope the ``reservations``
            query — required by the upstream schema.
        _org_id: Optional org filter; empty string disables the filter.
        _property_channel_id: Optional property filter narrowing the
            reservation list before client-side id matching.
    """

    def __init__(
        self,
        *,
        client: UnifiedDataGraphQLClient,
        customer_id: str,
        org_id: str = "",
        property_channel_id: str = "",
    ) -> None:
        if not customer_id:
            raise ValueError("customer_id is required")
        self._client = client
        self._customer_id = customer_id
        self._org_id = org_id
        self._property_channel_id = property_channel_id

    async def get_reservation(
        self,
        reservation_id: str,
    ) -> dict[str, Any] | None:
        """Return the raw reservation dict, or ``None`` if unknown.

        Implements
        :meth:`brain_engine.patterns.pms_fetcher.PmsFetcher.get_reservation`.
        Snake-case keys match what
        :meth:`brain_engine.patterns.feature_builder.FeatureBuilder.build`
        consumes — ``check_in``, ``check_out``, ``adults``,
        ``children``, ``total_price``, ``source``.
        """
        if not reservation_id:
            return None
        doc = await self._lookup(reservation_id)
        if doc is None:
            return None
        return to_feature_dict(doc.payload)

    async def get_calendar(
        self,
        property_id: str,
        check_in: str,
        check_out: str,
    ) -> dict[str, Any] | None:
        """Return per-day availability for ``[check_in, check_out)``.

        Reads ``unified_rateplans.calendar`` through the onboarding-api
        GraphQL layer — the same source the channel manager mirrors
        into Elasticsearch.  Each row is normalised into a flat day
        record with a derived ``status`` (``"available"`` /
        ``"blocked"`` / ``"unknown"``) so downstream feature builders
        and the prompt renderer never have to reinterpret
        ``stopSell`` / ``countAvailableUnits``.

        Args:
            property_id: ``propertyChannelId`` to filter rate plans by.
                Empty string falls back to the fetcher-scoped property.
            check_in: Inclusive ISO start (``YYYY-MM-DD``).
            check_out: Exclusive ISO end (``YYYY-MM-DD``).

        Returns:
            Dict with a ``"dates"`` list of day records, or ``None``
            when the window is empty or the GraphQL call fails.
        """
        days = await self._fetch_calendar_days(
            property_channel_id=property_id or self._property_channel_id,
            from_iso=check_in,
            to_iso=check_out,
        )
        if not days:
            return None
        return {
            "dates": [day.model_dump() for day in days],
        }

    async def fetch_calendar_days(
        self,
        *,
        property_channel_id: str,
        from_iso: str,
        to_iso: str,
    ) -> list[CalendarDay]:
        """Public wrapper returning typed :class:`CalendarDay` rows.

        The conversation pipeline prefers the structured form over the
        legacy ``get_calendar`` dict so the prompt block can render
        each row without re-parsing strings.  Returns an empty list
        when no rate plan covers the property or the GraphQL layer is
        unreachable.
        """
        return await self._fetch_calendar_days(
            property_channel_id=property_channel_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )

    async def _fetch_calendar_days(
        self,
        *,
        property_channel_id: str,
        from_iso: str,
        to_iso: str,
    ) -> list[CalendarDay]:
        """Pull rate plans + calendar and project to :class:`CalendarDay`.

        The unified schema does not expose a top-level
        ``propertyChannelId`` filter on ``ratePlans``; we fetch the
        tenant's plans and filter by ``data.propertyChannelId`` on the
        client side.  When multiple plans cover the property, days are
        merged with a "blocked wins" rule — if any plan reports the
        day as blocked, the merged status is ``"blocked"`` so the
        prompt never advertises a partially blocked night as free.
        """
        if not (from_iso and to_iso):
            return []
        from_dt = _to_iso_datetime(from_iso)
        to_dt = _to_iso_datetime(to_iso)
        if not (from_dt and to_dt):
            return []

        variables: dict[str, Any] = {
            "customerId": self._customer_id,
            "limit": _DEFAULT_LIST_LIMIT,
            "skip": 0,
            "from": from_dt,
            "to": to_dt,
        }
        if self._org_id:
            variables["orgId"] = self._org_id

        try:
            data = await self._client.execute(
                RATE_PLANS_WITH_CALENDAR_QUERY,
                variables,
                operation_name="RatePlansWithCalendar",
            )
        except UnifiedDataError as exc:
            logger.warning(
                "graphql_calendar_lookup_failed",
                property_channel_id=property_channel_id,
                from_iso=from_iso,
                to_iso=to_iso,
                error=str(exc),
            )
            return []

        rows = data.get("ratePlans") or []
        if not isinstance(rows, list):
            return []

        target = str(property_channel_id or "")
        merged: dict[str, CalendarDay] = {}
        skipped_inactive = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") or {}
            if not isinstance(payload, dict):
                continue
            # The unified schema does not always populate
            # ``data.propertyChannelId``; matching also against the
            # plan's top-level ``channelEntityId`` and the PMS-side
            # ids guards against tenants where the channel's short id
            # is the only available key (V1 demo seed `323133`).
            candidate_ids = {
                str(payload.get("propertyChannelId") or ""),
                str(payload.get("propertyPmsId") or ""),
                str(payload.get("pmsId") or ""),
                str(row.get("channelEntityId") or ""),
                str(row.get("pmsId") or ""),
                str(row.get("customerChannelId") or ""),
            }
            candidate_ids.discard("")
            # When the caller scopes by property, drop foreign rate
            # plans up front; otherwise accept every plan the tenant
            # has so callers can pass an empty property id and still
            # see calendar coverage.
            if target and candidate_ids and target not in candidate_ids:
                continue
            # Inactive rate plans cannot actually sell, so quoting
            # their availability would be fiction.  Skip them — if
            # every plan on the property is inactive the prompt block
            # ends up empty and the LLM is forced to defer.
            if payload.get("isActive") is False:
                skipped_inactive += 1
                continue
            currency = str(payload.get("currency") or "")
            calendar = payload.get("calendar") or []
            if not isinstance(calendar, list):
                continue
            for entry in calendar:
                if not isinstance(entry, dict):
                    continue
                day = _calendar_entry_to_day(entry, currency=currency)
                if not day.date:
                    continue
                existing = merged.get(day.date)
                merged[day.date] = (
                    _merge_calendar_days(existing, day)
                    if existing is not None
                    else day
                )

        result = [merged[key] for key in sorted(merged)]
        # Diagnostic dump — keeps the per-day projection alongside the
        # final merged status so a "blocked in ES, available in chat"
        # divergence can be traced from the pod log without rerunning
        # the GraphQL query manually.
        logger.info(
            "graphql_calendar_window_resolved property=%s from=%s to=%s "
            "rate_plans=%d skipped_inactive=%d days=%d sample=%s",
            target,
            from_iso,
            to_iso,
            len(rows),
            skipped_inactive,
            len(result),
            [
                {
                    "date": day.date,
                    "status": day.status,
                    "units": day.available_units,
                    "stop_sell": day.stop_sell,
                    "note": day.note,
                }
                for day in result[:10]
            ],
        )
        return result

    async def get_reservation_context(
        self,
        reservation_id: str,
    ) -> ReservationContext | None:
        """Return a :class:`ReservationContext` for prompt grounding.

        Distinct from :meth:`get_reservation` (which targets pattern
        feature building) so the prompt context can keep richer fields
        (status, currency, guest name) without bleeding back into the
        feature dict.  Returns ``None`` when the reservation is not
        found or the GraphQL layer is unreachable.
        """
        if not reservation_id:
            return None
        doc = await self._lookup(reservation_id)
        if doc is None:
            return None
        return _to_reservation_context(doc.payload)

    async def _lookup(
        self,
        reservation_id: str,
    ) -> _ReservationDoc | None:
        """Pull the reservation list and find the requested id."""
        variables: dict[str, Any] = {
            "customerId": self._customer_id,
            "limit": _DEFAULT_LIST_LIMIT,
            "skip": 0,
        }
        if self._org_id:
            variables["orgId"] = self._org_id
        if self._property_channel_id:
            variables["propertyChannelId"] = self._property_channel_id

        try:
            data = await self._client.execute(
                RESERVATIONS_LIST_QUERY,
                variables,
                operation_name="Reservations",
            )
        except UnifiedDataError as exc:
            logger.warning(
                "graphql_reservation_lookup_failed",
                reservation_id=reservation_id,
                error=str(exc),
            )
            return None

        rows = data.get("reservations") or []
        if not isinstance(rows, list):
            return None

        target = str(reservation_id)
        for row in rows:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") or {}
            if not isinstance(payload, dict):
                payload = {}
            ids = (
                str(row.get("id") or ""),
                str(row.get("channelEntityId") or ""),
                str(row.get("pmsId") or ""),
                str(row.get("customerChannelId") or ""),
                str(payload.get("pmsId") or ""),
            )
            if target in ids:
                return _ReservationDoc(
                    candidate_ids=tuple(filter(None, ids)),
                    payload=payload,
                )
        return None


async def fetch_calendar_window(
    *,
    client: UnifiedDataGraphQLClient,
    customer_id: str,
    org_id: str,
    property_channel_id: str,
    from_iso: str,
    to_iso: str,
) -> list[CalendarDay]:
    """Resolve a calendar window without instantiating the full fetcher.

    Used by the AG-UI handler to attach an availability snapshot to
    every turn so the conversation pipeline can render a strict
    ``[CALENDAR AVAILABILITY]`` block.  Returns an empty list when any
    prerequisite is missing or the GraphQL call fails — the caller is
    expected to treat that as "unknown" and force a deferral.
    """
    if not (customer_id and from_iso and to_iso):
        return []
    fetcher = GraphqlPmsFetcher(
        client=client,
        customer_id=customer_id,
        org_id=org_id,
        property_channel_id=property_channel_id,
    )
    return await fetcher.fetch_calendar_days(
        property_channel_id=property_channel_id,
        from_iso=from_iso,
        to_iso=to_iso,
    )


async def fetch_reservation_context(
    *,
    client: UnifiedDataGraphQLClient,
    customer_id: str,
    org_id: str,
    property_channel_id: str,
    reservation_id: str,
) -> ReservationContext | None:
    """One-shot helper for callers that don't need the full fetcher.

    Used by the AG-UI handler to resolve a reservation snapshot when
    the client did not ship one in ``state.reservation_context``.  Wraps
    :class:`GraphqlPmsFetcher` so callers stay free of the protocol.

    Returns ``None`` when any prerequisite is missing or the lookup
    fails — the caller is expected to fall through to the no-snapshot
    prompt branch (which still pins the model against fabrication).
    """
    if not (customer_id and reservation_id):
        return None
    fetcher = GraphqlPmsFetcher(
        client=client,
        customer_id=customer_id,
        org_id=org_id,
        property_channel_id=property_channel_id,
    )
    return await fetcher.get_reservation_context(reservation_id)


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def to_feature_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a unified reservation payload to the feature-builder shape.

    Maps GraphQL camelCase (``arrivalDate``, ``guestsCount``) to the
    snake_case keys :class:`FeatureBuilder` reads, mirroring the prior
    Botel adapter's contract so the rule runtime is unchanged by the
    migration.

    Public so the historical-replay extractor can reuse the same
    mapping when it enriches a :class:`DecisionCase` snapshot from a
    raw ES ``data`` payload.  An empty / non-dict input yields an
    empty dict — callers can safely pass through ``None``.
    """
    if not isinstance(payload, dict) or not payload:
        return {}
    customer = payload.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    nights = payload.get("nightsCount")
    return {
        "check_in": str(payload.get("arrivalDate") or "")[:10],
        "check_out": str(payload.get("departureDate") or "")[:10],
        "adults": _safe_int(payload.get("guestsCount")),
        "children": 0,  # unified row carries a combined guest count
        "nights": _safe_int(nights),
        "total_price": _safe_float(payload.get("amount")),
        "currency": str(payload.get("currency") or ""),
        "status": str(payload.get("status") or ""),
        "source": str(payload.get("otaName") or "manual").lower(),
        "guest_name": str(customer.get("nameSurname") or ""),
        "guest_email": str(customer.get("email") or ""),
        "guest_phone": str(customer.get("phone") or ""),
    }


def _to_reservation_context(payload: dict[str, Any]) -> ReservationContext:
    """Project a unified reservation payload to a prompt snapshot.

    Surfaces the fields the system prompt's ``[RESERVATION FACTS]``
    block lists.  Missing keys are left as their dataclass defaults so
    the renderer can omit them rather than print empty placeholders.
    """
    customer = payload.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    arrival = str(payload.get("arrivalDate") or "")
    departure = str(payload.get("departureDate") or "")
    return ReservationContext(
        status=str(payload.get("status") or ""),
        check_in=arrival[:10],
        check_out=departure[:10],
        check_in_time=_extract_time(arrival),
        check_out_time=_extract_time(departure),
        guest_name=str(customer.get("nameSurname") or ""),
        num_guests=_safe_int(payload.get("guestsCount")),
        property_name="",  # not on reservation row; resolve elsewhere
        booking_channel=str(payload.get("otaName") or ""),
        total_price=str(payload.get("amount") or ""),
        currency=str(payload.get("currency") or ""),
    )


def _extract_time(iso_value: str) -> str:
    """Return the ``HH:MM`` slice of an ISO 8601 timestamp."""
    if "T" not in iso_value:
        return ""
    tail = iso_value.split("T", 1)[1]
    return tail[:5] if len(tail) >= 5 else ""


def _to_iso_datetime(value: str) -> str:
    """Coerce a date-or-datetime string into a ``DateTime!`` literal.

    The upstream schema declares ``calendar(from, to)`` as ``DateTime!``
    and rejects bare ``YYYY-MM-DD`` values.  Bare dates are widened to
    ``YYYY-MM-DDT00:00:00Z`` so callers can keep the natural
    ``check_in`` / ``check_out`` shape the rest of the pipeline uses.
    """
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "T" in text:
        return text
    return f"{text}T00:00:00Z"


def _calendar_entry_to_day(
    entry: dict[str, Any],
    *,
    currency: str,
) -> CalendarDay:
    """Project one ``calendar`` row to :class:`CalendarDay`.

    Status logic — every documented block signal forces ``"blocked"``
    so the prompt cannot advertise a partially closed night as free:

    * ``stopSell`` — channel-side stop-sell flag.
    * ``closeToArrival`` / ``closeToDeparture`` — restriction flags
      that prevent the night from anchoring a check-in / check-out.
      In the V1 demo flow (single-night extensions) we treat both as
      a hard block because the guest cannot honor the restriction
      mid-stay.
    * ``countAvailableUnits <= 0`` — no inventory.

    Only when **none** of the above fire and ``countAvailableUnits``
    is a positive integer do we report ``"available"``; missing
    inventory falls through to ``"unknown"`` so the LLM is forced to
    defer instead of guessing.
    """
    raw_date = str(entry.get("date") or "")
    iso_date = raw_date[:10]
    stop_sell = bool(entry.get("stopSell"))
    close_to_arrival = bool(entry.get("closeToArrival"))
    close_to_departure = bool(entry.get("closeToDeparture"))
    units_value = entry.get("countAvailableUnits")
    available_units = _safe_int(units_value)
    if stop_sell or close_to_arrival or close_to_departure:
        status = "blocked"
    elif units_value is None:
        # Channel returned no unit count — neither block nor green
        # light; surface as ``unknown`` so the prompt forces a defer.
        status = "unknown"
    elif available_units <= 0:
        status = "blocked"
    else:
        status = "available"
    price = entry.get("price")
    note_parts: list[str] = []
    raw_note = str(entry.get("note") or "").strip()
    if raw_note:
        note_parts.append(raw_note)
    min_stay = _safe_int(entry.get("minStay"))
    max_stay = _safe_int(entry.get("maxStay"))
    if min_stay > 0:
        note_parts.append(f"minStay={min_stay}")
    if max_stay > 0:
        note_parts.append(f"maxStay={max_stay}")
    if close_to_arrival:
        note_parts.append("closeToArrival")
    if close_to_departure:
        note_parts.append("closeToDeparture")
    return CalendarDay(
        date=iso_date,
        status=status,
        available_units=available_units,
        stop_sell=stop_sell,
        price="" if price in (None, "") else str(price),
        currency=currency,
        note="; ".join(note_parts),
    )


def _merge_calendar_days(
    left: CalendarDay,
    right: CalendarDay,
) -> CalendarDay:
    """Combine two day projections sharing the same date.

    Multiple rate plans can describe the same property; the prompt
    must report the *most restrictive* picture so a partially blocked
    night never surfaces as "available".  Stop-sell wins, then
    ``"blocked"`` wins, otherwise the row with more units wins.
    Notes / price / currency take the first non-empty value.
    """
    stop_sell = left.stop_sell or right.stop_sell
    if stop_sell or "blocked" in {left.status, right.status}:
        status = "blocked"
    elif "available" in {left.status, right.status}:
        status = "available"
    else:
        status = "unknown"
    units = max(left.available_units, right.available_units)
    return CalendarDay(
        date=left.date,
        status=status,
        available_units=units,
        stop_sell=stop_sell,
        price=left.price or right.price,
        currency=left.currency or right.currency,
        note=left.note or right.note,
    )


def _safe_int(value: Any) -> int:
    """Coerce ``value`` to ``int`` or return 0 on failure."""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """Coerce ``value`` to ``float`` or return 0.0 on failure."""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
