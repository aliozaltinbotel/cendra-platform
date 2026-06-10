"""Subagents module — task delegation and parallel execution.

Enables the Brain Engine agent to spawn isolated subagents for
complex multi-step tasks. Each subagent runs in a BrainZFS clone
with its own context, preventing state pollution between parallel
tasks.

Components:
    - SubAgentSpec: Declarative subagent definition.
    - SubAgentRunner: Spawns and manages subagent execution.
    - SubAgentRegistry: Registers and discovers available subagents.
    - task_tool: Agent-callable tool for delegating work.
"""

from brain_engine.subagents.models import (
    SubAgentResult,
    SubAgentSpec,
    SubAgentStatus,
)
from brain_engine.subagents.registry import SubAgentRegistry
from brain_engine.subagents.runner import SubAgentRunner
from brain_engine.subagents.tools import task_tool

__all__ = [
    "SubAgentRegistry",
    "SubAgentResult",
    "SubAgentRunner",
    "SubAgentSpec",
    "SubAgentStatus",
    "task_tool",
]
