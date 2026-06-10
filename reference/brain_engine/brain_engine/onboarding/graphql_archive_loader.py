"""Historical archive loader backed by the unified GraphQL layer.

Fetches conversations (with their nested messages) from the Cendra
``onboarding-api`` GraphQL endpoint and yields
:class:`ArchivedConversation` records.  This is the canonical archive
loader from 2026-04-28 onward — the Botel PMS REST loader and the
composite split-window loader were retired when Cendra moved every
historical read onto the unified GraphQL gateway.

Design notes:

- The onboarding-api schema does not expose a top-level ``messages``
  query; message documents are nested inside
  :class:`UnifiedConversation.messages`.  The loader therefore paginates
  ``conversations`` and consumes ``data.messages`` from the same
  response — one round-trip per page, no per-reservation fan-out.
- The ``conversations`` query accepts an optional ``propertyChannelId``
  filter.  The loader forwards the caller's ``property_id`` into that
  variable so pagination stays bounded to a single property instead
  of the whole tenant.  A best-effort client-side check against
  ``channelEntityId`` / ``data.propertyChannelId`` / ``data.propertyPmsId``
  remains as a defensive belt-and-suspenders guard against upstream
  regressions or providers that populate a different id field.
- ``since`` / ``until`` are applied client-side against the conversation
  ``createdAt`` / ``lastMessageAt`` timestamp — the GraphQL layer does
  not yet accept a time window filter.
- All infrastructure failures are wrapped in
  :class:`ConversationArchiveError` so the orchestrator never has to
  import GraphQL-specific exception types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Final

import structlog

from brain_engine.integrations.unified_data import (
    CONVERSATIONS_WITH_MESSAGES_QUERY,
    RESERVATIONS_LIST_QUERY,
    UnifiedDataError,
    UnifiedDataGraphQLClient,
)
from brain_engine.onboarding.errors import ConversationArchiveError
from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)

__all__ = ["GraphQLConversationArchiveLoader"]


logger = structlog.get_logger(__name__)


_DEFAULT_CONVERSATION_PAGE_SIZE: Final[int] = 100
_MAX_PAGE_SIZE: Final[int] = 1000
_DEFAULT_LANGUAGE: Final[str] = "en"

# Reservation-index entries hold parsed dates *and* the raw GraphQL
# ``data`` payload so downstream snapshot enrichment can read real ES
# fields (camelCase) directly without re-fetching.  Memory cost is
# bounded by the property-scoped ``Reservations`` page count.
_ReservationEntry = tuple[
    "datetime | None",
    "datetime | None",
    "dict[str, Any]",
]

_GUEST_SENDER_TOKENS: Final[frozenset[str]] = frozenset(
    {"guest", "customer", "client", "traveler", "traveller", "inbound"}
)
_PM_SENDER_TOKENS: Final[frozenset[str]] = frozenset(
    # ``property`` is the canonical PM-side token on the Cendra
    # onboarding-api (UnifiedMessage.sender = "property" for host
    # replies). Without it every PM message would fall through to
    # UNKNOWN and the whole extractor pipeline would silently skip
    # every historical thread.
    {"pm", "host", "owner", "manager", "team", "staff", "outbound", "property"}
)
_SYSTEM_SENDER_TOKENS: Final[frozenset[str]] = frozenset(
    {"system", "bot", "assistant", "ai", "automation"}
)


class GraphQLConversationArchiveLoader:
    """Archive loader backed by the onboarding-api unified GraphQL layer.

    Attributes:
        name: Stable adapter name used in logs and
            :class:`ConversationArchiveError`.

    Args:
        client: Pre-configured :class:`UnifiedDataGraphQLClient`.
            Lifetime ownership stays with the construction site — the
            loader never closes the client.
        cendra_customer_id: Cendra workspace identifier.  Required by
            the GraphQL schema.
        cendra_org_id: Optional organisation filter.  Some queries
            (``rawData``, ``searchRawData``) require it despite what
            introspection reports.
        provider_type: Optional ``ProviderType`` filter (``HOSTAWAY``,
            ``GUESTY``, …) restricting results to a single PMS
            provider.
        conversation_page_size: Per-request ``limit`` for the
            conversations query.  Clamped into ``[1, 1000]``.
    """

    name: Final[str] = "graphql_unified"

    def __init__(
        self,
        client: UnifiedDataGraphQLClient,
        *,
        cendra_customer_id: str,
        cendra_org_id: str | None = None,
        provider_type: str | None = None,
        conversation_page_size: int = _DEFAULT_CONVERSATION_PAGE_SIZE,
    ) -> None:
        if not cendra_customer_id:
            raise ValueError("cendra_customer_id is required")
        self._client = client
        self._customer_id = cendra_customer_id
        self._org_id = cendra_org_id or None
        self._provider_type = provider_type or None
        self._conversation_page_size = _clamp_page_size(conversation_page_size)
        self._log = logger.bind(component=self.name)

    async def load(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int = 500,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> AsyncIterator[ArchivedConversation]:
        """Yield conversations matching ``property_id`` inside the window.

        Args:
            property_id: Brain Engine property identifier; matched
                client-side against ``channelEntityId``,
                ``data.propertyChannelId`` and ``data.propertyPmsId``.
            since: Inclusive lower bound on conversation ``createdAt``.
            until: Exclusive upper bound on conversation ``createdAt``.
            limit: Hard cap on the number of conversations yielded.
            customer_id_override: Optional per-call ``customerId`` for
                the GraphQL gateway, replacing the constructor's
                ``cendra_customer_id`` for this invocation only.  Used
                by the cross-tenant bootstrap endpoint so an operator
                can ingest a property owned by a different Cendra
                workspace without bouncing the pod.  Blank strings are
                treated as "no override" so the default still applies.
            org_id_override: Optional per-call ``orgId`` override.  See
                ``customer_id_override`` for rationale.
            provider_type_override: Optional per-call ``providerType``
                override.  See ``customer_id_override`` for rationale.

        Raises:
            ConversationArchiveError: On any GraphQL transport error,
                schema error or response shape mismatch.
        """
        if limit <= 0:
            return
        since_utc = _ensure_utc(since)
        until_utc = _ensure_utc(until)
        reservation_index = await self._load_reservation_index(
            property_id=property_id,
            customer_id_override=customer_id_override,
            org_id_override=org_id_override,
            provider_type_override=provider_type_override,
        )
        self._log.info(
            "graphql_archive.reservation_index_loaded",
            property_id=property_id,
            index_size=len(reservation_index),
        )
        joined = 0
        emitted = 0
        try:
            async for document in self._iter_conversations(
                property_id=property_id,
                since=since_utc,
                until=until_utc,
                customer_id_override=customer_id_override,
                org_id_override=org_id_override,
                provider_type_override=provider_type_override,
            ):
                if emitted >= limit:
                    return
                conversation = _build_conversation(
                    property_id=property_id,
                    document=document,
                    reservation_index=reservation_index,
                )
                if conversation is None:
                    continue
                if (
                    conversation.arrival_date is not None
                    or conversation.departure_date is not None
                ):
                    joined += 1
                emitted += 1
                yield conversation
        finally:
            self._log.info(
                "graphql_archive.load_complete",
                property_id=property_id,
                emitted=emitted,
                joined_with_dates=joined,
            )

    # ------------------------------------------------------------------
    # Reservation date index
    # ------------------------------------------------------------------

    async def _load_reservation_index(
        self,
        *,
        property_id: str,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> dict[str, _ReservationEntry]:
        """Build a ``{reservation_key: (arrival, departure, payload)}`` index.

        Each reservation document is indexed under every plausible
        identifier the conversation side may carry —
        ``channelEntityId``, top-level ``id``, top-level ``pmsId`` and
        ``data.pmsId`` — so :func:`_build_conversation` can resolve a
        join hit regardless of which field flavour the upstream schema
        emitted on a given conversation row.  The raw ES ``data``
        payload is retained on the index so the historical extractor
        can enrich the per-case PMS snapshot with live reservation
        fields (camelCase, see ``RESERVATIONS_LIST_QUERY``) instead of
        invented snake_case shapes.  Missing dates are kept as
        ``None`` so a partial reservation document still contributes
        the ids it does have.
        """
        index: dict[str, _ReservationEntry] = {}
        skip = 0
        page_size = self._conversation_page_size
        while True:
            page = await self._fetch_reservations(
                property_id=property_id,
                limit=page_size,
                skip=skip,
                customer_id_override=customer_id_override,
                org_id_override=org_id_override,
                provider_type_override=provider_type_override,
            )
            if not page:
                return index
            for document in page:
                if not isinstance(document, dict):
                    continue
                payload = document.get("data")
                arrival = (
                    _parse_iso(payload.get("arrivalDate"))
                    if isinstance(payload, dict)
                    else None
                )
                departure = (
                    _parse_iso(payload.get("departureDate"))
                    if isinstance(payload, dict)
                    else None
                )
                stored_payload = payload if isinstance(payload, dict) else {}
                if (
                    arrival is None
                    and departure is None
                    and not stored_payload
                ):
                    continue
                value: _ReservationEntry = (
                    arrival,
                    departure,
                    stored_payload,
                )
                for key in _reservation_index_keys(document):
                    if key and key not in index:
                        index[key] = value
            if len(page) < page_size:
                return index
            skip += page_size

    async def _fetch_reservations(
        self,
        *,
        property_id: str,
        limit: int,
        skip: int,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run one ``Reservations`` page; wrap transport errors."""
        customer_id, org_id, provider_type = self._resolve_tenant(
            customer_id_override=customer_id_override,
            org_id_override=org_id_override,
            provider_type_override=provider_type_override,
        )
        variables: dict[str, Any] = {
            "customerId": customer_id,
            "limit": limit,
            "skip": skip,
        }
        if org_id:
            variables["orgId"] = org_id
        if provider_type:
            variables["providerType"] = provider_type
        if property_id:
            variables["propertyChannelId"] = property_id
        try:
            data = await self._client.execute(
                RESERVATIONS_LIST_QUERY,
                variables,
                operation_name="Reservations",
            )
        except UnifiedDataError as exc:
            raise ConversationArchiveError(
                self.name,
                "GraphQL reservations query failed",
                property_id=property_id,
            ) from exc
        raw = data.get("reservations") or []
        if not isinstance(raw, list):
            raise ConversationArchiveError(
                self.name,
                "GraphQL reservations payload was not a list",
                property_id=property_id,
            )
        return raw

    # ------------------------------------------------------------------
    # Conversation iteration
    # ------------------------------------------------------------------

    async def _iter_conversations(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Paginate conversations and filter client-side by property+window."""
        skip = 0
        page_size = self._conversation_page_size
        while True:
            page = await self._fetch_conversations(
                property_id=property_id,
                limit=page_size,
                skip=skip,
                customer_id_override=customer_id_override,
                org_id_override=org_id_override,
                provider_type_override=provider_type_override,
            )
            if not page:
                return
            for document in page:
                if not isinstance(document, dict):
                    continue
                if not _matches_property(document, property_id):
                    continue
                if not _matches_window(document, since, until):
                    continue
                yield document
            if len(page) < page_size:
                return
            skip += page_size

    async def _fetch_conversations(
        self,
        *,
        property_id: str,
        limit: int,
        skip: int,
        customer_id_override: str | None = None,
        org_id_override: str | None = None,
        provider_type_override: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run one ``ConversationsWithMessages`` page; wrap transport errors."""
        customer_id, org_id, provider_type = self._resolve_tenant(
            customer_id_override=customer_id_override,
            org_id_override=org_id_override,
            provider_type_override=provider_type_override,
        )
        variables: dict[str, Any] = {
            "customerId": customer_id,
            "limit": limit,
            "skip": skip,
        }
        if org_id:
            variables["orgId"] = org_id
        if provider_type:
            variables["providerType"] = provider_type
        if property_id:
            variables["propertyChannelId"] = property_id
        try:
            data = await self._client.execute(
                CONVERSATIONS_WITH_MESSAGES_QUERY,
                variables,
                operation_name="ConversationsWithMessages",
            )
        except UnifiedDataError as exc:
            raise ConversationArchiveError(
                self.name,
                "GraphQL conversations query failed",
                property_id=property_id,
            ) from exc
        raw = data.get("conversations") or []
        if not isinstance(raw, list):
            raise ConversationArchiveError(
                self.name,
                "GraphQL conversations payload was not a list",
                property_id=property_id,
            )
        return raw

    # ------------------------------------------------------------------
    # Tenant resolution
    # ------------------------------------------------------------------

    def _resolve_tenant(
        self,
        *,
        customer_id_override: str | None,
        org_id_override: str | None,
        provider_type_override: str | None,
    ) -> tuple[str, str | None, str | None]:
        """Return the effective ``(customer_id, org_id, provider_type)``.

        Each ``*_override`` slot uses the same semantics:

        * ``None`` (the default kwarg value) means "no override on
          this call" — the Phase 3 ContextVar resolution applies
          (registry lookup → lazy probe → env default), with the
          constructor-baked value used only when no middleware has
          bound a :class:`TenantContext` to the current request.
        * A non-empty string replaces both the ContextVar and the
          baked value for the duration of the current call only.
        * An empty / whitespace-only string also means "no override"
          for ``customer_id`` (the GraphQL gateway rejects blank
          customer ids), and "explicitly clear" for ``org_id`` /
          ``provider_type`` (those are optional in the schema).

        The asymmetry matches what the cross-tenant bootstrap
        endpoint needs: an operator who wants to run against a
        different workspace must supply a real ``customer_id``, but
        may legitimately want to drop the ``provider_type`` filter
        (e.g. switching from a Hostaway-only pod default to a
        Lodgify property scope).
        """
        from brain_engine.tenants import current_tenant

        context = current_tenant()

        if customer_id_override and customer_id_override.strip():
            customer_id = customer_id_override.strip()
        elif context is not None and context.customer_id:
            customer_id = context.customer_id
        else:
            customer_id = self._customer_id

        if org_id_override is None:
            if context is not None:
                org_id = context.org_id
            else:
                org_id = self._org_id
        else:
            stripped = org_id_override.strip()
            org_id = stripped or None

        if provider_type_override is None:
            if context is not None and context.provider_type:
                provider_type = context.provider_type
            else:
                provider_type = self._provider_type
        else:
            stripped = provider_type_override.strip()
            provider_type = stripped or None

        return customer_id, org_id, provider_type


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _build_conversation(
    *,
    property_id: str,
    document: dict[str, Any],
    reservation_index: dict[str, _ReservationEntry] | None = None,
) -> ArchivedConversation | None:
    """Compose an :class:`ArchivedConversation` from a GraphQL document."""
    conversation_id = _conversation_id(document)
    if not conversation_id:
        return None
    payload = document.get("data") if isinstance(document, dict) else None
    raw_messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(raw_messages, list) or not raw_messages:
        return None
    parsed = tuple(_parse_message(raw) for raw in raw_messages)
    messages = tuple(m for m in parsed if m is not None)
    if not messages:
        return None
    messages = tuple(sorted(messages, key=lambda m: m.sent_at))
    reservation_id = _reservation_id(payload) or conversation_id
    guest_id = ""
    if isinstance(payload, dict):
        # UnifiedConversation exposes only identifier fields for the
        # guest (``cendraGuestId`` / ``guestChannelId``) — no display
        # name.  Prefer the Cendra id so downstream joins can resolve
        # to a full guest record, fall back to the channel id.
        guest_id_value = (
            payload.get("cendraGuestId") or payload.get("guestChannelId")
        )
        if guest_id_value:
            guest_id = str(guest_id_value)
    channel = str(document.get("providerType") or "").lower()
    arrival_date, departure_date, reservation_data = _lookup_reservation(
        reservation_id=reservation_id,
        payload=payload,
        index=reservation_index,
    )
    return ArchivedConversation(
        conversation_id=conversation_id,
        property_id=property_id,
        reservation_id=reservation_id,
        guest_id=guest_id,
        guest_name="",
        owner_id="",
        channel=channel,
        messages=messages,
        started_at=messages[0].sent_at,
        ended_at=messages[-1].sent_at,
        arrival_date=arrival_date,
        departure_date=departure_date,
        reservation_data=reservation_data,
    )


def _reservation_index_keys(document: dict[str, Any]) -> tuple[str, ...]:
    """Return the candidate join keys a reservation document carries.

    A conversation row may reference its reservation through any of
    ``channelEntityId``, top-level ``id``, top-level ``pmsId`` or the
    nested ``data.pmsId``.  Indexing under every flavour keeps the
    loader resilient to upstream-schema drift without forcing the
    join site to know which field "won" on a given row.
    """
    payload = document.get("data")
    raw: list[Any] = [
        document.get("channelEntityId"),
        document.get("id"),
        document.get("pmsId"),
    ]
    if isinstance(payload, dict):
        raw.append(payload.get("pmsId"))
    keys: list[str] = []
    for value in raw:
        if value is None:
            continue
        text = str(value)
        if text:
            keys.append(text)
    return tuple(keys)


def _lookup_reservation(
    *,
    reservation_id: str,
    payload: Any,
    index: dict[str, _ReservationEntry] | None,
) -> tuple[datetime | None, datetime | None, dict[str, Any] | None]:
    """Resolve ``(arrival, departure, raw_payload)`` for a conversation.

    Tries the reservation index first (keyed by every plausible id
    flavour) and falls back to dates embedded directly in the
    conversation payload — some upstream rows carry them inline even
    though the canonical source is the ``reservations`` query.  The
    raw ``data`` payload from the reservations query (camelCase, see
    ``RESERVATIONS_LIST_QUERY``) is forwarded so downstream snapshot
    enrichment can read live fields without re-fetching.
    """
    if index:
        candidates: list[str] = []
        if reservation_id:
            candidates.append(reservation_id)
        if isinstance(payload, dict):
            for field_name in ("reservationChannelId", "reservationPmsId"):
                value = payload.get(field_name)
                if value:
                    candidates.append(str(value))
        for key in candidates:
            hit = index.get(key)
            if hit is not None:
                arrival, departure, stored = hit
                return arrival, departure, stored or None
    if isinstance(payload, dict):
        arrival = _parse_iso(payload.get("arrivalDate"))
        departure = _parse_iso(payload.get("departureDate"))
        if arrival is not None or departure is not None:
            return arrival, departure, None
    return None, None, None


def _clamp_page_size(value: int) -> int:
    """Clamp a user-supplied page size into ``[1, _MAX_PAGE_SIZE]``."""
    if value < 1:
        return 1
    if value > _MAX_PAGE_SIZE:
        return _MAX_PAGE_SIZE
    return int(value)


def _ensure_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC, treating naive inputs as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matches_property(document: dict[str, Any], property_id: str) -> bool:
    """Match ``property_id`` against the candidate identifier fields.

    UnifiedConversation exposes ``propertyChannelId`` only (no PMS id
    mirror).  The wrapper's ``channelEntityId`` is kept as a fallback so
    the same id can be matched regardless of which field the caller
    thinks of as "the" property id.
    """
    if not property_id:
        return True
    payload = document.get("data")
    candidates: list[Any] = [document.get("channelEntityId")]
    if isinstance(payload, dict):
        candidates.append(payload.get("propertyChannelId"))
    return any(str(c) == property_id for c in candidates if c)


def _matches_window(
    document: dict[str, Any],
    since: datetime,
    until: datetime,
) -> bool:
    """Keep the document only when its timestamp falls inside the window."""
    payload = document.get("data")
    created_at = None
    if isinstance(payload, dict):
        created_at = _parse_iso(payload.get("createdAt"))
        if created_at is None:
            created_at = _parse_iso(payload.get("lastMessageAt"))
    if created_at is None:
        created_at = _parse_iso(document.get("transformedAt"))
    if created_at is None:
        return True
    return since <= created_at < until


def _conversation_id(document: dict[str, Any]) -> str:
    """Return the best stable identifier for a conversation document."""
    candidates: list[Any] = [
        document.get("id"),
        document.get("pmsId"),
        document.get("channelEntityId"),
    ]
    payload = document.get("data")
    if isinstance(payload, dict):
        candidates.append(payload.get("reservationChannelId"))
    for value in candidates:
        if value:
            return str(value)
    return ""


def _reservation_id(payload: Any) -> str:
    """Extract a reservation identifier from the conversation ``data`` block."""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("reservationChannelId")
    if value:
        return str(value)
    return ""


def _parse_message(raw: Any) -> ArchivedMessage | None:
    """Map a GraphQL :class:`UnifiedMessage` document into an archive record."""
    if not isinstance(raw, dict):
        return None
    body = raw.get("body")
    if not body:
        return None
    sent_at = _parse_iso(raw.get("createdAt"))
    if sent_at is None:
        sent_at = _parse_iso(raw.get("modifiedAt"))
    if sent_at is None:
        return None
    sender = _classify_sender(raw.get("sender"), raw.get("sendByAI"))
    return ArchivedMessage(
        sender=sender,
        text=str(body),
        sent_at=sent_at,
        language=_DEFAULT_LANGUAGE,
    )


def _classify_sender(raw: Any, send_by_ai: Any = None) -> MessageSender:
    """Map the ``sender`` string + ``sendByAI`` flag to a :class:`MessageSender`.

    ``sender`` is a free-form string on :class:`UnifiedMessage` (not an
    enum).  When the ``sendByAI`` flag is set the message originated
    from the PM-side automation stack and is treated as PM traffic so
    that the resulting ``DecisionCase`` carries the correct polarity.
    """
    if send_by_ai is True:
        return MessageSender.PM
    token = str(raw or "").strip().lower()
    if not token:
        return MessageSender.UNKNOWN
    if token in _GUEST_SENDER_TOKENS:
        return MessageSender.GUEST
    if token in _PM_SENDER_TOKENS:
        return MessageSender.PM
    if token in _SYSTEM_SENDER_TOKENS:
        return MessageSender.SYSTEM
    if "guest" in token or "customer" in token or "inbound" in token:
        return MessageSender.GUEST
    if "host" in token or "manager" in token or "outbound" in token:
        return MessageSender.PM
    if "system" in token or "bot" in token or "ai" in token:
        return MessageSender.SYSTEM
    return MessageSender.UNKNOWN


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string; accept ``datetime`` unchanged."""
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_utc(parsed)
