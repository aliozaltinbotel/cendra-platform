"""PMS fetch contract used by the pattern-rule runtime path.

:class:`~brain_engine.patterns.router.PatternRuleRouter` evaluates rule
conditions against a :class:`~brain_engine.patterns.feature_builder.BookingFeatures`
dict.  Those features are computed from raw PMS data (reservation +
calendar).  The conversation pipeline must therefore obtain that data
just-in-time for the active turn — without coupling the pipeline to a
specific PMS implementation.

:class:`PmsFetcher` is that decoupling seam.  Any object exposing the
two coroutines below can be injected into
:class:`~brain_engine.conversation.service.ConversationService` to
enable rule consultation; passing ``None`` keeps the legacy LLM-only
behaviour.

The interface is intentionally minimal — the fetcher returns raw
dicts because :class:`FeatureBuilder.build` consumes raw dicts.  This
keeps every concrete adapter (today: the unified-data GraphQL gateway)
thin and substitutable.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PmsFetcher(Protocol):
    """Read-only PMS accessor for pattern-rule feature construction.

    Both methods must never raise on "not found" — they return ``None``
    so the pipeline can silently skip rule consultation for turns that
    lack a reservation (pre-booking inquiries, broadcast messages).
    Real errors (network failure, auth failure) should be raised so the
    caller can log and fall through to LLM-only behaviour.
    """

    async def get_reservation(
        self,
        reservation_id: str,
    ) -> dict[str, Any] | None:
        """Return the raw reservation dict, or ``None`` if unknown.

        Args:
            reservation_id: Channel-agnostic reservation identifier.

        Returns:
            Dict with at minimum ``check_in``, ``check_out``, ``adults``,
            ``children``, ``total_price`` — the fields
            :meth:`FeatureBuilder.build` reads.  Missing fields default
            to zero/empty in the builder.
        """
        ...

    async def get_calendar(
        self,
        property_id: str,
        check_in: str,
        check_out: str,
    ) -> dict[str, Any] | None:
        """Return calendar availability around the reservation dates.

        Args:
            property_id: Property whose calendar to fetch.
            check_in: ISO date string, reservation start.
            check_out: ISO date string, reservation end.

        Returns:
            Dict accepted by :func:`_extract_occupied_dates`, i.e. either
            ``{"dates": [{"date": "...", "status": "..."}, ...]}`` or a
            flat ``{date: status}`` mapping.  ``None`` when calendar
            data is unavailable — the router still runs but gap /
            occupancy features will be zero.
        """
        ...
