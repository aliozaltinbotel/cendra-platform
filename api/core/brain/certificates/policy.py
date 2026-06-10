""":class:`TierPolicy` mapping action kinds to ceiling tiers.

A *policy* answers: "for this action class, what is the highest
:class:`AutonomyTier` (most autonomy) the runtime is willing to
allow?"  The verifier consults the policy when a certificate is
presented; if the cert's ``granted_tier`` exceeds the policy's
ceiling, the verification fails.

Action kinds are opaque, vertical-neutral strings (golden rule 4:
no vertical vocabulary in the kernel).  The kernel therefore ships
**no default mapping** — ceilings are data, supplied per vertical
pack (e.g. ``packs/hospitality/tier_defaults.yaml``) or per tenant.
Owner-policy DSL (Moat #2, Batch 5) will write into a richer policy
that narrows pack defaults per owner.  The conservative baseline a
pack ships should align with EU AI Act Art. 14 (human oversight on
significant-effect actions).

Genericised at port time from the reference's
``certificates/policy.py``, whose ``CardActionKind``-keyed
``_DEFAULTS`` / ``DEFAULT_TIER_POLICY`` were hospitality content;
that mapping now lives in ``packs/hospitality/tier_defaults.yaml``.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.brain.certificates.tier import AutonomyTier

__all__ = [
    "TierPolicy",
]


class TierPolicy:
    """Look up the ceiling tier authorised for an action kind.

    The mapping is injected at construction (pack defaults, tenant
    overrides); individual ceilings may be replaced at runtime via
    :meth:`override`.  ``ceiling_for`` raises :class:`KeyError` for
    action kinds without an explicit mapping so misconfigurations
    fail loudly rather than silently permitting full autonomy.
    """

    def __init__(
        self,
        defaults: Mapping[str, AutonomyTier] | None = None,
    ) -> None:
        self._mapping: dict[str, AutonomyTier] = dict(defaults or {})

    def ceiling_for(self, action_kind: str) -> AutonomyTier:
        """Return the highest authorised tier for ``action_kind``."""
        try:
            return self._mapping[action_kind]
        except KeyError as exc:
            raise KeyError(f"no tier ceiling configured for {action_kind!r}") from exc

    def override(
        self,
        action_kind: str,
        tier: AutonomyTier,
    ) -> None:
        """Set or replace the ceiling for one action kind."""
        self._mapping[action_kind] = tier

    def known_actions(self) -> tuple[str, ...]:
        """Return the action kinds with a configured ceiling."""
        return tuple(self._mapping.keys())
