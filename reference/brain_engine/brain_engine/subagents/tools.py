"""Subagent tools — agent-callable tool for task delegation.

Provides the ``task`` tool that allows the parent agent to spawn
subagents for complex, multi-step, or parallelizable work.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.subagents.models import SubAgentResult
from brain_engine.subagents.registry import SubAgentRegistry
from brain_engine.subagents.runner import SubAgentRunner

logger = logging.getLogger(__name__)

TASK_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": (
                "Clear, detailed description of the task for the subagent. "
                "Include all necessary context so the subagent can work "
                "autonomously."
            ),
        },
        "subagent_type": {
            "type": "string",
            "description": "Name of the subagent type to use.",
        },
    },
    "required": ["description", "subagent_type"],
}

TASK_TOOL_DESCRIPTION_TEMPLATE = """\
Launch a subagent to handle a complex, multi-step task with an \
isolated context window.

Available subagent types:
{available_agents}

Usage notes:
- Launch multiple agents concurrently for independent tasks.
- Each invocation is stateless: single prompt in, single result back.
- Provide clear, detailed prompts so the subagent can work autonomously.
- The subagent's outputs should generally be trusted.
"""


def _build_task_handler(
    runner: SubAgentRunner,
) -> Any:
    """Create the task tool handler bound to a runner.

    Args:
        runner: SubAgentRunner instance.

    Returns:
        Async handler function.
    """

    async def handle_task(args: dict[str, Any]) -> dict[str, Any]:
        """Execute a subagent task.

        Args:
            args: Dict with ``description`` and ``subagent_type``.

        Returns:
            Dict with task result and metadata.
        """
        description = args["description"]
        subagent_type = args["subagent_type"]

        try:
            result = await runner.run(subagent_type, description)
        except KeyError as exc:
            return {"success": False, "error": str(exc)}

        return {
            "success": result.succeeded,
            "output": result.to_tool_message(),
            "task_id": result.task_id,
            "elapsed_ms": result.elapsed_ms,
            "subagent": result.subagent_name,
        }

    return handle_task


def task_tool(
    runner: SubAgentRunner,
    registry: SubAgentRegistry,
) -> dict[str, Any]:
    """Build the task tool definition for subagent delegation.

    Args:
        runner: SubAgentRunner for execution.
        registry: SubAgentRegistry for available types.

    Returns:
        Tool dict with name, description, parameters, and handler.
    """
    available = registry.build_tool_description()
    description = TASK_TOOL_DESCRIPTION_TEMPLATE.format(
        available_agents=available,
    )

    return {
        "name": "task",
        "description": description,
        "parameters": TASK_TOOL_SCHEMA,
        "handler": _build_task_handler(runner),
    }
