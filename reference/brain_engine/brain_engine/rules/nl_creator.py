"""Natural language rule creator — converts plain text to CompositeRules.

Maps to the Cendra "Rule Creator" page where PMs describe rules in
natural language:
- "Auto-label high-value bookings"
- "Create a rule for late check-in handling"
- "Tag repeat customers as VIP"

The NL creator uses an LLM to parse the description into structured
CompositeRule components (condition, true/false behaviors), then
validates and returns the rule for PM review before activation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

import litellm

from brain_engine.rules.composite_rule import (
    CompositeRule,
    ConditionalBehavior,
    ConditionOperator,
    RuleCondition,
)

logger = logging.getLogger(__name__)

_MODEL: Final[str] = "gpt-4o"
_TEMPERATURE: Final[float] = 0.1
_MAX_TOKENS: Final[int] = 1500


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RuleCreationResult:
    """Result of natural-language rule creation.

    Attributes:
        success: Whether the rule was successfully parsed.
        rule: The created CompositeRule (None if parsing failed).
        explanation: Human-readable explanation of how the rule works.
        error: Error message if parsing failed.
        raw_llm_output: Raw LLM JSON output for debugging.
    """

    success: bool
    rule: CompositeRule | None = None
    explanation: str = ""
    error: str = ""
    raw_llm_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response.

        Returns:
            Dict with result fields and optional rule details.
        """
        result: dict[str, Any] = {
            "success": self.success,
            "explanation": self.explanation,
        }
        if self.rule is not None:
            result["rule"] = self.rule.to_dict()
            result["rule_display"] = self.rule.display
        if self.error:
            result["error"] = self.error
        return result


# ---------------------------------------------------------------------------
# System prompt for LLM parsing
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: Final[str] = """\
You are a rule parser for a vacation rental AI system. Convert the user's
natural language rule description into a structured JSON rule.

Output ONLY valid JSON with this exact structure:
{
  "name": "rule name (short, descriptive)",
  "description": "what this rule does",
  "intent_trigger": "the guest intent that triggers this rule (e.g. late_checkout, discount, booking_inquiry, early_checkin, general)",
  "condition": {
    "field": "PMS field to check (e.g. total_price, nights, adults, booking_source, season)",
    "operator": "gt|gte|lt|lte|eq|neq|in|not_in|contains",
    "value": "the threshold value (number or string)"
  },
  "behavior_if_true": {
    "tone": "warm|professional|firm|apologetic",
    "instructions": "what the AI should do when condition is true",
    "suggest_alternatives": false,
    "example_response": "optional example response"
  },
  "behavior_if_false": {
    "tone": "polite|professional|apologetic",
    "instructions": "what the AI should do when condition is false",
    "suggest_alternatives": true,
    "example_response": "optional example response"
  },
  "property_id": "",
  "tags": ["tag1", "tag2"]
}

Available condition fields:
- total_price (float), nights (int), adults (int), children (int)
- booking_source (string: airbnb, booking.com, direct, etc.)
- season (string: high, low, standard, holiday)
- payment_status (string: paid, pending, partial)
- is_repeat_guest (bool), id_verified (bool)
- lead_time_hours (float), adr (float)

If the user's description doesn't clearly map to a condition, use
field="total_price" operator="gte" value=0 as a catch-all (always true).
"""


# ---------------------------------------------------------------------------
# NaturalLanguageRuleCreator
# ---------------------------------------------------------------------------

class NaturalLanguageRuleCreator:
    """Converts natural language descriptions into CompositeRules.

    Uses an LLM to parse the description, then constructs a validated
    CompositeRule object that the PM can review before activation.

    Attributes:
        _model: LLM model identifier.
        _log: Logger instance.
    """

    def __init__(self, model: str = _MODEL) -> None:
        self._model = model
        self._log = logger

    async def create_rule(
        self,
        *,
        description: str,
        property_id: str = "",
        agent_id: str = "",
    ) -> RuleCreationResult:
        """Create a CompositeRule from a natural language description.

        Args:
            description: PM's natural language rule description
                (e.g. "Auto-label high-value bookings over $1000").
            property_id: Optional property scope for the rule.
            agent_id: Optional agent to link the rule to.

        Returns:
            RuleCreationResult with the parsed rule or error details.
        """
        if not description or not description.strip():
            return RuleCreationResult(
                success=False,
                error="Rule description cannot be empty.",
            )

        try:
            raw = await self._call_llm(description)
        except Exception as exc:
            self._log.error("NL rule creation LLM call failed: %s", exc)
            return RuleCreationResult(
                success=False,
                error=f"LLM parsing failed: {exc}",
            )

        parsed = self._parse_llm_output(raw)
        if parsed is None:
            return RuleCreationResult(
                success=False,
                error="Failed to parse LLM output as valid JSON.",
                raw_llm_output={"raw": raw},
            )

        rule = self._build_rule(parsed, property_id)
        if rule is None:
            return RuleCreationResult(
                success=False,
                error="Failed to construct rule from parsed output.",
                raw_llm_output=parsed,
            )

        explanation = self._build_explanation(rule, description)

        self._log.info(
            "NL rule created: %s (condition=%s %s %s)",
            rule.name,
            rule.condition.field,
            rule.condition.operator,
            rule.condition.value,
        )

        return RuleCreationResult(
            success=True,
            rule=rule,
            explanation=explanation,
            raw_llm_output=parsed,
        )

    # -------------------------------------------------------------------
    # LLM interaction
    # -------------------------------------------------------------------

    async def _call_llm(self, description: str) -> str:
        """Call LLM to parse a natural language rule description.

        Args:
            description: PM's rule description.

        Returns:
            Raw LLM response text.
        """
        response = await litellm.acompletion(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    # -------------------------------------------------------------------
    # Parsing and construction
    # -------------------------------------------------------------------

    def _parse_llm_output(self, raw: str) -> dict[str, Any] | None:
        """Parse raw LLM output as JSON.

        Args:
            raw: LLM response text.

        Returns:
            Parsed dict or None on failure.
        """
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self._log.warning("Failed to parse LLM JSON: %.200s", raw)
            return None

    def _build_rule(
        self,
        parsed: dict[str, Any],
        property_id: str,
    ) -> CompositeRule | None:
        """Construct a CompositeRule from parsed LLM output.

        Args:
            parsed: Parsed JSON from LLM.
            property_id: Property scope.

        Returns:
            CompositeRule or None if construction fails.
        """
        try:
            cond_data = parsed.get("condition", {})
            condition = RuleCondition(
                field=cond_data.get("field", "total_price"),
                operator=ConditionOperator(cond_data.get("operator", "gte")),
                value=cond_data.get("value", 0),
            )

            true_data = parsed.get("behavior_if_true", {})
            behavior_true = ConditionalBehavior(
                tone=true_data.get("tone", "professional"),
                instructions=true_data.get("instructions", ""),
                suggest_alternatives=true_data.get("suggest_alternatives", False),
                example_response=true_data.get("example_response", ""),
            )

            false_data = parsed.get("behavior_if_false", {})
            behavior_false = ConditionalBehavior(
                tone=false_data.get("tone", "polite"),
                instructions=false_data.get("instructions", ""),
                suggest_alternatives=false_data.get("suggest_alternatives", True),
                example_response=false_data.get("example_response", ""),
            )

            return CompositeRule(
                name=parsed.get("name", "Untitled Rule"),
                description=parsed.get("description", ""),
                intent_trigger=parsed.get("intent_trigger", "general"),
                condition=condition,
                behavior_if_true=behavior_true,
                behavior_if_false=behavior_false,
                property_id=property_id,
                source="nl_creator",
                tags=tuple(parsed.get("tags", [])),
            )
        except (ValueError, TypeError, KeyError) as exc:
            self._log.warning("Rule construction failed: %s", exc)
            return None

    def _build_explanation(
        self,
        rule: CompositeRule,
        original_description: str,
    ) -> str:
        """Generate a human-readable explanation of the created rule.

        Args:
            rule: The constructed CompositeRule.
            original_description: PM's original text.

        Returns:
            Explanation string for PM review.
        """
        return (
            f"Rule \"{rule.name}\" created from: \"{original_description}\"\n\n"
            f"Condition: When {rule.condition.display}\n"
            f"  If TRUE → {rule.behavior_if_true.tone} tone: "
            f"{rule.behavior_if_true.instructions}\n"
            f"  If FALSE → {rule.behavior_if_false.tone} tone: "
            f"{rule.behavior_if_false.instructions}"
        )
