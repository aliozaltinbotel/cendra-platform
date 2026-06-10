"""Workflow-kind vocabulary surface for per-workflow autonomy gating.

Genericised at port time from the reference's ``workflow_kinds.py``
@a761e29 per PORTING_MAP's explicit Batch 2 note — *"workflow_kinds.py
enum → per-tenant registry table (vertical-agnostic requirement)"*.
The reference declared a 12-member ``WorkflowKind`` StrEnum (the V2
wireframe card taxonomy) plus a hard-coded ``event_type`` → kind map;
both are hospitality vocabulary.  In cendra-platform:

- workflow kinds are opaque, vertical-neutral strings — stable wire
  values persisted in ``WorkflowAutonomy.workflow`` and exposed to the
  Trust Meter, so renames remain breaking;
- the vocabulary and its event aliases live in per-tenant registry rows
  (``models.brain_autonomy.BrainWorkflowKind``, seeded from
  ``packs/hospitality/workflow_kinds.yaml`` by the Batch 6 pack
  loader), surfaced through :class:`WorkflowKindRegistry`;
- resolvers stay pluggable: :data:`EXPLICIT_ATTRIBUTE_RESOLVER` honours
  an interaction's explicit ``workflow`` attribute (kernel default,
  vocabulary-free); :func:`make_event_resolver` builds the reference's
  event-map heuristic from registry / pack data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "EXPLICIT_ATTRIBUTE_RESOLVER",
    "InMemoryWorkflowKindRegistry",
    "WorkflowKindRegistry",
    "WorkflowResolver",
    "make_event_resolver",
]


type WorkflowResolver = Callable[[Any], str | None]


@runtime_checkable
class WorkflowKindRegistry(Protocol):
    """Per-tenant registry of workflow kinds and their event aliases."""

    def kinds(self) -> tuple[str, ...]:
        """Return the registered workflow-kind strings."""
        ...

    def resolve_event(self, event_type: str) -> str | None:
        """Map an ``event_type`` alias to its kind (``None`` = skip)."""
        ...


class InMemoryWorkflowKindRegistry:
    """Mapping-backed :class:`WorkflowKindRegistry` for tests / packs.

    ``event_aliases`` maps each kind to the event-type strings that
    resolve to it; lookups are case-insensitive, mirroring the
    reference's ``_EVENT_TYPE_MAP`` behaviour.
    """

    def __init__(self, event_aliases: Mapping[str, tuple[str, ...]]) -> None:
        self._kinds = tuple(event_aliases.keys())
        self._by_event: dict[str, str] = {}
        for kind, aliases in event_aliases.items():
            self._by_event[kind.lower()] = kind
            for alias in aliases:
                self._by_event[alias.lower()] = kind

    def kinds(self) -> tuple[str, ...]:
        return self._kinds

    def resolve_event(self, event_type: str) -> str | None:
        if not event_type:
            return None
        return self._by_event.get(event_type.lower())


def make_event_resolver(registry: WorkflowKindRegistry) -> WorkflowResolver:
    """Build the reference-shaped resolver on top of a registry.

    Honours an explicit ``ix.workflow`` attribute when present and
    registered (allows upstream code to bypass the event-type
    heuristic), otherwise resolves ``ix.event_type`` through the
    registry's alias table.  Unknown values resolve to ``None`` so the
    collector skips the interaction rather than bucketing it into an
    arbitrary workflow.
    """

    known = set(registry.kinds())

    def _resolve(ix: Any) -> str | None:
        explicit = str(getattr(ix, "workflow", "") or "")
        if explicit:
            return explicit if explicit in known else None
        return registry.resolve_event(str(getattr(ix, "event_type", "")))

    return _resolve


def _explicit_attribute_resolver(ix: Any) -> str | None:
    """Vocabulary-free default: trust only an explicit ``workflow`` attr."""
    explicit = str(getattr(ix, "workflow", "") or "")
    return explicit or None


EXPLICIT_ATTRIBUTE_RESOLVER: Final[WorkflowResolver] = _explicit_attribute_resolver
