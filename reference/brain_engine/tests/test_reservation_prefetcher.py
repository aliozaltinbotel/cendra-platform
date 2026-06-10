"""Tests for the Sprint 9 ``ReservationPrefetcher``.

Pin both the per-property GraphQL fan-out shape and the
crash-isolation contract — the prefetcher must NEVER raise into
its caller.  Anything upstream (transport error, malformed
payload, missing field) becomes a soft ``None`` so the
conversation pipeline can fall back to the legacy
``case_builder.build()`` path bit-for-bit.

The mock ``GraphQLClient`` here implements only the surface
:class:`ReservationPrefetcher` actually exercises (``execute``);
anything else means the prefetcher grew an I/O shape worth
re-reviewing or the mock drifted from the real library.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Mock GraphQL client
# ---------------------------------------------------------------------------


class _MockClient:
    """Stub ``UnifiedDataGraphQLClient`` covering only ``execute``."""

    def __init__(
        self,
        *,
        pages: list[list[dict[str, Any]]] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._pages = list(pages or [])
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(dict(variables or {}))
        if self._raise is not None:
            raise self._raise
        if not self._pages:
            return {"reservations": []}
        return {"reservations": self._pages.pop(0)}


# ---------------------------------------------------------------------------
# Deterministic clock for TTL tests
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


def test_prefetcher_rejects_blank_customer_id() -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    with pytest.raises(ValueError):
        ReservationPrefetcher(client=_MockClient(), customer_id="")


def test_prefetcher_rejects_non_positive_ttl() -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    with pytest.raises(ValueError):
        ReservationPrefetcher(
            client=_MockClient(),
            customer_id="cust",
            ttl_seconds=0,
        )


# ---------------------------------------------------------------------------
# Happy-path payload shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_returns_pms_data_shape(clock: _FakeClock) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "id": "internal-1",
                    "channelEntityId": "chan-1",
                    "pmsId": "PMS-1",
                    "data": {
                        "pmsId": "PMS-1",
                        "status": "confirmed",
                        "arrivalDate": "2026-05-12",
                        "departureDate": "2026-05-15",
                        "createdAt": "2026-05-01T10:00:00Z",
                        "amount": 350.0,
                        "currency": "EUR",
                        "otaName": "bookingcom",
                        "guestsCount": 2,
                        "propertyPmsId": "323133",
                        "propertyChannelId": "chan-prop",
                    },
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    out = await prefetch.fetch_pms_payload(
        property_id="chan-prop",
        reservation_id="PMS-1",
    )
    assert out is not None
    assert out["check_in"] == "2026-05-12"
    assert out["check_out"] == "2026-05-15"
    assert out["created_at"] == "2026-05-01T10:00:00Z"
    assert out["adults"] == 2
    assert out["total_price"] == 350.0
    assert out["currency"] == "EUR"
    assert out["source"] == "bookingcom"
    assert out["status"] == "confirmed"
    assert out["reservation_id"] == "PMS-1"


@pytest.mark.asyncio
async def test_fetch_resolves_via_channel_entity_id(clock: _FakeClock) -> None:
    """Index keys cover every plausible identifier the row carries."""
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "id": "internal-1",
                    "channelEntityId": "chan-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    out = await prefetch.fetch_pms_payload(
        property_id="prop",
        reservation_id="chan-1",
    )
    assert out is not None
    assert out["created_at"] == "2026-04-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Cache + TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_does_not_call_graphql_again(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "pmsId": "R-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
        ttl_seconds=600.0,
    )
    await prefetch.fetch_pms_payload(
        property_id="prop", reservation_id="R-1",
    )
    await prefetch.fetch_pms_payload(
        property_id="prop", reservation_id="R-1",
    )
    # One execute call only — second lookup served from cache.
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_cache_refetches_after_ttl_expiry(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "pmsId": "R-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
            [
                {
                    "pmsId": "R-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
        ttl_seconds=600.0,
    )
    await prefetch.fetch_pms_payload(
        property_id="prop", reservation_id="R-1",
    )
    clock.advance(700)  # past TTL
    await prefetch.fetch_pms_payload(
        property_id="prop", reservation_id="R-1",
    )
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_concurrent_first_lookups_collapse_to_single_fetch(
    clock: _FakeClock,
) -> None:
    """Per-property lock coalesces concurrent first-conversations."""
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "pmsId": "R-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    # Three concurrent lookups for the same property — only the
    # first should reach GraphQL; the other two await the lock and
    # then read the cache.
    results = await asyncio.gather(
        prefetch.fetch_pms_payload(
            property_id="prop", reservation_id="R-1",
        ),
        prefetch.fetch_pms_payload(
            property_id="prop", reservation_id="R-1",
        ),
        prefetch.fetch_pms_payload(
            property_id="prop", reservation_id="R-1",
        ),
    )
    assert all(r is not None for r in results)
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Crash isolation — soft fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graphql_failure_returns_none_no_raise(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(raise_exc=RuntimeError("upstream down"))
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    out = await prefetch.fetch_pms_payload(
        property_id="prop",
        reservation_id="R-1",
    )
    assert out is None  # soft fail, no exception escaped


@pytest.mark.asyncio
async def test_unknown_reservation_id_returns_none(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(
        pages=[
            [
                {
                    "pmsId": "R-1",
                    "data": {"createdAt": "2026-04-01T00:00:00Z"},
                },
            ],
        ],
    )
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    out = await prefetch.fetch_pms_payload(
        property_id="prop",
        reservation_id="DOES-NOT-EXIST",
    )
    assert out is None


@pytest.mark.asyncio
async def test_missing_inputs_short_circuit(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )
    client = _MockClient(pages=[[]])
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    assert (
        await prefetch.fetch_pms_payload(
            property_id="",
            reservation_id="R-1",
        )
        is None
    )
    assert (
        await prefetch.fetch_pms_payload(
            property_id="prop",
            reservation_id="",
        )
        is None
    )
    # Neither short-circuit case should have hit GraphQL.
    assert client.calls == []


# ---------------------------------------------------------------------------
# Pagination — multi-page index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_walks_until_short_page(
    clock: _FakeClock,
) -> None:
    from brain_engine.conversation.reservation_prefetcher import (
        _GQL_PAGE_SIZE,
        ReservationPrefetcher,
    )
    full_page = [
        {
            "pmsId": f"R-{i}",
            "data": {"createdAt": "2026-04-01T00:00:00Z"},
        }
        for i in range(_GQL_PAGE_SIZE)
    ]
    short_page = [
        {
            "pmsId": "R-LAST",
            "data": {"createdAt": "2026-04-02T00:00:00Z"},
        },
    ]
    client = _MockClient(pages=[full_page, short_page])
    prefetch = ReservationPrefetcher(
        client=client,
        customer_id="cust",
        clock=clock,
    )
    out = await prefetch.fetch_pms_payload(
        property_id="prop",
        reservation_id="R-LAST",
    )
    assert out is not None
    assert len(client.calls) == 2
    # ``skip`` advanced by page size between calls.
    assert client.calls[0]["skip"] == 0
    assert client.calls[1]["skip"] == _GQL_PAGE_SIZE
