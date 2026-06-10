"""Value objects for the causal-navigation subsystem (Gap #3).

Every object here is immutable (``frozen=True, slots=True``) so that
inference rules, the graph builder, and the navigation service can
pass them around without defensive copying.

Conventions
-----------

- An edge points from the *earlier / causative* event (``source_key``)
  to the *later / effect* event (``target_key``).  All :class:`CausalKind`
  variants describe how the target relates to the source in that
  direction (``target was triggered by source``, ``target resolved
  source``, and so on).
- Event identity is handled via :func:`event_key` so the graph never
  depends on Python object identity.
- Confidence values are clamped into ``[0.0, 1.0]``.  Rules may emit
  ``0.0`` to express "I saw a link but cannot score it", which the
  builder will treat as below-threshold and drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from brain_engine.narrative.models import TimelineEvent

__all__ = [
    "CausalChain",
    "CausalEdge",
    "CausalGraph",
    "CausalKind",
    "event_key",
]


class CausalKind(StrEnum):
    """How the target event relates to the source event."""

    TRIGGERED = "triggered"
    RESOLVED = "resolved"
    MITIGATED = "mitigated"
    FOLLOWED_UP = "followed_up"
    CAUSED = "caused"
    RELATED = "related"


def event_key(event: TimelineEvent) -> str:
    """Return a stable string key for a timeline event.

    The underlying :meth:`TimelineEvent.dedupe_key` already returns a
    tuple that collapses duplicates across fetches.  We flatten it into
    a single string so the graph can key dictionaries and JSON
    serialise without extra work.
    """
    parts = event.dedupe_key()
    return "|".join(parts)


@dataclass(frozen=True, slots=True)
class CausalEdge:
    """One directed causal link between two timeline events.

    Attributes:
        source_key: :func:`event_key` of the earlier event.
        target_key: :func:`event_key` of the later event.
        kind: Semantic label chosen by the inference rule.
        confidence: Score in ``[0.0, 1.0]``; higher means the rule is
            more certain the link is real.
        reason: Short human explanation — surfaced to the client so
            operators can audit why a link was drawn.
        inferred_by: Tag of the rule that produced the edge; useful
            for debugging and for filtering in tests.
    """

    source_key: str
    target_key: str
    kind: CausalKind
    confidence: float
    reason: str = ""
    inferred_by: str = ""

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("CausalEdge.source_key must be non-empty")
        if not self.target_key:
            raise ValueError("CausalEdge.target_key must be non-empty")
        if self.source_key == self.target_key:
            raise ValueError("CausalEdge cannot be a self-loop")
        clamped = max(0.0, min(1.0, float(self.confidence)))
        if clamped != self.confidence:
            object.__setattr__(self, "confidence", clamped)

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """Identity used by the builder when merging duplicate edges."""
        return (self.source_key, self.target_key, self.kind.value)


@dataclass(frozen=True, slots=True)
class CausalChain:
    """Ordered sequence of edges forming one navigation path."""

    anchor_key: str
    direction: str
    edges: tuple[CausalEdge, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in {"ancestors", "descendants"}:
            raise ValueError(
                "CausalChain.direction must be 'ancestors' or 'descendants'"
            )

    @property
    def depth(self) -> int:
        """Number of edges walked from the anchor."""
        return len(self.edges)

    @property
    def leaf_key(self) -> str:
        """Key of the furthest event reached; falls back to the anchor."""
        if not self.edges:
            return self.anchor_key
        last = self.edges[-1]
        return last.source_key if self.direction == "ancestors" else last.target_key


@dataclass(frozen=True, slots=True)
class CausalGraph:
    """Immutable bundle of timeline events plus their causal edges."""

    events: tuple[TimelineEvent, ...] = ()
    edges: tuple[CausalEdge, ...] = ()
    meta: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not isinstance(self.meta, MappingProxyType):
            object.__setattr__(
                self,
                "meta",
                MappingProxyType(dict(self.meta)),
            )

    def event(self, key: str) -> TimelineEvent | None:
        """Return the event identified by ``key``, or ``None``."""
        for candidate in self.events:
            if event_key(candidate) == key:
                return candidate
        return None

    def outgoing(self, key: str) -> tuple[CausalEdge, ...]:
        """Edges pointing *away* from ``key`` (descendants direction)."""
        return tuple(edge for edge in self.edges if edge.source_key == key)

    def incoming(self, key: str) -> tuple[CausalEdge, ...]:
        """Edges pointing *into* ``key`` (ancestors direction)."""
        return tuple(edge for edge in self.edges if edge.target_key == key)

    def keys(self) -> Iterable[str]:
        """Yield keys for every event currently in the graph."""
        return (event_key(e) for e in self.events)
