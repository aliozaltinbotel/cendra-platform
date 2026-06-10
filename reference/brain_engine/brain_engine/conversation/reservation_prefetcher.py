"""Pre-fetch ``reservation.data.createdAt`` for live conversations.

Sprint 9 (forward path) closes the gap that blocked
``lead_time_hours`` from ever being populated on freshly-ingested
``DecisionCase`` rows.  ``conversation/service.py`` historically
called ``case_builder.build()`` without ``pms_data``, so
:func:`FeatureBuilder._compute_lead_time` always read an empty
``created_at`` and returned ``0.0`` — the field never made it
into ``pms_snapshot`` and the synthesiser never saw the axis on
post-Sprint-2 cases.

This module solves the gap with a per-property index of
``{reservation_id: pms_data_dict}`` derived from the unified
GraphQL gateway's ``RESERVATIONS_LIST_QUERY``.  The index is
fetched lazily on the first conversation for a given property
and cached with a configurable TTL.  ``createdAt`` is immutable
so the only reason to refresh is to pick up *new* reservations
that arrived after the last fetch — a 30-minute TTL keeps the
cache fresh enough for the live conversation pipeline without
turning every message into a GraphQL round-trip.

Risk profile (matches the ``BRAIN_LEAD_TIME_FETCH_ENABLED`` flag
gate in ``conversation/service.py``):

* **Flag off** — :class:`ReservationPrefetcher` is never
  instantiated.  The conversation pipeline behaves bit-for-bit
  identically to pre-Sprint-9 (calls ``case_builder.build()``
  without ``pms_data``).  Cannot break anything because no new
  code executes.
* **Flag on, GraphQL healthy** — first conversation per
  property pays the pagination cost (typically <2s for ≤3000
  reservations); subsequent conversations resolve from the
  in-memory cache (<1ms).
* **Flag on, GraphQL down** — :meth:`fetch_pms_payload` swallows
  the exception, logs a warning, and returns ``None``.  The
  caller falls through to the legacy path (no ``pms_data``),
  so the conversation still logs successfully.  Lead-time stays
  at ``0`` for the affected window — degrades to current
  behaviour, never breaks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Final

import structlog

from brain_engine.integrations.unified_data import (
    RESERVATIONS_LIST_QUERY,
    UnifiedDataGraphQLClient,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "ReservationPrefetcher",
]


logger = structlog.get_logger(__name__)


# 30 minutes — long enough to keep most live conversations on a
# warm cache, short enough that new reservations created during
# the day still surface within a single shift.
DEFAULT_TTL_SECONDS: Final[float] = 1800.0

# Pagination knobs — mirror the backfill script
# (``scripts/backfill_temporal_features.py``) so the fetched index
# shape stays identical between the forward path and the historical
# backfill.
_GQL_PAGE_SIZE: Final[int] = 1000
_GQL_MAX_PAGES: Final[int] = 200


class ReservationPrefetcher:
    """Lazy per-property GraphQL index of reservation creation timestamps.

    Args:
        client: Pre-configured :class:`UnifiedDataGraphQLClient`.  The
            prefetcher does not own the client lifecycle — the caller
            (typically the FastAPI lifespan) closes it on shutdown.
        customer_id: Cendra workspace identifier.  Required by every
            unified GraphQL query.
        org_id: Optional org filter.  Forwarded verbatim when set.
        provider_type: Optional PMS provider filter (e.g.
            ``"HOSTAWAY"``).
        ttl_seconds: How long a per-property index entry is reused
            before re-fetching.  Defaults to
            :data:`DEFAULT_TTL_SECONDS` (30 minutes).
        clock: Override for ``time.time``.  Tests inject a deterministic
            clock to verify TTL expiry without ``asyncio.sleep``.
    """

    def __init__(
        self,
        *,
        client: UnifiedDataGraphQLClient,
        customer_id: str,
        org_id: str | None = None,
        provider_type: str | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not customer_id:
            raise ValueError("customer_id is required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._client = client
        self._customer_id = customer_id
        self._org_id = org_id or None
        self._provider_type = provider_type or None
        self._ttl = float(ttl_seconds)
        self._clock = clock
        # ``{property_id: (expires_at_epoch, {reservation_id: pms_data})}``
        self._cache: dict[
            str,
            tuple[float, dict[str, dict[str, Any]]],
        ] = {}
        # Per-property fetch lock so concurrent first-conversations
        # for the same property collapse to a single GraphQL fan-out.
        self._fetch_locks: dict[str, asyncio.Lock] = {}
        self._log = logger.bind(component="reservation_prefetcher")

    async def fetch_pms_payload(
        self,
        *,
        property_id: str,
        reservation_id: str,
    ) -> dict[str, Any] | None:
        """Return the ``pms_data`` payload for one reservation.

        Args:
            property_id: ``propertyChannelId`` filter forwarded to the
                GraphQL query.  Used as the index cache key.
            reservation_id: Identifier matching ``decision_cases``;
                resolved against any of ``data.pmsId`` /
                ``channelEntityId`` / ``customerChannelId`` /
                ``pmsId`` / ``id`` populated by the upstream
                response.

        Returns:
            Dict shaped to feed ``case_builder.build(pms_data=...)``
            (``check_in`` / ``check_out`` / ``created_at`` etc.).
            ``None`` when either input is missing, the GraphQL
            fan-out failed (already logged), or the reservation is
            not in the property's index.
        """
        if not property_id or not reservation_id:
            return None
        index = await self._get_or_build_index(property_id=property_id)
        if index is None:
            return None
        return index.get(str(reservation_id))

    async def _get_or_build_index(
        self,
        *,
        property_id: str,
    ) -> dict[str, dict[str, Any]] | None:
        """Resolve the per-property index, refreshing if expired.

        Returns ``None`` when the GraphQL fan-out raised — caller
        falls through to legacy behaviour without crashing the
        conversation.
        """
        now = self._clock()
        entry = self._cache.get(property_id)
        if entry is not None and entry[0] >= now:
            return entry[1]
        # Coalesce concurrent fetches for the same property — only
        # the first task pays the pagination cost; the rest read
        # the cache once the lock releases.
        lock = self._fetch_locks.setdefault(property_id, asyncio.Lock())
        async with lock:
            entry = self._cache.get(property_id)
            now = self._clock()
            if entry is not None and entry[0] >= now:
                return entry[1]
            try:
                index = await self._fetch_property_index(property_id)
            except Exception:
                self._log.warning(
                    "reservation_prefetch_failed",
                    exc_info=True,
                    property_id=property_id,
                )
                return None
            self._cache[property_id] = (now + self._ttl, index)
            return index

    async def _fetch_property_index(
        self,
        property_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Paginate ``RESERVATIONS_LIST_QUERY`` for one property.

        Stops on the first short page; ``_GQL_MAX_PAGES`` is a hard
        upper bound that only matters if upstream returns full pages
        indefinitely.  Builds the index keyed by every plausible
        reservation identifier so ``decision_cases.reservation_id``
        resolves regardless of which one the live ingestion stored.
        """
        index: dict[str, dict[str, Any]] = {}
        skip = 0
        for _ in range(_GQL_MAX_PAGES):
            variables: dict[str, object] = {
                "customerId": self._customer_id,
                "propertyChannelId": property_id,
                "limit": _GQL_PAGE_SIZE,
                "skip": skip,
            }
            if self._org_id:
                variables["orgId"] = self._org_id
            if self._provider_type:
                variables["providerType"] = self._provider_type
            page = await self._client.execute(
                RESERVATIONS_LIST_QUERY,
                variables,
                operation_name="Reservations",
            )
            rows = page.get("reservations") if isinstance(page, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                payload = _row_to_pms_payload(row)
                if payload is None:
                    continue
                for key in _row_to_index_keys(row):
                    index.setdefault(key, payload)
            if len(rows) < _GQL_PAGE_SIZE:
                break
            skip += _GQL_PAGE_SIZE
        return index


def _row_to_pms_payload(row: object) -> dict[str, Any] | None:
    """Project one ``reservations[]`` entry into ``pms_data`` shape.

    The shape mirrors what
    :meth:`brain_engine.patterns.case_builder.CaseBuilder._build_pms_snapshot`
    consumes (``check_in`` / ``check_out`` / ``adults`` /
    ``total_price`` / ``currency`` / ``source`` / ``status`` /
    ``payment_status`` / ``property_id`` / ``listing_id`` /
    ``reservation_id`` / ``created_at``).  Fields the GraphQL row
    does not carry default to ``None`` so downstream code that
    already tolerates absent keys keeps working.

    Returns ``None`` when the row payload is malformed (missing
    ``data`` block) — the caller skips it.
    """
    if not isinstance(row, dict):
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    return {
        "reservation_id": (
            data.get("pmsId")
            or row.get("pmsId")
            or row.get("channelEntityId")
        ),
        "status": data.get("status"),
        "check_in": data.get("arrivalDate"),
        "check_out": data.get("departureDate"),
        "created_at": data.get("createdAt"),
        "adults": data.get("guestsCount"),
        "children": None,
        "infants": None,
        "total_price": data.get("amount"),
        "currency": data.get("currency"),
        "source": data.get("otaName"),
        "payment_status": None,
        "property_id": data.get("propertyPmsId"),
        "property_name": None,
        "listing_id": data.get("propertyChannelId"),
    }


def _row_to_index_keys(row: object) -> list[str]:
    """Return every plausible identifier the live ingestion may store.

    ``decision_cases.reservation_id`` is set by the upstream caller
    (``conversation/service.py`` reads ``state.request.reservation_id``)
    and historically resolves to the PMS id.  We index every
    candidate so the lookup is robust to provider-specific quirks.
    """
    if not isinstance(row, dict):
        return []
    out: list[str] = []
    for key in ("pmsId", "channelEntityId", "customerChannelId", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    data = row.get("data")
    if isinstance(data, dict):
        inner_pms = data.get("pmsId")
        if isinstance(inner_pms, str) and inner_pms:
            out.append(inner_pms)
    return out
