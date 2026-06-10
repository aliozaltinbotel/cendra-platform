"""Skill middleware — injects active skills into prompts.

Bridges the skill system with the middleware pipeline. Adds
relevant skill instructions to the system prompt and enforces
tool restrictions when a skill is active.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest
from brain_engine.skills.injector import SkillInjector
from brain_engine.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillMiddleware:
    """Middleware that injects skills into LLM prompts.

    Uses the SkillInjector to build context-aware skill sections
    and optionally restricts tool access per-skill.

    Args:
        registry: Skill registry.
        max_skills: Maximum skills to inject per call.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        max_skills: int = 15,
    ) -> None:
        self._injector = SkillInjector(registry, max_skills=max_skills)
        self._registry = registry
        self._active_skill: str | None = None

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "skill"

    @property
    def active_skill(self) -> str | None:
        """Currently active skill name."""
        return self._active_skill

    def set_active_skill(self, skill_name: str | None) -> None:
        """Set the currently active skill.

        Args:
            skill_name: Skill name or None to clear.
        """
        self._active_skill = skill_name

    def get_tools(self) -> list[Tool]:
        """No additional tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """Build skill section for system prompt.

        Returns:
            Formatted skill prompt section.
        """
        return self._injector.build_skill_prompt()

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Inject skill instructions into system prompt.

        Args:
            messages: Current message list.

        Returns:
            Messages with skill section added.
        """
        skill_text = self._injector.build_skill_prompt()
        if not skill_text:
            return messages

        return _inject_skill_section(messages, skill_text)

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing."""
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Enforce tool restrictions for active skill.

        Args:
            request: Tool call request.
            handler: Next handler in chain.

        Returns:
            Tool result or restriction error.
        """
        if not self._is_tool_allowed(request.name):
            logger.warning(
                "[SkillMW] tool '%s' blocked by skill '%s'",
                request.name, self._active_skill,
            )
            return f"Error: tool '{request.name}' not allowed by active skill"

        return await handler(request)

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed by the active skill.

        Args:
            tool_name: Tool being invoked.

        Returns:
            True if allowed.
        """
        allowed = self._injector.get_allowed_tools(self._active_skill)
        if allowed is None:
            return True
        return tool_name in allowed


def _inject_skill_section(
    messages: list[dict[str, Any]],
    skill_text: str,
) -> list[dict[str, Any]]:
    """Inject skill section into the system message.

    Args:
        messages: Original messages.
        skill_text: Skill section text.

    Returns:
        Modified messages.
    """
    result = list(messages)
    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            result[i] = {
                **msg,
                "content": str(msg.get("content", "")) + f"\n\n{skill_text}",
            }
            return result

    result.insert(0, {"role": "system", "content": skill_text})
    return result
