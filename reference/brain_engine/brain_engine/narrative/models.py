"""Value objects for the property-timeline narrative subsystem.

Defines the unified shape that every source adapter produces and that
the composer, renderers, and API endpoint consume.  Models are pure
data: all derivation logic stays in small helper methods so callers
never mutate them.

Core objects:

- ``EventKind`` — coarse taxonomy used for filtering and grouping.
- ``RenderStyle`` — deterministic renderer verbosity selector.
- ``TimelineRange`` — half-open ``[since, until)`` window with a
  ``from_params`` constructor that normalises ``days`` / ``since`` /
  ``until`` inputs and a cheap ``contains`` predicate.
- ``TimelineEvent`` — single unified event carrying the originating
  source tag, a stable ``dedupe_key`` and a monotonic ``occurred_at``
  timestamp for chronological sorting.
- ``Narrative`` — top-level result with the composed text, the events
  it was rendered from, the range it covers, and a free-form ``meta``
  mapping reserved as the forward seam for Gap #3 causal links.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Mapping

__all__ = [
    "EventKind",
    "Narrative",
    "RenderStyle",
    "TimelineEvent",
    "TimelineRange",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventKind(StrEnum):
    """Coarse taxonomy applied to every timeline event.

    Each source adapter maps its native type into one of these values.
    Unknown types fall back to ``OTHER`` so the narrative stays
    complete even as new upstream categories appear.
    """

    BOOKING = "booking"
    INCIDENT = "incident"
    DECISION = "decision"
    COMPLAINT = "complaint"
    UPSELL = "upsell"
    OPS = "ops"
    OTHER = "other"


class RenderStyle(StrEnum):
    """Verbosity selector for the deterministic text renderer."""

    CONCISE = "concise"
    FULL = "full"


# ---------------------------------------------------------------------------
# Time window
# ---------------------------------------------------------------------------


_DEFAULT_RANGE_DAYS: Final[int] = 90
_MAX_RANGE_DAYS: Final[int] = 3650


@dataclass(frozen=True, slots=True)
class TimelineRange:
    """Half-open time window ``[since, until)`` used for timeline queries.

    Always stored in UTC.  Construction from raw HTTP parameters is
    delegated to :meth:`from_params` so boundary parsing lives in one
    place.
    """

    since: datetime
    until: datetime

    def __post_init__(self) -> None:
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError("TimelineRange boundaries must be timezone-aware")
        if self.since > self.until:
            raise ValueError("TimelineRange.since must be <= until")

    @classmethod
    def from_params(
        cls,
        *,
        days: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        now: datetime | None = None,
    ) -> TimelineRange:
        """Build a range from the three mutually-compatible HTTP params.

        Resolution order:

        1. Explicit ``since`` + ``until`` wins.
        2. Explicit ``since`` with no ``until`` closes at ``now``.
        3. Otherwise use a rolling window of ``days`` (default 90) that
           ends at ``now``.

        ``days`` is clamped into ``[1, _MAX_RANGE_DAYS]`` to keep store
        queries finite.
        """
        anchor = now if now is not None else datetime.now(timezone.utc)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        if since is not None and until is not None:
            return cls(_as_utc(since), _as_utc(until))
        if since is not None:
            return cls(_as_utc(since), anchor)

        span = days if days is not None else _DEFAULT_RANGE_DAYS
        if span < 1:
            span = 1
        if span > _MAX_RANGE_DAYS:
            span = _MAX_RANGE_DAYS
        return cls(anchor - timedelta(days=span), anchor)

    def contains(self, at: datetime) -> bool:
        """Return ``True`` when ``at`` lies inside ``[since, until)``."""
        moment = _as_utc(at)
        return self.since <= moment < self.until

    @property
    def span_days(self) -> int:
        """Window length in whole days, rounded up to at least 1."""
        delta = self.until - self.since
        raw = delta.days + (1 if delta.seconds or delta.microseconds else 0)
        return max(raw, 1)


def _as_utc(value: datetime) -> datetime:
    """Normalise a datetime to UTC, assuming naive inputs are already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Unified event
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One chronological item produced by a :class:`TimelineSource`.

    Fields are intentionally minimal — rich per-source payloads live in
    ``details`` so rendering stays uniform.

    Attributes:
        occurred_at: UTC timestamp used for ordering.  Always timezone
            aware; naive inputs are coerced in ``__post_init__``.
        kind: Coarse :class:`EventKind` taxonomy.
        summary: One-line human sentence suitable for both text and
            voice output.
        source: Tag of the originating adapter (``"customer_memory"``,
            ``"decision_case"``, ``"guest_history"``, …).  Used for
            dedupe keys and observability.
        native_id: Stable identifier from the source system.  Empty
            string when the upstream object has no id.
        property_id: Property the event belongs to.
        property_name: Human-readable property label when available.
        details: Free-form payload the renderers may inspect.
    """

    occurred_at: datetime
    kind: EventKind
    summary: str
    source: str
    native_id: str = ""
    property_id: str = ""
    property_name: str = ""
    details: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            object.__setattr__(
                self,
                "occurred_at",
                self.occurred_at.replace(tzinfo=timezone.utc),
            )
        else:
            object.__setattr__(
                self,
                "occurred_at",
                self.occurred_at.astimezone(timezone.utc),
            )
        if not isinstance(self.details, MappingProxyType):
            object.__setattr__(
                self,
                "details",
                MappingProxyType(dict(self.details)),
            )

    def dedupe_key(self) -> tuple[str, str, str, str]:
        """Stable tuple used by the composer to drop duplicates.

        Prefers ``(source, native_id)`` when ``native_id`` is present.
        Falls back to ``(source, kind, occurred_at_iso, summary)`` so
        sources without stable ids still dedupe sensibly against
        themselves on re-fetch.
        """
        if self.native_id:
            return (self.source, self.native_id, "", "")
        return (
            self.source,
            self.kind.value,
            self.occurred_at.isoformat(),
            self.summary,
        )


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Narrative:
    """Composed text narrative plus the events it was rendered from.

    Attributes:
        text: Final narrative string (plain text, safe for TTS).
        events: Events that fed the rendering, in ascending time order.
        range: Effective window the composer actually used.
        meta: Forward-compatible mapping.  Gap #3 will attach a causal
            graph here without changing the public contract.
    """

    text: str
    events: tuple[TimelineEvent, ...]
    range: TimelineRange
    meta: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not isinstance(self.meta, MappingProxyType):
            object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))
