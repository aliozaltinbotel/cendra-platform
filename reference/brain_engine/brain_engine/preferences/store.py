"""PreferenceStore — Persistent storage for owner preference rules.

Stores rules in Redis with lookup by owner_id + property_id + action_type.
Rules are matched against incoming approval requests to determine if
auto-approval is possible.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from brain_engine.preferences.models import PreferenceRule, RuleScope

logger = logging.getLogger(__name__)


class PreferenceStore:
    """Stores and retrieves owner preference rules.

    Uses an in-memory dict as primary storage with optional Redis persistence.
    Rules are indexed by (owner_id, action_type) for fast lookup.

    Args:
        redis_client: Optional async Redis client for persistence.
    """

    REDIS_PREFIX = "pref:rule:"

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._rules: dict[str, PreferenceRule] = {}

    async def save_rule(
        self,
        owner_id: str,
        property_id: str,
        action_type: str,
        auto_approve: bool,
        scope: str = "this_property",
        conditions: dict[str, Any] | None = None,
        created_from: str = "",
    ) -> PreferenceRule:
        """Create or update a preference rule.

        If a rule with the same owner/property/action already exists,
        it is updated rather than duplicated.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.
            action_type: Action type this rule applies to.
            auto_approve: Whether to auto-approve.
            scope: Rule scope.
            conditions: Conditions for rule activation.
            created_from: Approval request that generated this rule.

        Returns:
            The saved PreferenceRule.
        """
        existing = await self._find_exact_rule(owner_id, property_id, action_type)
        now = datetime.now(timezone.utc).isoformat()

        if existing:
            existing.auto_approve = auto_approve
            existing.scope = RuleScope(scope)
            existing.conditions = conditions or {}
            existing.updated_at = now
            rule = existing
        else:
            rule = PreferenceRule(
                rule_id=f"RULE-{uuid.uuid4().hex[:8].upper()}",
                owner_id=owner_id,
                property_id=property_id,
                action_type=action_type,
                auto_approve=auto_approve,
                scope=RuleScope(scope),
                conditions=conditions or {},
                created_at=now,
                updated_at=now,
                created_from=created_from,
            )

        self._rules[rule.rule_id] = rule
        await self._persist_rule(rule)

        logger.info(
            "Preference rule saved: %s (owner=%s, property=%s, action=%s, approve=%s)",
            rule.rule_id, owner_id, property_id, action_type, auto_approve,
        )
        return rule

    async def find_rule(
        self,
        owner_id: str,
        property_id: str,
        action_type: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find the best matching rule for a given action.

        Searches rules by priority:
        1. Exact match (owner + property + action)
        2. Owner-wide rule (owner + action, all_properties scope)
        3. Always rule (owner + action, always scope)

        Checks conditions against context if provided.

        Args:
            owner_id: Owner identifier.
            property_id: Property identifier.
            action_type: Action type to look up.
            context: Current context for condition matching.

        Returns:
            Rule dict with auto_approve flag, or None if no rule found.
        """
        candidates: list[PreferenceRule] = []

        for rule in self._rules.values():
            if not rule.active:
                continue
            if rule.owner_id != owner_id:
                continue
            if rule.action_type != action_type:
                continue

            # Scope-based matching
            match rule.scope:
                case RuleScope.THIS_TIME:
                    continue  # One-time rules are already consumed
                case RuleScope.THIS_PROPERTY:
                    if rule.property_id != property_id:
                        continue
                case RuleScope.ALL_PROPERTIES | RuleScope.ALWAYS:
                    pass  # Matches any property
                case RuleScope.CONDITIONAL:
                    if not self._check_conditions(rule.conditions, context or {}):
                        continue

            candidates.append(rule)

        if not candidates:
            return None

        # Pick highest priority rule
        best = max(candidates, key=lambda r: r.priority)
        best.usage_count += 1
        await self._persist_rule(best)

        return {
            "rule_id": best.rule_id,
            "auto_approve": best.auto_approve,
            "scope": best.scope.value,
            "conditions": best.conditions,
            "usage_count": best.usage_count,
        }

    async def get_rules_for_owner(self, owner_id: str) -> list[PreferenceRule]:
        """Get all active rules for a specific owner."""
        return [
            rule for rule in self._rules.values()
            if rule.owner_id == owner_id and rule.active
        ]

    async def deactivate_rule(self, rule_id: str) -> bool:
        """Deactivate a rule (soft delete)."""
        rule = self._rules.get(rule_id)
        if not rule:
            return False
        rule.active = False
        rule.updated_at = datetime.now(timezone.utc).isoformat()
        await self._persist_rule(rule)
        return True

    async def load_from_redis(self) -> int:
        """Load all rules from Redis into memory. Returns count loaded."""
        if not self._redis:
            return 0

        try:
            keys = await self._redis.keys(f"{self.REDIS_PREFIX}*")
            count = 0
            for key in keys:
                data = await self._redis.get(key)
                if data:
                    rule = PreferenceRule.model_validate_json(data)
                    self._rules[rule.rule_id] = rule
                    count += 1
            logger.info("Loaded %d preference rules from Redis", count)
            return count
        except Exception:
            logger.exception("Failed to load preference rules from Redis")
            return 0

    async def _find_exact_rule(
        self,
        owner_id: str,
        property_id: str,
        action_type: str,
    ) -> PreferenceRule | None:
        """Find an exact match rule (same owner, property, action)."""
        for rule in self._rules.values():
            if (
                rule.owner_id == owner_id
                and rule.property_id == property_id
                and rule.action_type == action_type
                and rule.active
            ):
                return rule
        return None

    @staticmethod
    def _check_conditions(
        conditions: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """Check if context satisfies rule conditions.

        Supports simple operators:
        - key: value (exact match)
        - key_min: value (>= comparison)
        - key_max: value (<= comparison)
        """
        for key, expected in conditions.items():
            if key.endswith("_min"):
                base_key = key[:-4]
                actual = context.get(base_key)
                if actual is None or actual < expected:
                    return False
            elif key.endswith("_max"):
                base_key = key[:-4]
                actual = context.get(base_key)
                if actual is None or actual > expected:
                    return False
            else:
                if context.get(key) != expected:
                    return False
        return True

    async def _persist_rule(self, rule: PreferenceRule) -> None:
        """Persist a rule to Redis (if available)."""
        if not self._redis:
            return
        try:
            key = f"{self.REDIS_PREFIX}{rule.rule_id}"
            await self._redis.set(key, rule.model_dump_json())
        except Exception:
            logger.exception("Failed to persist rule %s", rule.rule_id)
