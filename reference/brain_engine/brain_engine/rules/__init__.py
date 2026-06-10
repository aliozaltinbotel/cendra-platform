"""Rules engine — composite rules with numeric conditions and behavioral guidance.

Supports conditional AI behavior based on PMS data (reservation amounts,
guest tiers, booking dates).  Rules combine a testable condition (label)
with behavioral guidance (tone/action when true vs false).
"""

from brain_engine.rules.composite_rule import (
    CompositeRule,
    ConditionOperator,
    ConditionalBehavior,
    RuleCondition,
)
from brain_engine.rules.condition_evaluator import ConditionEvaluator, EvalContext

__all__ = [
    "CompositeRule",
    "ConditionEvaluator",
    "ConditionOperator",
    "ConditionalBehavior",
    "EvalContext",
    "RuleCondition",
]
