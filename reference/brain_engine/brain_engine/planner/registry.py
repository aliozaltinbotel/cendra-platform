"""Style registry — built-in styles plus DSL-defined extensions.

The registry is the integration point Moat #2 (Owner-policy DSL)
will hit: the DSL compiler emits :class:`PlannerStyleSpec` records
for owner-defined styles and registers them here.
"""

from __future__ import annotations

from typing import Protocol

from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
    PlannerStyleSpec,
)


__all__ = [
    "OwnerStyleResolver",
    "StyleNotFoundError",
    "StyleRegistry",
]


class StyleNotFoundError(LookupError):
    """Raised when a requested style id is not registered."""


class OwnerStyleResolver(Protocol):
    """Maps an ``owner_id`` to the style id the owner prefers.

    The DSL of Moat #2 will provide a concrete implementation that
    reads the compiled DSL records.  In Moat #4, callers wire a
    no-op resolver (returns ``None`` for everything) so the safety
    default applies until the DSL ships.
    """

    def resolve(self, owner_id: str) -> PlannerStyleId | None:
        """Return the style pinned for ``owner_id`` or ``None``."""
        ...


class StyleRegistry:
    """Lookup + extension surface for Planner style specs.

    Construction seeds the six built-in styles.  :meth:`register`
    adds or overwrites a custom style; :meth:`get` retrieves a spec
    or raises :class:`StyleNotFoundError`.
    """

    def __init__(self) -> None:
        self._specs: dict[PlannerStyleId, PlannerStyleSpec] = dict(
            BUILTIN_STYLE_SPECS
        )

    def get(self, style_id: PlannerStyleId) -> PlannerStyleSpec:
        """Return the registered spec for ``style_id``."""
        try:
            return self._specs[style_id]
        except KeyError as exc:
            raise StyleNotFoundError(style_id.value) from exc

    def register(self, spec: PlannerStyleSpec) -> None:
        """Add or overwrite a spec (DSL-emitted custom styles)."""
        self._specs[spec.style_id] = spec

    def known_ids(self) -> tuple[PlannerStyleId, ...]:
        """Return the currently registered style ids."""
        return tuple(self._specs.keys())
