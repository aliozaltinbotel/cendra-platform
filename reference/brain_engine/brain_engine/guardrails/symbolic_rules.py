"""Symbolic Rules Engine — hard logical rules that cannot be violated.

Implements the neuro-symbolic validation layer from Brain Engine architecture.
Rules are deterministic and take precedence over LLM-generated actions.

Each rule returns (allowed: bool, reason: str). If any rule blocks,
the action is rejected regardless of what the LLM suggested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleResult:
    """Result of evaluating a symbolic rule."""
    rule_name: str
    allowed: bool
    reason: str


Rule = Callable[[str, dict[str, Any]], RuleResult | None]


class SymbolicRulesEngine:
    """Validates actions against hard business rules before execution.

    Rules are ordered by priority. The engine short-circuits on the first
    blocking rule (HIGH severity).
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, Rule]] = []
        self._register_default_rules()

    def add_rule(self, name: str, rule: Rule) -> None:
        self._rules.append((name, rule))

    def validate(self, action: str, context: dict[str, Any]) -> list[RuleResult]:
        """Validate an action against all rules.

        Args:
            action: The action being attempted (e.g. "confirm_order", "make_call").
            context: Current state including slot values.

        Returns:
            List of RuleResult objects. Check any with allowed=False.
        """
        results: list[RuleResult] = []
        for name, rule_fn in self._rules:
            try:
                result = rule_fn(action, context)
                if result is not None:
                    results.append(result)
                    if not result.allowed:
                        logger.warning("Rule '%s' blocked action '%s': %s", name, action, result.reason)
            except Exception as exc:
                logger.error("Rule '%s' raised: %s", name, exc)
        return results

    def is_allowed(self, action: str, context: dict[str, Any]) -> tuple[bool, str]:
        """Quick check: is the action allowed?

        Returns:
            (True, "OK") or (False, "reason for rejection").
        """
        results = self.validate(action, context)
        for r in results:
            if not r.allowed:
                return False, r.reason
        return True, "OK"

    def _register_default_rules(self) -> None:
        """Register default Airbnb property management rules."""
        self.add_rule("no_call_without_phone", _rule_no_call_without_phone)
        self.add_rule("no_duplicate_calls", _rule_no_duplicate_calls)
        self.add_rule("require_slots_for_call", _rule_require_slots_for_call)
        self.add_rule("no_claim_without_photos", _rule_no_claim_without_photos)
        self.add_rule("cleaning_before_checkin", _rule_cleaning_before_checkin)
        self.add_rule("no_access_code_before_checkout", _rule_no_code_before_checkout)


# ── Default Rules ──────────────────────────────────────────────────────

def _rule_no_call_without_phone(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    if action.startswith("call_"):
        target = action.split("_", 1)[1] if "_" in action else ""
        phone_key = f"{target}_phone"
        phone = ctx.get(phone_key, "") or ctx.get("phone_number", "")
        if not phone:
            return RuleResult(
                rule_name="no_call_without_phone",
                allowed=False,
                reason=f"Cannot make call: no phone number for '{target}' (checked '{phone_key}')",
            )
    return None


def _rule_no_duplicate_calls(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    if action.startswith("call_"):
        active_calls = ctx.get("active_calls", [])
        if action in active_calls:
            return RuleResult(
                rule_name="no_duplicate_calls",
                allowed=False,
                reason=f"Call '{action}' is already in progress",
            )
    return None


def _rule_require_slots_for_call(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    required_slots = {
        "call_guest_checkin": ["incoming_guest_name", "incoming_guest_phone"],
        "call_guest_checkout": ["departing_guest_name", "departing_guest_phone"],
        "call_cleaner": ["cleaner_name", "cleaner_phone"],
    }
    if action in required_slots:
        missing = [s for s in required_slots[action] if not ctx.get(s)]
        if missing:
            return RuleResult(
                rule_name="require_slots_for_call",
                allowed=False,
                reason=f"Missing required slots for '{action}': {missing}",
            )
    return None


def _rule_no_claim_without_photos(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    if action == "submit_claim":
        photos = ctx.get("photos_after", [])
        if not photos:
            return RuleResult(
                rule_name="no_claim_without_photos",
                allowed=False,
                reason="Cannot submit damage claim without post-checkout photos",
            )
    return None


def _rule_cleaning_before_checkin(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    if action == "confirm_checkin":
        cleaning_done = ctx.get("cleaning_completed", False)
        if not cleaning_done:
            return RuleResult(
                rule_name="cleaning_before_checkin",
                allowed=False,
                reason="Cannot confirm guest check-in before cleaning is completed",
            )
    return None


def _rule_no_code_before_checkout(action: str, ctx: dict[str, Any]) -> RuleResult | None:
    if action == "send_access_code":
        guest_out = ctx.get("departing_guest_checked_out", False)
        if not guest_out:
            return RuleResult(
                rule_name="no_access_code_before_checkout",
                allowed=False,
                reason="Cannot send cleaner access code until departing guest has checked out",
            )
    return None
