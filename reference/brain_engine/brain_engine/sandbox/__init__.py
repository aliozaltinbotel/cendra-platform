"""Onboarding sandbox — example replies for unanswered guest threads.

The sandbox surfaces the threads where the most recent message is a
guest message still waiting on a PM response (Mümin's onboarding step
12).  For each such thread Brain Engine generates a candidate reply
that the PM can approve, edit, or discard during onboarding.

A generator Protocol keeps the real LLM plug-in optional; the default
:class:`TemplateExampleReplyGenerator` is deterministic so the
sandbox always returns *something* even when no LLM is wired.
"""

from __future__ import annotations

from brain_engine.sandbox.generator import (
    ExampleReplyGenerator,
    TemplateExampleReplyGenerator,
)
from brain_engine.sandbox.llm_generator import LLMExampleReplyGenerator
from brain_engine.sandbox.models import UnansweredThread
from brain_engine.sandbox.postgres_store import (
    PgUnansweredThreadStore,
    create_sandbox_pool,
)
from brain_engine.sandbox.readiness import (
    DEFAULT_SANDBOX_REQUIRED_ANSWERS,
    SandboxReadiness,
    SandboxReadinessService,
)
from brain_engine.sandbox.review_heuristics import classify_review_need
from brain_engine.sandbox.store import (
    InMemoryUnansweredThreadStore,
    UnansweredThreadStore,
)

__all__ = [
    "DEFAULT_SANDBOX_REQUIRED_ANSWERS",
    "ExampleReplyGenerator",
    "InMemoryUnansweredThreadStore",
    "LLMExampleReplyGenerator",
    "PgUnansweredThreadStore",
    "SandboxReadiness",
    "SandboxReadinessService",
    "TemplateExampleReplyGenerator",
    "UnansweredThread",
    "UnansweredThreadStore",
    "classify_review_need",
    "create_sandbox_pool",
]
