"""Causal inference rules for the timeline-causal subsystem (Gap #3).

Every rule is a small, independent heuristic that consumes the same
sorted list of :class:`TimelineEvent` objects and emits zero or more
:class:`CausalEdge` values.  The builder runs all rules concurrently
and merges the result, so a rule must never mutate shared state.

Rules shipped in v1
-------------------

- :class:`TemporalProximityRule` — consecutive events that fall within
  a short window are linked with :data:`CausalKind.TRIGGERED`.  The
  confidence decays linearly with the gap.
- :class:`ResolutionRule` — a decision that follows a complaint or an
  incident on the same property is linked with
  :data:`CausalKind.RESOLVED`.
- :class:`SharedEntityRule` — events that share a ``booking_id`` or
  ``guest_id`` in their ``details`` payload are linked with
  :data:`CausalKind.RELATED`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Protocol, Sequence, runtime_checkable

from brain_engine.causal.models import CausalEdge, CausalKind, event_key
from brain_engine.narrative.models import EventKind, TimelineEvent

__all__ = [
    "CausalInferenceRule",
    "ResolutionRule",
    "SharedEntityRule",
    "TemporalProximityRule",
]


_TRIGGER_TARGETS: frozenset[EventKind] = frozenset(
    {
        EventKind.INCIDENT,
        EventKind.COMPLAINT,
        EventKind.DECISION,
        EventKind.OPS,
    }
)

_RESOLUTION_SOURCES: frozenset[EventKind] = frozenset(
    {EventKind.INCIDENT, EventKind.COMPLAINT}
)

_SHARED_KEYS: tuple[str, ...] = ("booking_id", "guest_id", "reservation_id")


@runtime_checkable
class CausalInferenceRule(Protocol):
    """Protocol every concrete inference rule must satisfy."""

    tag: str

    async def infer(
        self,
        events: Sequence[TimelineEvent],
    ) -> Iterable[CausalEdge]:
        """Return edges inferred from the ordered event list."""
        ...


class TemporalProximityRule:
    """Link each event with its immediate successor on the same property.

    The rule is deliberately conservative: we only emit an edge when
    the target event kind is operationally interesting
    (:data:`_TRIGGER_TARGETS`) and when the time gap falls inside
    ``max_gap``.  Confidence decays linearly with the gap.
    """

    tag: str = "temporal_proximity"

    def __init__(
        self,
        *,
        max_gap: timedelta = timedelta(hours=24),
        min_confidence: float = 0.3,
    ) -> None:
        if max_gap <= timedelta(0):
            raise ValueError("TemporalProximityRule.max_gap must be positive")
        self._max_gap = max_gap
        self._min_confidence = max(0.0, min(1.0, float(min_confidence)))

    async def infer(
        self,
        events: Sequence[TimelineEvent],
    ) -> Iterable[CausalEdge]:
        edges: list[CausalEdge] = []
        ordered = sorted(events, key=lambda e: e.occurred_at)
        for before, after in zip(ordered, ordered[1:]):
            if after.kind not in _TRIGGER_TARGETS:
                continue
            if not _same_property(before, after):
                continue
            gap = after.occurred_at - before.occurred_at
            if gap < timedelta(0) or gap > self._max_gap:
                continue
            confidence = self._confidence(gap)
            if confidence < self._min_confidence:
                continue
            edges.append(
                CausalEdge(
                    source_key=event_key(before),
                    target_key=event_key(after),
                    kind=CausalKind.TRIGGERED,
                    confidence=confidence,
                    reason=(
                        f"{after.kind.value} followed {before.kind.value} "
                        f"within {_format_gap(gap)}"
                    ),
                    inferred_by=self.tag,
                )
            )
        return edges

    def _confidence(self, gap: timedelta) -> float:
        ratio = gap.total_seconds() / self._max_gap.total_seconds()
        return max(self._min_confidence, 1.0 - ratio)


class ResolutionRule:
    """Link a decision back to the complaint or incident it addresses."""

    tag: str = "resolution"

    def __init__(
        self,
        *,
        max_gap: timedelta = timedelta(hours=48),
        confidence: float = 0.8,
    ) -> None:
        if max_gap <= timedelta(0):
            raise ValueError("ResolutionRule.max_gap must be positive")
        self._max_gap = max_gap
        self._confidence = max(0.0, min(1.0, float(confidence)))

    async def infer(
        self,
        events: Sequence[TimelineEvent],
    ) -> Iterable[CausalEdge]:
        edges: list[CausalEdge] = []
        ordered = sorted(events, key=lambda e: e.occurred_at)
        for index, target in enumerate(ordered):
            if target.kind is not EventKind.DECISION:
                continue
            for source in reversed(ordered[:index]):
                if source.kind not in _RESOLUTION_SOURCES:
                    continue
                if not _same_property(source, target):
                    continue
                gap = target.occurred_at - source.occurred_at
                if gap > self._max_gap:
                    break
                edges.append(
                    CausalEdge(
                        source_key=event_key(source),
                        target_key=event_key(target),
                        kind=CausalKind.RESOLVED,
                        confidence=self._confidence,
                        reason=(
                            f"decision resolved preceding "
                            f"{source.kind.value}"
                        ),
                        inferred_by=self.tag,
                    )
                )
                break
        return edges


class SharedEntityRule:
    """Link events that refer to the same booking, guest or reservation."""

    tag: str = "shared_entity"

    def __init__(
        self,
        *,
        keys: tuple[str, ...] = _SHARED_KEYS,
        confidence: float = 0.5,
    ) -> None:
        if not keys:
            raise ValueError("SharedEntityRule.keys must be non-empty")
        self._keys = keys
        self._confidence = max(0.0, min(1.0, float(confidence)))

    async def infer(
        self,
        events: Sequence[TimelineEvent],
    ) -> Iterable[CausalEdge]:
        edges: list[CausalEdge] = []
        ordered = sorted(events, key=lambda e: e.occurred_at)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                shared = _first_shared_key(first, second, self._keys)
                if shared is None:
                    continue
                edges.append(
                    CausalEdge(
                        source_key=event_key(first),
                        target_key=event_key(second),
                        kind=CausalKind.RELATED,
                        confidence=self._confidence,
                        reason=f"shared {shared[0]}={shared[1]}",
                        inferred_by=self.tag,
                    )
                )
        return edges


def _same_property(a: TimelineEvent, b: TimelineEvent) -> bool:
    if not a.property_id or not b.property_id:
        return True
    return a.property_id == b.property_id


def _first_shared_key(
    a: TimelineEvent,
    b: TimelineEvent,
    keys: Sequence[str],
) -> tuple[str, str] | None:
    for key in keys:
        left = a.details.get(key)
        right = b.details.get(key)
        if left and right and left == right:
            return key, str(left)
    return None


def _format_gap(gap: timedelta) -> str:
    total = int(gap.total_seconds())
    if total < 3600:
        minutes = max(total // 60, 1)
        return f"{minutes}m"
    hours = total // 3600
    return f"{hours}h"
