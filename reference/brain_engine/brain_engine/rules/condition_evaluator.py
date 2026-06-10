"""ConditionEvaluator — runtime evaluation of rule conditions against PMS data.

Takes a CompositeRule and live PMS context (reservation amount, guest tier,
dates, etc.) and determines which behavioral branch applies.  The result
is a prompt block ready for injection into the system prompt.

The evaluator is intentionally simple — it evaluates a single condition
against a flat dict of PMS fields.  Complex multi-condition rules should
be composed from multiple CompositeRules at the pipeline level.

Usage:
    evaluator = ConditionEvaluator()
    result = evaluator.evaluate(
        rule=baby_crib_rule,
        context=EvalContext(data={"reservation_amount": 1500}),
    )
    if result.matched:
        system_prompt += result.prompt_block
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from brain_engine.rules.composite_rule import (
    CompositeRule,
    ConditionOperator,
    ConditionalBehavior,
    RuleCondition,
)

logger = logging.getLogger(__name__)


# Operator dispatch table — maps ConditionOperator to a comparison function.
# Using stdlib operator module for clarity and correctness.
_OPS: Final[dict[ConditionOperator, Callable[[Any, Any], bool]]] = {
    ConditionOperator.GT: operator.gt,
    ConditionOperator.GTE: operator.ge,
    ConditionOperator.LT: operator.lt,
    ConditionOperator.LTE: operator.le,
    ConditionOperator.EQ: operator.eq,
    ConditionOperator.NEQ: operator.ne,
    ConditionOperator.IN: lambda val, lst: val in lst,
    ConditionOperator.NOT_IN: lambda val, lst: val not in lst,
    ConditionOperator.CONTAINS: lambda val, sub: sub in str(val),
}


@dataclass(frozen=True, slots=True)
class EvalContext:
    """Runtime context for condition evaluation.

    Holds a flat dict of PMS field values.  The evaluator looks up
    the condition's field name in this dict.

    Attributes:
        data: PMS data dict (e.g. {"reservation_amount": 1500, "guest_tier": "vip"}).
        property_id: Current property (for property-scoped rules).
    """

    data: dict[str, Any] = field(default_factory=dict)
    property_id: str = ""


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Result of evaluating a composite rule.

    Attributes:
        rule_id: Which rule was evaluated.
        matched: Whether a matching rule was found and evaluated.
        condition_met: Whether the condition evaluated to True.
        behavior: The selected behavioral branch.
        prompt_block: Rendered prompt text ready for injection.
        condition_display: Human-readable condition for logging.
        field_value: The actual PMS value that was compared.
    """

    rule_id: str = ""
    matched: bool = False
    condition_met: bool = False
    behavior: ConditionalBehavior = field(default_factory=ConditionalBehavior)
    prompt_block: str = ""
    condition_display: str = ""
    field_value: Any = None


class ConditionEvaluator:
    """Evaluates CompositeRule conditions against live PMS data.

    Stateless — safe to share across requests.
    """

    def evaluate(
        self,
        rule: CompositeRule,
        context: EvalContext,
    ) -> EvalResult:
        """Evaluate a single rule against the provided context.

        Args:
            rule: The composite rule to evaluate.
            context: PMS data context.

        Returns:
            EvalResult with the selected behavior and prompt block.
        """
        if not rule.active:
            return EvalResult(rule_id=rule.rule_id)

        if rule.condition is None:
            # No condition — always use behavior_if_true
            return EvalResult(
                rule_id=rule.rule_id,
                matched=True,
                condition_met=True,
                behavior=rule.behavior_if_true,
                prompt_block=rule.behavior_if_true.to_prompt_block(),
            )

        # Check property scope
        if rule.property_id and context.property_id:
            if rule.property_id != context.property_id:
                return EvalResult(rule_id=rule.rule_id)

        # Evaluate condition
        condition_met, field_value = self._eval_condition(
            rule.condition, context.data,
        )

        behavior = (
            rule.behavior_if_true if condition_met
            else rule.behavior_if_false
        )

        result = EvalResult(
            rule_id=rule.rule_id,
            matched=True,
            condition_met=condition_met,
            behavior=behavior,
            prompt_block=behavior.to_prompt_block(),
            condition_display=rule.condition.display,
            field_value=field_value,
        )

        logger.debug(
            "Rule '%s' evaluated: %s = %s → condition_met=%s",
            rule.name,
            rule.condition.field,
            field_value,
            condition_met,
        )

        return result

    def evaluate_many(
        self,
        rules: Sequence[CompositeRule],
        context: EvalContext,
        intent: str = "",
    ) -> list[EvalResult]:
        """Evaluate multiple rules, filtering by intent trigger.

        Only rules whose intent_trigger matches the provided intent
        (or rules with no intent_trigger) are evaluated.

        Args:
            rules: All available composite rules.
            context: PMS data context.
            intent: Current guest intent (e.g. "baby_crib_request").

        Returns:
            List of EvalResult for matched rules (unmatched rules excluded).
        """
        results: list[EvalResult] = []

        for rule in rules:
            # Filter by intent
            if rule.intent_trigger and intent:
                if rule.intent_trigger != intent:
                    continue

            result = self.evaluate(rule, context)
            if result.matched:
                results.append(result)

        return results

    def build_prompt_injection(
        self,
        results: Sequence[EvalResult],
    ) -> str:
        """Combine all matched rule behaviors into a single prompt block.

        Args:
            results: EvalResults from evaluate_many().

        Returns:
            Combined prompt text, or empty string if no rules matched.
        """
        blocks = [r.prompt_block for r in results if r.prompt_block]
        if not blocks:
            return ""

        header = "## Active Rules (from learned patterns)\n"
        return header + "\n\n".join(blocks)

    # ── Internal ─────────────────────────────────────────────── #

    @staticmethod
    def _eval_condition(
        condition: RuleCondition,
        data: dict[str, Any],
    ) -> tuple[bool, Any]:
        """Evaluate a single condition against the data dict.

        Returns (condition_met, actual_field_value).  If the field is
        missing from data, the condition evaluates to False.
        """
        field_value = data.get(condition.field)
        if field_value is None:
            return False, None

        op_func = _OPS.get(condition.operator)
        if op_func is None:
            logger.warning("Unknown operator: %s", condition.operator)
            return False, field_value

        try:
            # Coerce types for numeric comparison
            threshold = condition.value
            if isinstance(threshold, (int, float)) and isinstance(field_value, str):
                field_value = float(field_value)
            elif isinstance(field_value, (int, float)) and isinstance(threshold, str):
                threshold = float(threshold)

            return op_func(field_value, threshold), field_value
        except (TypeError, ValueError):
            logger.warning(
                "Type mismatch in condition: %s %s %s (field_value=%r)",
                condition.field,
                condition.operator.value,
                condition.value,
                field_value,
            )
            return False, field_value
