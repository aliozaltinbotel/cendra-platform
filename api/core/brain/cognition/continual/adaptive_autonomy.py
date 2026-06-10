"""Adaptive Autonomy Manager — L1-L4 progression tracking.

Manages the autonomy level per owner/property combination.
As Brain Engine proves reliability, autonomy increases:
    L1 (Suggest):     Propose action, wait for approval
    L2 (Act & Inform): Execute, then notify owner
    L3 (Silent):       Execute silently, log only
    L4 (Act & Learn):  Execute and evolve skills autonomously

Promotion decisions use the **Wilson score lower bound** at 95 %
confidence rather than the raw ``successful / total`` ratio.  Raw
ratios are over-confident on small samples — 17/20 reports 0.85 but
its 95 % lower bound is only ≈ 0.63, which correctly keeps such an
owner/property out of the higher tiers until more evidence
accumulates.  See :mod:`core.brain.patterns.wilson`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Final

from core.brain.patterns.wilson import (
    AUTONOMY_WILSON_L2,
    AUTONOMY_WILSON_L3,
    AUTONOMY_WILSON_L4,
    wilson_lower_bound,
)

logger = logging.getLogger(__name__)


class AutonomyLevel(IntEnum):
    """Autonomy levels ordered by independence."""

    L1_SUGGEST = 1
    L2_ACT_INFORM = 2
    L3_SILENT = 3
    L4_ACT_LEARN = 4


# Progression thresholds — decision counts (statistical mass) and
# Wilson-score lower bounds (statistically-sound reliability floor).
_MIN_DECISIONS_FOR_L2: Final[int] = 20
_MIN_WILSON_FOR_L2: Final[float] = AUTONOMY_WILSON_L2
_MIN_DECISIONS_FOR_L3: Final[int] = 50
_MIN_WILSON_FOR_L3: Final[float] = AUTONOMY_WILSON_L3
_MIN_DECISIONS_FOR_L4: Final[int] = 100
_MIN_WILSON_FOR_L4: Final[float] = AUTONOMY_WILSON_L4

# Actions that always require approval regardless of autonomy level
_ALWAYS_REQUIRE_APPROVAL: frozenset[str] = frozenset(
    {
        "financial_transaction_above_limit",
        "guest_eviction",
        "policy_change",
        "vendor_contract",
        "insurance_claim_submission",
        "lock_code_change",
    }
)


@dataclass
class OwnerAutonomy:
    """Autonomy state for an owner/property pair.

    Attributes:
        owner_id: Owner identifier.
        property_id: Property identifier.
        level: Current autonomy level.
        total_decisions: Total decisions made.
        successful_decisions: Decisions that went well.
        owner_overrides: Times the owner overrode a decision.
        last_override_reason: Reason for the last override.
    """

    owner_id: str
    property_id: str
    level: int = AutonomyLevel.L1_SUGGEST
    total_decisions: int = 0
    successful_decisions: int = 0
    owner_overrides: int = 0
    last_override_reason: str = ""

    @property
    def success_rate(self) -> float:
        """Naive maximum-likelihood success rate (for display only).

        Not used for promotion decisions — see :meth:`wilson_lower` for
        the statistically-sound reliability floor.
        """
        if self.total_decisions == 0:
            return 0.0
        return self.successful_decisions / self.total_decisions

    @property
    def wilson_lower(self) -> float:
        """Wilson 95 % lower bound on the true success rate."""
        return wilson_lower_bound(
            self.successful_decisions,
            self.total_decisions,
        )


class AdaptiveAutonomyManager:
    """Manages autonomy progression per owner/property.

    Tracks decision quality and automatically suggests level
    upgrades when thresholds are met.

    Args:
        redis_client: Async Redis client for persistence.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._prefix = "brain:autonomy:"

    def get_level(
        self,
        owner_id: str,
        property_id: str,
    ) -> AutonomyLevel:
        """Get current autonomy level for an owner/property.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.

        Returns:
            Current AutonomyLevel.
        """
        state = self._load(owner_id, property_id)
        return AutonomyLevel(state.level)

    def should_ask_approval(
        self,
        owner_id: str,
        property_id: str,
        action: str,
    ) -> bool:
        """Check whether approval is needed for this action.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.
            action: The action to check.

        Returns:
            True if approval should be requested.
        """
        if action in _ALWAYS_REQUIRE_APPROVAL:
            return True

        level = self.get_level(owner_id, property_id)
        return level == AutonomyLevel.L1_SUGGEST

    def record_decision(
        self,
        owner_id: str,
        property_id: str,
        success: bool,
        owner_overrode: bool = False,
        override_reason: str = "",
    ) -> AutonomyLevel:
        """Record a decision outcome and check for level progression.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.
            success: Whether the decision was successful.
            owner_overrode: Whether the owner overrode the decision.
            override_reason: Reason for the override.

        Returns:
            Current (possibly updated) AutonomyLevel.
        """
        state = self._load(owner_id, property_id)

        state.total_decisions += 1
        if success:
            state.successful_decisions += 1
        if owner_overrode:
            state.owner_overrides += 1
            state.last_override_reason = override_reason

        new_level = self._evaluate_progression(state)
        if new_level > state.level:
            logger.info(
                "Autonomy upgrade: owner=%s property=%s L%d -> L%d",
                owner_id,
                property_id,
                state.level,
                new_level,
            )
            state.level = new_level

        # Demotion on recent overrides
        demoted_level = self._evaluate_demotion(state)
        if demoted_level < state.level:
            logger.info(
                "Autonomy downgrade: owner=%s property=%s L%d -> L%d",
                owner_id,
                property_id,
                state.level,
                demoted_level,
            )
            state.level = demoted_level

        self._save(state)
        return AutonomyLevel(state.level)

    def get_stats(
        self,
        owner_id: str,
        property_id: str,
    ) -> dict[str, Any]:
        """Get autonomy statistics for an owner/property.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.

        Returns:
            Dictionary with autonomy stats.
        """
        state = self._load(owner_id, property_id)
        return {
            "level": AutonomyLevel(state.level).name,
            "total_decisions": state.total_decisions,
            "success_rate": round(state.success_rate, 3),
            "wilson_lower": round(state.wilson_lower, 3),
            "owner_overrides": state.owner_overrides,
            "next_level_requirements": self._next_level_info(state),
        }

    # ── Progression / Demotion logic ────────────────────────────────── #

    @staticmethod
    def _evaluate_progression(state: OwnerAutonomy) -> int:
        """Check if the owner/property qualifies for a level upgrade.

        Uses the Wilson 95 % lower bound on the success rate together
        with a minimum-decisions floor so small-sample over-confidence
        cannot fast-track an owner into a higher autonomy tier.

        Args:
            state: Current autonomy state.

        Returns:
            The highest level the state qualifies for.
        """
        lower = state.wilson_lower
        total = state.total_decisions

        if total >= _MIN_DECISIONS_FOR_L4 and lower >= _MIN_WILSON_FOR_L4:
            return AutonomyLevel.L4_ACT_LEARN
        if total >= _MIN_DECISIONS_FOR_L3 and lower >= _MIN_WILSON_FOR_L3:
            return AutonomyLevel.L3_SILENT
        if total >= _MIN_DECISIONS_FOR_L2 and lower >= _MIN_WILSON_FOR_L2:
            return AutonomyLevel.L2_ACT_INFORM
        return AutonomyLevel.L1_SUGGEST

    @staticmethod
    def _evaluate_demotion(state: OwnerAutonomy) -> int:
        """Check if recent overrides warrant a demotion.

        Args:
            state: Current autonomy state.

        Returns:
            The level to demote to (same or lower).
        """
        if state.total_decisions < 10:
            return state.level

        recent_override_rate = state.owner_overrides / state.total_decisions
        if recent_override_rate > 0.2:
            return AutonomyLevel.L1_SUGGEST
        if recent_override_rate > 0.1 and state.level > AutonomyLevel.L2_ACT_INFORM:
            return AutonomyLevel.L2_ACT_INFORM

        return state.level

    @staticmethod
    def _next_level_info(state: OwnerAutonomy) -> dict[str, Any]:
        """Describe what's needed for the next level.

        Args:
            state: Current autonomy state.

        Returns:
            Requirements dict for the next level.
        """
        current = state.level
        if current >= AutonomyLevel.L4_ACT_LEARN:
            return {"status": "max_level_reached"}

        thresholds = {
            AutonomyLevel.L1_SUGGEST: (
                _MIN_DECISIONS_FOR_L2,
                _MIN_WILSON_FOR_L2,
            ),
            AutonomyLevel.L2_ACT_INFORM: (
                _MIN_DECISIONS_FOR_L3,
                _MIN_WILSON_FOR_L3,
            ),
            AutonomyLevel.L3_SILENT: (
                _MIN_DECISIONS_FOR_L4,
                _MIN_WILSON_FOR_L4,
            ),
        }
        min_decisions, min_wilson = thresholds.get(AutonomyLevel(current)) or (999, 1.0)

        return {
            "decisions_needed": max(0, min_decisions - state.total_decisions),
            "required_wilson_lower": min_wilson,
            "current_wilson_lower": round(state.wilson_lower, 3),
            "current_success_rate": round(state.success_rate, 3),
        }

    # ── Persistence ─────────────────────────────────────────────────── #

    def _load(
        self,
        owner_id: str,
        property_id: str,
    ) -> OwnerAutonomy:
        """Load autonomy state from Redis.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.

        Returns:
            OwnerAutonomy state (new if not found).
        """
        key = f"{self._prefix}{owner_id}:{property_id}"
        raw = self._redis.get(key)
        if not raw:
            return OwnerAutonomy(owner_id=owner_id, property_id=property_id)

        data = json.loads(raw)
        return OwnerAutonomy(**{k: v for k, v in data.items() if k in OwnerAutonomy.__dataclass_fields__})

    def _save(self, state: OwnerAutonomy) -> None:
        """Save autonomy state to Redis.

        Args:
            state: The state to persist.
        """
        key = f"{self._prefix}{state.owner_id}:{state.property_id}"
        self._redis.set(key, json.dumps(asdict(state)))
