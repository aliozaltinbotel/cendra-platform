"""Observability system for Brain Engine — callbacks, tracing, and metrics.

Provides a LangChain-inspired callback hierarchy with parent/child run
ID tracking, structured trace spans, and aggregated metrics collection.

Example::

    from brain_engine.observability import (
        CallbackManager, TracingCallback, MetricsCallback,
    )

    tracing = TracingCallback()
    metrics = MetricsCallback()
    manager = CallbackManager(callbacks=[tracing, metrics])

    ctx = manager.create_run_context("my_agent", "agent")
    await manager.on_llm_start(ctx, messages)
    await manager.on_llm_end(ctx, response)
"""

from brain_engine.observability.manager import CallbackManager
from brain_engine.observability.metrics import (
    AggregatedMetrics,
    MetricsCallback,
)
from brain_engine.observability.models import (
    RunContext,
    SpanRecord,
    TokenMetrics,
)
from brain_engine.observability.protocol import CallbackProtocol
from brain_engine.observability.tracing import TracingCallback

__all__ = [
    "AggregatedMetrics",
    "CallbackManager",
    "CallbackProtocol",
    "MetricsCallback",
    "RunContext",
    "SpanRecord",
    "TokenMetrics",
    "TracingCallback",
]
