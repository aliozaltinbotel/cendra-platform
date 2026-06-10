"""Built-in middleware components for Brain Engine.

Provides ready-to-use middleware for logging, summarization,
tool call patching, guardrails, memory, skills, observability,
structured output, rate limiting, caching, and approval.
"""

from brain_engine.middleware.builtin.approval_mw import ApprovalMiddleware
from brain_engine.middleware.builtin.caching_mw import CachingMiddleware
from brain_engine.middleware.builtin.guardrail_mw import GuardrailMiddleware
from brain_engine.middleware.builtin.logging_mw import LoggingMiddleware
from brain_engine.middleware.builtin.memory_mw import MemoryMiddleware
from brain_engine.middleware.builtin.observability_mw import ObservabilityMiddleware
from brain_engine.middleware.builtin.patch_tool_calls import PatchToolCallsMiddleware
from brain_engine.middleware.builtin.rate_limit_mw import RateLimitMiddleware
from brain_engine.middleware.builtin.skill_mw import SkillMiddleware
from brain_engine.middleware.builtin.structured_output_mw import StructuredOutputMiddleware
from brain_engine.middleware.builtin.summarization import SummarizationMiddleware

__all__ = [
    "ApprovalMiddleware",
    "CachingMiddleware",
    "GuardrailMiddleware",
    "LoggingMiddleware",
    "MemoryMiddleware",
    "ObservabilityMiddleware",
    "PatchToolCallsMiddleware",
    "RateLimitMiddleware",
    "SkillMiddleware",
    "StructuredOutputMiddleware",
    "SummarizationMiddleware",
]
