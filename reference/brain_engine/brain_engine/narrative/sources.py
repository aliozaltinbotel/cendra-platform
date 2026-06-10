"""Timeline source adapters.

Defines the :class:`TimelineSource` protocol and three concrete
adapters that convert the Brain Engine's canonical stores into the
unified :class:`TimelineEvent` shape:

- :class:`CustomerMemoryTimelineSource` wraps the Redis-backed
  ``CustomerMemory.recall_events`` accessor.  It skips itself when the
  caller did not provide a ``customer_id`` so the endpoint stays
  useful without an ownership resolver.
- :class:`DecisionCaseTimelineSource` wraps ``DecisionCaseStore.search``
  and maps each :class:`DecisionCase` into a narrative event.  The
  ``OPS`` stage maps to :class:`EventKind.OPS`; compensation scenarios
  map to :class:`EventKind.COMPLAINT`; everything else is a generic
  :class:`EventKind.DECISION`.
- :class:`GuestHistoryTimelineSource` wraps
  ``GuestHistoryStore.get_property_incidents`` and turns each
  :class:`IncidentRecord` into an :class:`EventKind.INCIDENT` (or
  :class:`EventKind.COMPLAINT` when the incident type says so).

Adapters never leak their native exceptions — everything is re-raised
as :class:`TimelineSourceError` with the original as the cause.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final, Protocol, runtime_checkable

import structlog

from brain_engine.narrative.errors import TimelineSourceError
from brain_engine.narrative.models import EventKind, TimelineEvent, TimelineRange

__all__ = [
    "CustomerMemoryTimelineSource",
    "DecisionCaseTimelineSource",
    "GuestHistoryTimelineSource",
    "TimelineSource",
]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TimelineSource(Protocol):
    """Adapter that turns a backing store into timeline events."""

    name: str

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
        """Return events from this source for the given property window.

        Implementations must:

        - Filter by ``property_id`` upstream where possible.
        - Narrow the result to ``reservation_id`` and/or ``guest_id``
          when either is provided.  Upstream filtering is preferred;
          client-side filtering is an acceptable fallback when the
          backend exposes no native parameter.
        - Return *unbounded-by-time* results that the composer will clip
          against ``range`` — pre-filtering by time is a best-effort
          optimisation, not a correctness guarantee.
        - Raise :class:`TimelineSourceError` (wrapping the underlying
          exception) on failure.
        """
        ...


# ---------------------------------------------------------------------------
# Customer-memory adapter
# ---------------------------------------------------------------------------


_CUSTOMER_EVENT_KIND: Final[dict[str, EventKind]] = {
    "booking_created": EventKind.BOOKING,
    "booking_confirmed": EventKind.BOOKING,
    "booking_modified": EventKind.BOOKING,
    "booking_cancelled": EventKind.BOOKING,
    "checkin": EventKind.BOOKING,
    "checkout": EventKind.BOOKING,
    "incident": EventKind.INCIDENT,
    "incident_resolved": EventKind.INCIDENT,
    "complaint": EventKind.COMPLAINT,
    "compensation": EventKind.COMPLAINT,
    "upsell": EventKind.UPSELL,
    "upsell_accepted": EventKind.UPSELL,
    "cleaner_dispatched": EventKind.OPS,
    "vendor_dispatched": EventKind.OPS,
    "negotiation": EventKind.OPS,
    "pm_override": EventKind.DECISION,
    "decision": EventKind.DECISION,
}


class CustomerMemoryTimelineSource:
    """Adapter over :class:`CustomerMemory`.

    Contributes events only when ``customer_id`` is provided.  Without
    it there is no usable index key, so the adapter returns an empty
    list rather than silently pulling events for another customer.
    """

    name: Final[str] = "customer_memory"

    def __init__(self, customer_memory: Any) -> None:
        self._memory = customer_memory

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
        if not customer_id:
            return []
        try:
            raw = await self._memory.recall_events(
                customer_id,
                property_id=property_id or None,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - wrapped below
            raise TimelineSourceError(
                self.name,
                "recall_events failed",
                property_id=property_id,
                customer_id=customer_id,
            ) from exc

        events: list[TimelineEvent] = []
        for item in raw:
            if reservation_id and str(
                getattr(item, "reservation_id", "")
            ) != reservation_id:
                continue
            when = _parse_iso(getattr(item, "created_at", ""))
            if when is None:
                continue
            kind = _CUSTOMER_EVENT_KIND.get(
                str(getattr(item, "event_type", "")).lower(),
                EventKind.OTHER,
            )
            events.append(
                TimelineEvent(
                    occurred_at=when,
                    kind=kind,
                    summary=str(getattr(item, "summary", "")) or "(no summary)",
                    source=self.name,
                    native_id=str(getattr(item, "event_id", "")),
                    property_id=str(getattr(item, "property_id", "")),
                    property_name=str(getattr(item, "property_name", "")),
                    details={
                        "event_type": getattr(item, "event_type", ""),
                        "outcome": getattr(item, "outcome", ""),
                        "guest_name": getattr(item, "guest_name", ""),
                        "reservation_id": getattr(item, "reservation_id", ""),
                        "revenue_impact": getattr(item, "revenue_impact", 0.0),
                    },
                )
            )
        return events


# ---------------------------------------------------------------------------
# Decision-case adapter
# ---------------------------------------------------------------------------


_COMPLAINT_SCENARIOS: Final[frozenset[str]] = frozenset(
    {"complaint_compensation", "noise_complaint", "damage_report"}
)


class DecisionCaseTimelineSource:
    """Adapter over :class:`DecisionCaseStore`.

    Maps the ``OPS`` stage to :class:`EventKind.OPS`, compensation
    scenarios to :class:`EventKind.COMPLAINT`, and every other case to
    the generic :class:`EventKind.DECISION`.
    """

    name: Final[str] = "decision_case"

    def __init__(self, case_store: Any) -> None:
        self._store = case_store

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
        try:
            if reservation_id:
                raw = await self._store.get_by_reservation(reservation_id)
            else:
                raw = await self._store.search(
                    property_id=property_id or None,
                    limit=limit,
                )
        except Exception as exc:  # noqa: BLE001
            raise TimelineSourceError(
                self.name,
                "search failed",
                property_id=property_id,
                reservation_id=reservation_id,
            ) from exc

        events: list[TimelineEvent] = []
        for case in raw:
            if reservation_id and property_id and str(
                getattr(case, "property_id", "")
            ) != property_id:
                continue
            if guest_id and str(getattr(case, "guest_id", "")) != guest_id:
                continue
            when = _coerce_datetime(getattr(case, "created_at", None))
            if when is None:
                continue
            events.append(
                TimelineEvent(
                    occurred_at=when,
                    kind=_decision_kind(case),
                    summary=_decision_summary(case),
                    source=self.name,
                    native_id=str(getattr(case, "case_id", "")),
                    property_id=str(getattr(case, "property_id", "")),
                    details={
                        "stage": _enum_value(getattr(case, "stage", "")),
                        "scenario": _enum_value(getattr(case, "scenario", "")),
                        "reservation_id": getattr(case, "reservation_id", None),
                        "guest_id": getattr(case, "guest_id", None),
                    },
                )
            )
        return events


def _decision_kind(case: Any) -> EventKind:
    stage = _enum_value(getattr(case, "stage", "")).lower()
    scenario = _enum_value(getattr(case, "scenario", "")).lower()
    if stage == "ops":
        return EventKind.OPS
    if scenario in _COMPLAINT_SCENARIOS:
        return EventKind.COMPLAINT
    return EventKind.DECISION


def _decision_summary(case: Any) -> str:
    scenario = _enum_value(getattr(case, "scenario", "")) or "decision"
    decision = getattr(case, "decision", None)
    action = ""
    if decision is not None:
        action_type = getattr(decision, "action_type", "")
        action = _enum_value(action_type)
    if action:
        return f"{scenario}: {action}"
    return str(scenario)


# ---------------------------------------------------------------------------
# Guest-history adapter
# ---------------------------------------------------------------------------


_INCIDENT_COMPLAINT_TYPES: Final[frozenset[str]] = frozenset(
    {"complaint", "noise_complaint", "damage"}
)


class GuestHistoryTimelineSource:
    """Adapter over :class:`GuestHistoryStore`.

    Uses the structured ``get_property_incidents`` accessor rather than
    the Markdown-rendering ``build_property_context`` so the narrative
    layer keeps full control over wording.
    """

    name: Final[str] = "guest_history"

    def __init__(self, guest_history: Any) -> None:
        self._history = guest_history

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
        try:
            raw = await self._history.get_property_incidents(
                property_id, limit=limit
            )
        except Exception as exc:  # noqa: BLE001
            raise TimelineSourceError(
                self.name,
                "get_property_incidents failed",
                property_id=property_id,
            ) from exc

        events: list[TimelineEvent] = []
        for inc in raw:
            if reservation_id and str(
                getattr(inc, "booking_id", "")
            ) != reservation_id:
                continue
            if guest_id and str(getattr(inc, "guest_id", "")) != guest_id:
                continue
            when = _parse_iso(getattr(inc, "created_at", ""))
            if when is None:
                continue
            incident_type = str(getattr(inc, "incident_type", "")).lower()
            kind = (
                EventKind.COMPLAINT
                if incident_type in _INCIDENT_COMPLAINT_TYPES
                else EventKind.INCIDENT
            )
            events.append(
                TimelineEvent(
                    occurred_at=when,
                    kind=kind,
                    summary=_incident_summary(inc),
                    source=self.name,
                    native_id=str(getattr(inc, "incident_id", "")),
                    property_id=str(getattr(inc, "property_id", "")),
                    property_name=str(getattr(inc, "property_name", "")),
                    details={
                        "incident_type": incident_type,
                        "status": getattr(inc, "status", ""),
                        "severity": getattr(inc, "severity", 0),
                        "resolved_at": getattr(inc, "resolved_at", None),
                        "guest_name": getattr(inc, "guest_name", ""),
                    },
                )
            )
        return events


def _incident_summary(inc: Any) -> str:
    itype = str(getattr(inc, "incident_type", "") or "incident")
    status = str(getattr(inc, "status", "") or "open")
    summary = getattr(inc, "resolution_summary", None)
    if summary:
        return f"{itype} ({status}): {summary}"
    return f"{itype} ({status})"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning ``None`` on failure."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coerce_datetime(value: Any) -> datetime | None:
    """Accept a :class:`datetime` or an ISO string; return UTC-aware value."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        return _parse_iso(value)
    return None


def _enum_value(value: Any) -> str:
    """Extract ``.value`` from a ``StrEnum`` or fall back to ``str``."""
    raw = getattr(value, "value", value)
    return "" if raw is None else str(raw)
