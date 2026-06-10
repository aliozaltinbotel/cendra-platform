"""CompositeRule — conditional AI behavior driven by PMS data.

A composite rule binds a testable condition (e.g. reservation_amount > 1000)
to two behavioral branches — one for when the condition is true, one for false.

This enables the Rule Creator to learn patterns like:
  "When a guest asks for a baby crib AND the reservation > €1000,
   accept warmly.  Otherwise, decline politely and suggest alternatives."

The condition is evaluated at runtime against live PMS data pulled via
reservation_info_retriever.  The matching behavior is then injected into
the system prompt before the LLM generates a response.

Data model:
  CompositeRule
    ├── RuleCondition        (field, operator, value)
    ├── ConditionalBehavior  (behavior_if_true)
    └── ConditionalBehavior  (behavior_if_false)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4


class ConditionOperator(StrEnum):
    """Comparison operators for rule conditions."""

    GT = "gt"        # >
    GTE = "gte"      # >=
    LT = "lt"        # <
    LTE = "lte"      # <=
    EQ = "eq"        # ==
    NEQ = "neq"      # !=
    IN = "in"        # value in list
    NOT_IN = "not_in"  # value not in list
    CONTAINS = "contains"  # string contains substring


# Maps operators to human-readable labels for PM notifications
_OPERATOR_LABELS: Final[dict[ConditionOperator, str]] = {
    ConditionOperator.GT: ">",
    ConditionOperator.GTE: ">=",
    ConditionOperator.LT: "<",
    ConditionOperator.LTE: "<=",
    ConditionOperator.EQ: "=",
    ConditionOperator.NEQ: "!=",
    ConditionOperator.IN: "in",
    ConditionOperator.NOT_IN: "not in",
    ConditionOperator.CONTAINS: "contains",
}


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """A single testable condition against PMS data.

    Attributes:
        field: The PMS field to evaluate (e.g. "reservation_amount", "guest_tier").
        operator: Comparison operator.
        value: The threshold/target value.  Type depends on operator.
        unit: Optional display unit (e.g. "EUR", "nights") — not used in evaluation.
    """

    field: str
    operator: ConditionOperator
    value: Any
    unit: str = ""

    @property
    def display(self) -> str:
        """Human-readable representation for PM review."""
        op_label = _OPERATOR_LABELS.get(self.operator, self.operator.value)
        unit_suffix = f" {self.unit}" if self.unit else ""
        return f"{self.field} {op_label} {self.value}{unit_suffix}"


@dataclass(frozen=True, slots=True)
class ConditionalBehavior:
    """Behavioral guidance for the LLM when a condition branch is active.

    Attributes:
        tone: Tone descriptor ("warm acceptance", "polite refusal", etc.).
        instructions: Specific instructions injected into the system prompt.
        suggest_alternatives: Whether to suggest alternatives (for rejection branch).
        example_response: Optional example response for few-shot guidance.
    """

    tone: str = ""
    instructions: str = ""
    suggest_alternatives: bool = False
    example_response: str = ""

    def to_prompt_block(self) -> str:
        """Render as a block for system prompt injection."""
        parts: list[str] = []
        if self.tone:
            parts.append(f"Tone: {self.tone}")
        if self.instructions:
            parts.append(self.instructions)
        if self.suggest_alternatives:
            parts.append("Suggest alternatives to the guest.")
        if self.example_response:
            parts.append(f"Example: {self.example_response}")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class CompositeRule:
    """A rule combining a numeric condition with two behavioral branches.

    Attributes:
        rule_id: Unique identifier.
        name: Human-readable rule name (e.g. "baby_crib_by_amount").
        description: What this rule does and why.
        intent_trigger: The guest intent that activates this rule
            (e.g. "baby_crib_request").
        condition: The condition to evaluate against PMS data.
        behavior_if_true: LLM guidance when condition is met.
        behavior_if_false: LLM guidance when condition is not met.
        property_id: Scope — empty means all properties.
        source: How this rule was created ("rule_creator", "manual", etc.).
        confidence: Confidence in this rule (0-1), from threshold inference.
        created_at: ISO timestamp.
        tags: Searchable tags.
        active: Whether this rule is currently active.
    """

    rule_id: str = ""
    name: str = ""
    description: str = ""
    intent_trigger: str = ""
    condition: RuleCondition | None = None
    behavior_if_true: ConditionalBehavior = field(default_factory=ConditionalBehavior)
    behavior_if_false: ConditionalBehavior = field(default_factory=ConditionalBehavior)
    property_id: str = ""
    source: str = "manual"
    confidence: float = 1.0
    created_at: str = ""
    tags: tuple[str, ...] = ()
    active: bool = True

    def __post_init__(self) -> None:
        """Generate rule_id and created_at if not provided."""
        if not self.rule_id:
            object.__setattr__(self, "rule_id", f"CR-{uuid4().hex[:8].upper()}")
        if not self.created_at:
            object.__setattr__(
                self, "created_at",
                datetime.now(timezone.utc).isoformat(),
            )

    @property
    def display(self) -> str:
        """Human-readable summary for PM review."""
        cond = self.condition.display if self.condition else "no condition"
        return (
            f"[{self.name}] IF {cond} "
            f"THEN {self.behavior_if_true.tone or 'default'} "
            f"ELSE {self.behavior_if_false.tone or 'default'}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage in ProceduralMemory / Redis."""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "intent_trigger": self.intent_trigger,
            "condition": {
                "field": self.condition.field,
                "operator": self.condition.operator.value,
                "value": self.condition.value,
                "unit": self.condition.unit,
            } if self.condition else None,
            "behavior_if_true": {
                "tone": self.behavior_if_true.tone,
                "instructions": self.behavior_if_true.instructions,
                "suggest_alternatives": self.behavior_if_true.suggest_alternatives,
                "example_response": self.behavior_if_true.example_response,
            },
            "behavior_if_false": {
                "tone": self.behavior_if_false.tone,
                "instructions": self.behavior_if_false.instructions,
                "suggest_alternatives": self.behavior_if_false.suggest_alternatives,
                "example_response": self.behavior_if_false.example_response,
            },
            "property_id": self.property_id,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositeRule:
        """Deserialize from dict."""
        cond_data = data.get("condition")
        condition = None
        if cond_data:
            condition = RuleCondition(
                field=cond_data["field"],
                operator=ConditionOperator(cond_data["operator"]),
                value=cond_data["value"],
                unit=cond_data.get("unit", ""),
            )

        true_data = data.get("behavior_if_true", {})
        false_data = data.get("behavior_if_false", {})

        return cls(
            rule_id=data.get("rule_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            intent_trigger=data.get("intent_trigger", ""),
            condition=condition,
            behavior_if_true=ConditionalBehavior(**true_data),
            behavior_if_false=ConditionalBehavior(**false_data),
            property_id=data.get("property_id", ""),
            source=data.get("source", "manual"),
            confidence=data.get("confidence", 1.0),
            created_at=data.get("created_at", ""),
            tags=tuple(data.get("tags", ())),
            active=data.get("active", True),
        )
