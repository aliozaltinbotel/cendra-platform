"""InterruptConfig — per-tool interrupt configuration.

Defines which tools require human approval, what decisions are
allowed, and how the interrupt should be presented.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from brain_engine.interrupts.models import InterruptDecision


class InterruptPolicy(StrEnum):
    """When to interrupt for a tool."""

    ALWAYS = "always"
    NEVER = "never"
    CONDITIONAL = "conditional"


class InterruptConfig(BaseModel):
    """Per-tool interrupt configuration.

    Attributes:
        tool_name: Name of the tool this config applies to.
        policy: When to interrupt (always, never, conditional).
        allowed_decisions: Which decisions the human can make.
        description_template: Template for the interrupt description.
        condition: Optional condition function name for CONDITIONAL policy.
        timeout_seconds: How long to wait for human response.
    """

    tool_name: str
    policy: InterruptPolicy = InterruptPolicy.ALWAYS
    allowed_decisions: list[InterruptDecision] = Field(
        default_factory=lambda: [
            InterruptDecision.APPROVE,
            InterruptDecision.REJECT,
        ],
    )
    description_template: str = "Tool '{tool_name}' requires approval."
    condition: str = ""
    timeout_seconds: int = 3600

    def should_interrupt(self, tool_args: dict[str, Any] | None = None) -> bool:
        """Determine whether to interrupt for this tool call.

        Args:
            tool_args: The tool's arguments (for conditional evaluation).

        Returns:
            True if execution should be interrupted.
        """
        if self.policy == InterruptPolicy.ALWAYS:
            return True
        if self.policy == InterruptPolicy.NEVER:
            return False
        return False

    def format_description(self, tool_args: dict[str, Any] | None = None) -> str:
        """Format the interrupt description for the client.

        Args:
            tool_args: Tool arguments for template substitution.

        Returns:
            Formatted description string.
        """
        desc = self.description_template.replace(
            "{tool_name}", self.tool_name,
        )
        if tool_args:
            for key, val in tool_args.items():
                desc = desc.replace(f"{{{key}}}", str(val))
        return desc

    def is_decision_allowed(self, decision: InterruptDecision) -> bool:
        """Check if a decision is allowed by this config.

        Args:
            decision: The human's decision.

        Returns:
            True if the decision is in the allowed list.
        """
        return decision in self.allowed_decisions


def build_interrupt_configs(
    config_map: dict[str, bool | dict[str, Any]],
) -> dict[str, InterruptConfig]:
    """Build InterruptConfig objects from a simplified config map.

    Supports the DeepAgents/LangGraph shorthand format:
        {"edit_file": True, "execute": {"allowed_decisions": [...]}}

    Args:
        config_map: Dict mapping tool names to True/False/config dict.

    Returns:
        Dict mapping tool names to InterruptConfig objects.
    """
    configs: dict[str, InterruptConfig] = {}
    for tool_name, value in config_map.items():
        if isinstance(value, bool):
            policy = InterruptPolicy.ALWAYS if value else InterruptPolicy.NEVER
            configs[tool_name] = InterruptConfig(
                tool_name=tool_name,
                policy=policy,
            )
        elif isinstance(value, dict):
            allowed = value.get("allowed_decisions", ["approve", "reject"])
            decisions = [InterruptDecision(d) for d in allowed]
            configs[tool_name] = InterruptConfig(
                tool_name=tool_name,
                policy=InterruptPolicy.ALWAYS,
                allowed_decisions=decisions,
                timeout_seconds=value.get("timeout_seconds", 3600),
            )
    return configs
