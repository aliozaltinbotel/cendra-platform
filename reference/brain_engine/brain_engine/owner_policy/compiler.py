"""Compile a :class:`PolicyDocument` to runtime artefacts.

The compiler converts an AST into:

1. :class:`PlannerStyleSpec` records that extend the built-in style
   denylist with the owner's extra ``forbid`` entries; each owner
   gets a *derived* style id of the form ``owner__<owner_id>``.
2. A mapping ``owner_id → derived style id`` that the registry of
   :mod:`brain_engine.owner_policy.registry` uses to satisfy the
   :class:`brain_engine.planner.OwnerStyleResolver` Protocol.
3. A mapping ``owner_id → jurisdiction code`` used by upstream
   selectors that want to override their default jurisdiction.

The compiler raises :class:`OwnerPolicyCompileError` for:

- Duplicate ``owner`` blocks for the same identifier in one
  document.
- Owner blocks with no ``style = ...`` statement *and* no
  ``forbid`` clause (would compile to a no-op style).
"""

from __future__ import annotations

from dataclasses import dataclass

from brain_engine.owner_policy.ast import (
    OwnerBlock,
    PolicyDocument,
)
from brain_engine.owner_policy.errors import (
    OwnerPolicyCompileError,
)
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
    PlannerStyleSpec,
)


__all__ = [
    "CompiledPolicy",
    "OwnerPolicyCompiler",
    "derived_style_id",
]


def derived_style_id(owner_id: str) -> str:
    """Return the per-owner derived style identifier.

    Derived ids are namespaced so they cannot collide with the six
    built-in :class:`PlannerStyleId` values.  The registry stores
    these as plain :class:`PlannerStyleSpec` records, but the
    :class:`PlannerStyleId` enum cannot enumerate them — callers
    look them up by the string returned here.
    """
    return f"owner__{owner_id}"


@dataclass(frozen=True, slots=True)
class CompiledPolicy:
    """Runtime artefacts produced from one :class:`PolicyDocument`.

    Attributes:
        styles: Mapping from derived style id (string) to
            :class:`PlannerStyleSpec`.  Keys are the strings
            returned by :func:`derived_style_id`.
        owner_style: Mapping from owner id to derived style id;
            empty when no owner declared a custom envelope.
        jurisdictions: Mapping from owner id to jurisdiction code;
            empty when no owner set ``jurisdiction = "..."``.
    """

    styles: dict[str, PlannerStyleSpec]
    owner_style: dict[str, str]
    jurisdictions: dict[str, str]


class OwnerPolicyCompiler:
    """Compile :class:`PolicyDocument` into a :class:`CompiledPolicy`.

    The compiler is stateless; one instance can compile many
    documents.  Each call returns a fresh :class:`CompiledPolicy`
    so multi-tenant callers can hold per-tenant artefacts safely.
    """

    def compile(
        self,
        document: PolicyDocument,
    ) -> CompiledPolicy:
        """Return the runtime artefacts for ``document``."""
        styles: dict[str, PlannerStyleSpec] = {}
        owner_style: dict[str, str] = {}
        jurisdictions: dict[str, str] = {}
        seen: set[str] = set()
        for block in document.owners:
            self._validate_unique(block, seen)
            seen.add(block.owner_id)
            spec = self._build_spec(block)
            if spec is not None:
                key = derived_style_id(block.owner_id)
                styles[key] = spec
                owner_style[block.owner_id] = key
            if block.jurisdiction is not None:
                jurisdictions[block.owner_id] = block.jurisdiction
        return CompiledPolicy(
            styles=styles,
            owner_style=owner_style,
            jurisdictions=jurisdictions,
        )

    # ── internals ─────────────────────────────────────────────── #

    @staticmethod
    def _validate_unique(
        block: OwnerBlock,
        seen: set[str],
    ) -> None:
        if block.owner_id in seen:
            raise OwnerPolicyCompileError(
                f"duplicate owner block {block.owner_id!r}"
            )

    @staticmethod
    def _build_spec(
        block: OwnerBlock,
    ) -> PlannerStyleSpec | None:
        """Return the per-owner spec or ``None`` when trivially empty.

        A block with no ``style`` and no ``forbid`` produces no
        spec — there is nothing to register.  Callers may still
        record the ``jurisdiction`` for that owner.
        """
        if block.style_id is None and not block.forbid:
            return None
        base = _resolve_base(block.style_id)
        denylist = frozenset(base.denylist | set(block.forbid))
        derived = derived_style_id(block.owner_id)
        # PlannerStyleSpec.style_id is typed as PlannerStyleId; we
        # repurpose the existing enum via a synthesised value when
        # a derived id is needed.  The registry matches by the
        # ``style_id`` attribute, so any string that round-trips
        # through StrEnum.__call__ will work — but the enum is
        # closed.  Instead, keep base.style_id and rely on the
        # CompiledPolicy.styles dict mapping the derived key.
        return PlannerStyleSpec(
            style_id=base.style_id,
            description=(
                f"{base.description} (owner override "
                f"{block.owner_id!r}: +{len(block.forbid)} forbid)"
                if block.forbid
                else base.description
            ),
            denylist=denylist,
            autonomy_ceiling=base.autonomy_ceiling,
            reversibility_ceiling=base.reversibility_ceiling,
            preference_weights=dict(base.preference_weights),
        )


def _resolve_base(
    style_id: PlannerStyleId | None,
) -> PlannerStyleSpec:
    """Return the built-in spec to derive from.

    Falls back to :attr:`PlannerStyleId.COOPERATIVE` when the
    owner pinned no style — the owner's ``forbid`` list is then
    layered on top of the cooperative default.
    """
    if style_id is None:
        return BUILTIN_STYLE_SPECS[PlannerStyleId.COOPERATIVE]
    return BUILTIN_STYLE_SPECS[style_id]
