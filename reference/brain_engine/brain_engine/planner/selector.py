"""Pick the Planner style for one decision.

The selector consults the owner resolver first; if the owner has no
pinned style, the regulated-jurisdiction safety default applies.
The default deliberately errs on the strict side: when in doubt for
a Reg 2024/1028 / EU AI Act high-risk jurisdiction,
:attr:`PlannerStyleId.COMPLIANCE_STRICT` wins.

Custom selection logic (e.g. severity-driven defensive switch)
lives here so future moats can extend without forking the styles
module.
"""

from __future__ import annotations

from typing import Final

import structlog

from brain_engine.planner.context import PlannerContext
from brain_engine.planner.decision import PlannerDecision
from brain_engine.planner.registry import (
    OwnerStyleResolver,
    StyleRegistry,
)
from brain_engine.planner.styles import PlannerStyleId


__all__ = ["StyleSelector"]


_REGULATED_JURISDICTIONS: Final[frozenset[str]] = frozenset(
    {
        "BCN",  # Barcelona — phase-out by 2028
        "PAR",  # Paris — Loi Le Meur 90-day cap
        "AMS",  # Amsterdam — 30-night / 15-night centre
        "AMS_CENTER",
        "BER",  # Berlin — ZwVbG permits
        "LIS",  # Lisbon — DL 76/2024 freeze
        "NYC",  # NYC LL18 — host-present, max 2 guests
    }
)


_HIGH_SEVERITY: Final[frozenset[str]] = frozenset(
    {"warn", "critical"}
)


logger = structlog.get_logger(__name__)


class StyleSelector:
    """Pick a :class:`PlannerStyleId` for one :class:`PlannerContext`.

    Selection order:

    1. Owner resolver — if the owner has a DSL-pinned style, use
       it.
    2. Jurisdiction default — regulated jurisdictions force
       :attr:`PlannerStyleId.COMPLIANCE_STRICT` regardless of
       owner.
    3. Severity default — ``warn`` / ``critical`` fall back to
       :attr:`PlannerStyleId.DEFENSIVE`.
    4. Otherwise — :attr:`PlannerStyleId.COOPERATIVE`.

    The selector returns a :class:`PlannerDecision` carrying the
    resolved spec plus a one-line rationale for the audit log.
    """

    def __init__(
        self,
        *,
        registry: StyleRegistry,
        owner_resolver: OwnerStyleResolver,
    ) -> None:
        self._registry = registry
        self._owner = owner_resolver
        self._log = logger.bind(component="planner_selector")

    def pick(self, context: PlannerContext) -> PlannerDecision:
        """Return the :class:`PlannerDecision` for ``context``."""
        owner_pick = self._owner.resolve(context.owner_id)
        if owner_pick is not None:
            spec = self._registry.get(owner_pick)
            rationale = (
                f"owner {context.owner_id} pinned "
                f"{owner_pick.value}"
            )
            self._log.info(
                "planner.style.owner_pinned",
                owner_id=context.owner_id,
                style_id=owner_pick.value,
            )
            return PlannerDecision(
                style_id=owner_pick,
                spec=spec,
                rationale=rationale,
            )
        jurisdiction = context.jurisdiction
        if (
            jurisdiction is not None
            and jurisdiction.upper() in _REGULATED_JURISDICTIONS
        ):
            spec = self._registry.get(
                PlannerStyleId.COMPLIANCE_STRICT
            )
            rationale = (
                f"jurisdiction {jurisdiction} is regulated; "
                "safety default"
            )
            self._log.info(
                "planner.style.jurisdiction_default",
                jurisdiction=jurisdiction,
                style_id=PlannerStyleId.COMPLIANCE_STRICT.value,
            )
            return PlannerDecision(
                style_id=PlannerStyleId.COMPLIANCE_STRICT,
                spec=spec,
                rationale=rationale,
            )
        if context.severity in _HIGH_SEVERITY:
            spec = self._registry.get(PlannerStyleId.DEFENSIVE)
            rationale = (
                f"severity={context.severity}; defensive default"
            )
            self._log.info(
                "planner.style.severity_default",
                severity=context.severity,
                style_id=PlannerStyleId.DEFENSIVE.value,
            )
            return PlannerDecision(
                style_id=PlannerStyleId.DEFENSIVE,
                spec=spec,
                rationale=rationale,
            )
        spec = self._registry.get(PlannerStyleId.COOPERATIVE)
        return PlannerDecision(
            style_id=PlannerStyleId.COOPERATIVE,
            spec=spec,
            rationale="no pin, no risk markers; default cooperative",
        )
