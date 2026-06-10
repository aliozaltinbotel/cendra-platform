"""PolicyEnforcer — Checks existing preference rules before executing actions.

Before each action, the enforcer queries the PreferenceStore to determine
if the owner has established rules for this action type. Returns a decision:
auto-approve, auto-deny, or require explicit approval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from brain_engine.preferences.store import PreferenceStore

logger = logging.getLogger(__name__)


class PolicyDecision(StrEnum):
    """Decision from the policy enforcer."""

    AUTO_APPROVE = "auto_approve"
    AUTO_DENY = "auto_deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Result of a policy check.

    Attributes:
        decision: The enforcement decision.
        rule_id: ID of the rule that produced this decision (if any).
        reason: Human-readable reason for the decision.
    """

    decision: PolicyDecision
    rule_id: str = ""
    reason: str = ""


class PolicyEnforcer:
    """Checks preference rules before action execution.

    Queries the PreferenceStore to find applicable rules and returns
    a decision for the ApprovalGateway.

    Args:
        preference_store: Store containing owner preference rules.
    """

    def __init__(self, preference_store: PreferenceStore) -> None:
        self._store = preference_store

    async def check_policy(
        self,
        owner_id: str,
        property_id: str,
        action_type: str,
        context: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """Check if an action has a matching preference rule.

        Args:
            owner_id: Property owner ID.
            property_id: Property ID.
            action_type: Action being proposed.
            context: Current context for condition evaluation.

        Returns:
            PolicyResult with the decision and optional rule reference.
        """
        rule = await self._store.find_rule(
            owner_id=owner_id,
            property_id=property_id,
            action_type=action_type,
            context=context,
        )

        if rule is None:
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="No preference rule found — explicit approval required.",
            )

        if rule["auto_approve"]:
            logger.info(
                "Policy auto-approves %s for owner=%s (rule=%s)",
                action_type, owner_id, rule["rule_id"],
            )
            return PolicyResult(
                decision=PolicyDecision.AUTO_APPROVE,
                rule_id=rule["rule_id"],
                reason=f"Auto-approved by rule {rule['rule_id']} (scope: {rule['scope']}).",
            )

        logger.info(
            "Policy auto-denies %s for owner=%s (rule=%s)",
            action_type, owner_id, rule["rule_id"],
        )
        return PolicyResult(
            decision=PolicyDecision.AUTO_DENY,
            rule_id=rule["rule_id"],
            reason=f"Auto-denied by rule {rule['rule_id']} (scope: {rule['scope']}).",
        )

    async def get_owner_policy_summary(self, owner_id: str) -> dict[str, Any]:
        """Get a summary of all rules for an owner.

        Returns:
            Dict with rule counts by action type and scope.
        """
        rules = await self._store.get_rules_for_owner(owner_id)

        summary: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            action = rule.action_type
            if action not in summary:
                summary[action] = []
            summary[action].append({
                "rule_id": rule.rule_id,
                "property_id": rule.property_id or "all",
                "auto_approve": rule.auto_approve,
                "scope": rule.scope.value,
                "conditions": rule.conditions,
                "usage_count": rule.usage_count,
            })

        return {
            "owner_id": owner_id,
            "total_rules": len(rules),
            "rules_by_action": summary,
        }
