"""Owner resolver backed by compiled DSL artefacts.

The Planner layer (Moat #4) ships the
:class:`core.brain.planning.OwnerStyleResolver` Protocol that the
:class:`core.brain.planning.StyleSelector` consults first when
picking a style.  This module supplies the concrete implementation
the DSL pipeline produces — the registry hands back the *built-in*
style id the owner pinned (selector lookup uses that), while the
extended denylist is registered with the registry under the same
id so the spec the selector retrieves carries the per-owner
constraints.

The registry is read-only after :meth:`load`; multi-tenant systems
typically construct one per tenant and keep them alive for the
lifetime of the process.
"""

from __future__ import annotations

from core.brain.planning.registry import StyleRegistry
from core.brain.planning.styles import PlannerStyleId
from core.brain.policy.compiler import (
    CompiledPolicy,
    OwnerPolicyCompiler,
)
from core.brain.policy.parser import OwnerPolicyParser

__all__ = [
    "DSLOwnerResolver",
    "load_owner_policy",
]


class DSLOwnerResolver:
    """:class:`OwnerStyleResolver` driven by a compiled policy.

    Construction takes a :class:`CompiledPolicy` and the
    :class:`StyleRegistry` to register per-owner specs into.  Once
    constructed, :meth:`resolve` answers selector queries.
    """

    def __init__(
        self,
        *,
        policy: CompiledPolicy,
        registry: StyleRegistry,
    ) -> None:
        self._owner_to_style: dict[str, PlannerStyleId] = {}
        self._jurisdictions: dict[str, str] = dict(policy.jurisdictions)
        for owner_id, derived_key in policy.owner_style.items():
            spec = policy.styles[derived_key]
            registry.register(spec)
            self._owner_to_style[owner_id] = spec.style_id

    def resolve(self, owner_id: str) -> PlannerStyleId | None:
        """Return the style id pinned for ``owner_id`` or ``None``."""
        return self._owner_to_style.get(owner_id)

    def jurisdiction_for(self, owner_id: str) -> str | None:
        """Return the jurisdiction code declared for ``owner_id``."""
        return self._jurisdictions.get(owner_id)


def load_owner_policy(
    *,
    source: str,
    registry: StyleRegistry,
) -> DSLOwnerResolver:
    """Parse, compile and register an owner-policy document.

    Args:
        source: Owner-policy DSL document text.
        registry: :class:`StyleRegistry` to register the per-owner
            :class:`PlannerStyleSpec` records into.

    Returns:
        A :class:`DSLOwnerResolver` ready to plug into
        :class:`core.brain.planning.StyleSelector`.

    Raises:
        OwnerPolicyParseError: On syntax errors.
        OwnerPolicyCompileError: On semantic errors (unknown
            style id, unknown action kind, duplicate owner).
    """
    parser = OwnerPolicyParser()
    document = parser.parse(source)
    compiler = OwnerPolicyCompiler()
    compiled = compiler.compile(document)
    return DSLOwnerResolver(policy=compiled, registry=registry)
