"""Runtime dispatcher for the 24/7 escalation chain.

Given a starting tier, a property, and the current moment, walk the
:class:`EscalationPolicy` chain until a tier yields at least one
on-duty :class:`brain_engine.team.TeamMember`.  The result records
both the resolved tier and the ordered fallback chain so the V2 UI
can render *why* this responder was picked and who is next on call.

The dispatcher is stateless apart from its dependencies:

- :class:`EscalationPolicy` — the policy object (immutable; safe
  to share between dispatchers).
- :class:`brain_engine.team.TeamRoster` — the live roster used to
  resolve a tier into concrete people.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import structlog

from brain_engine.escalation.models import (
    EscalationLevel,
    EscalationPolicy,
    EscalationTier,
)
from brain_engine.team.models import TeamMember
from brain_engine.team.roster import TeamRoster


__all__ = ["EscalationDecision", "EscalationDispatcher"]


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """Resolved escalation routing for a single situation.

    Attributes:
        starting_tier: Tier the dispatcher was asked to start at.
        resolved_tier: Tier that actually fielded a responder.
        responders: Ordered on-duty members at ``resolved_tier``;
            empty when even the bottom of the chain has nobody.
        fallback_chain: Tiers walked beyond ``resolved_tier`` (for
            transparency in the UI).
        response_window_minutes: SLA promise of ``resolved_tier``.
        escalated: ``True`` when the dispatcher had to walk past
            the starting tier to find a responder.
    """

    starting_tier: EscalationTier
    resolved_tier: EscalationTier
    responders: tuple[TeamMember, ...]
    fallback_chain: tuple[EscalationTier, ...]
    response_window_minutes: int

    @property
    def escalated(self) -> bool:
        """Whether the resolved tier differs from the starting tier."""
        return self.resolved_tier is not self.starting_tier


class EscalationDispatcher:
    """Picks the right escalation tier for a property + moment.

    The dispatcher does not send messages — it only *resolves* the
    target tier and the on-duty chain.  Outbound delivery (Telegram,
    voice, push) is the caller's responsibility.
    """

    def __init__(
        self,
        *,
        policy: EscalationPolicy,
        roster: TeamRoster,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._roster = roster
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = logger.bind(component="escalation_dispatcher")

    def dispatch(
        self,
        *,
        property_id: str,
        starting_tier: EscalationTier = EscalationTier.TIER_1_PRIMARY_PM,
        at: datetime | None = None,
    ) -> EscalationDecision:
        """Walk the chain from ``starting_tier`` until somebody answers.

        Args:
            property_id: Property the situation is attached to.
            starting_tier: Tier the situation kind suggests as the
                entry point (e.g. a quiet-hours guest message starts
                at ``TIER_1_PRIMARY_PM``; a fire alarm at
                ``TIER_4_PARTNER_24_7``).
            at: Override moment used for shift evaluation; defaults
                to the dispatcher clock.

        Returns:
            A populated :class:`EscalationDecision`.  When even the
            bottom of the chain cannot field anybody, the decision
            still names the bottom tier with an empty
            ``responders`` tuple so the caller can surface the gap.
        """
        moment = at or self._clock()
        chain = self._walk_chain(starting_tier)
        fallback: list[EscalationTier] = []
        for level in chain:
            responders = self._responders(
                level=level,
                property_id=property_id,
                moment=moment,
            )
            if responders:
                self._log.info(
                    "escalation.resolved",
                    property_id=property_id,
                    starting_tier=starting_tier.value,
                    resolved_tier=level.tier.value,
                    responder_count=len(responders),
                )
                return EscalationDecision(
                    starting_tier=starting_tier,
                    resolved_tier=level.tier,
                    responders=responders,
                    fallback_chain=tuple(fallback),
                    response_window_minutes=(
                        level.response_window_minutes
                    ),
                )
            fallback.append(level.tier)
        last = chain[-1] if chain else None
        if last is None:
            self._log.warning(
                "escalation.empty_chain",
                property_id=property_id,
                starting_tier=starting_tier.value,
            )
            return EscalationDecision(
                starting_tier=starting_tier,
                resolved_tier=starting_tier,
                responders=(),
                fallback_chain=(),
                response_window_minutes=0,
            )
        self._log.warning(
            "escalation.no_responder",
            property_id=property_id,
            starting_tier=starting_tier.value,
            bottom_tier=last.tier.value,
        )
        return EscalationDecision(
            starting_tier=starting_tier,
            resolved_tier=last.tier,
            responders=(),
            fallback_chain=tuple(fallback[:-1]),
            response_window_minutes=last.response_window_minutes,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _walk_chain(
        self,
        starting_tier: EscalationTier,
    ) -> list[EscalationLevel]:
        """Materialise the ordered chain starting at ``starting_tier``."""
        chain: list[EscalationLevel] = []
        seen: set[EscalationTier] = set()
        current: EscalationTier | None = starting_tier
        while current is not None and current not in seen:
            level = self._policy.level_for(current)
            if level is None:
                break
            chain.append(level)
            seen.add(current)
            current = level.fallback_tier
        return chain

    def _responders(
        self,
        *,
        level: EscalationLevel,
        property_id: str,
        moment: datetime,
    ) -> tuple[TeamMember, ...]:
        """Resolve on-duty members for a given level."""
        if level.target_role is None:
            return ()
        return self._roster.on_duty_for_role(
            level.target_role,
            property_id=property_id,
            at=moment,
        )
