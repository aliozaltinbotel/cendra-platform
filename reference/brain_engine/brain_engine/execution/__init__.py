"""Execution module — main agent loop and runtime context.

Provides the core execution engine that orchestrates the agent's
think-act-observe cycle. Integrates with interrupts, middleware,
checkpointing, and streaming.

Components:
    - ExecutionEngine: Main agent loop with superstep execution.
    - AgentAction / AgentFinish: Typed step results.
    - Runtime: Context injection (store, stream, config).
    - RetryPolicy / CachePolicy: Per-step execution policies.
    - StepCollector: Intermediate steps tracking.
"""

from brain_engine.execution.models import (
    AgentAction,
    AgentFinish,
    AgentStep,
    ExecutionConfig,
    ExecutionResult,
    StepType,
)
from brain_engine.execution.engine import ExecutionEngine
from brain_engine.execution.policies import CachePolicy, RetryPolicy
from brain_engine.execution.runtime import Runtime
from brain_engine.execution.steps import StepCollector

__all__ = [
    "AgentAction",
    "AgentFinish",
    "AgentStep",
    "CachePolicy",
    "ExecutionConfig",
    "ExecutionEngine",
    "ExecutionResult",
    "RetryPolicy",
    "Runtime",
    "StepCollector",
    "StepType",
]
