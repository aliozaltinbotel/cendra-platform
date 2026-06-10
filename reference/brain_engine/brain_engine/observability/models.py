"""Observability data models — run context, spans, and callback results.

Provides structured types for tracking execution spans, maintaining
parent/child run hierarchies, and collecting callback metadata.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunContext:
    """Tracks a single execution run within the callback hierarchy.

    Attributes:
        run_id: Unique identifier for this run.
        parent_run_id: Parent run ID (None for root).
        name: Human-readable run name.
        run_type: Category (llm, tool, agent, chain).
        tags: Propagated tags for filtering and routing.
        metadata: Arbitrary key-value metadata.
        start_time: Start timestamp (monotonic seconds).
    """

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_run_id: str | None = None
    name: str = ""
    run_type: str = "agent"
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.monotonic)

    def child(
        self,
        name: str,
        run_type: str,
        extra_tags: list[str] | None = None,
    ) -> RunContext:
        """Create a child run context inheriting tags and metadata.

        Args:
            name: Name for the child run.
            run_type: Type of the child run.
            extra_tags: Additional tags for the child.

        Returns:
            New RunContext linked to this parent.
        """
        merged_tags = list(self.tags)
        if extra_tags:
            merged_tags.extend(extra_tags)

        return RunContext(
            parent_run_id=self.run_id,
            name=name,
            run_type=run_type,
            tags=merged_tags,
            metadata=dict(self.metadata),
        )

    def elapsed_ms(self) -> int:
        """Elapsed milliseconds since run start.

        Returns:
            Integer milliseconds.
        """
        return int((time.monotonic() - self.start_time) * 1000)


@dataclass
class SpanRecord:
    """Completed execution span with timing and outcome.

    Attributes:
        run_id: Run identifier.
        parent_run_id: Parent run identifier.
        name: Span name.
        run_type: Span type.
        start_time: Start timestamp (monotonic).
        end_time: End timestamp (monotonic).
        duration_ms: Duration in milliseconds.
        status: Outcome status (ok, error).
        error: Error message if failed.
        inputs: Span input data.
        outputs: Span output data.
        tags: Propagated tags.
        metadata: Extra metadata.
    """

    run_id: str = ""
    parent_run_id: str | None = None
    name: str = ""
    run_type: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: int = 0
    status: str = "ok"
    error: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenMetrics:
    """Token usage metrics for a single LLM call.

    Attributes:
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        total_tokens: Sum of prompt + completion.
        model: Model identifier.
        cost_usd: Estimated cost in USD.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0


def build_span_from_context(
    ctx: RunContext,
    status: str = "ok",
    error: str = "",
    outputs: dict[str, Any] | None = None,
) -> SpanRecord:
    """Build a completed SpanRecord from a RunContext.

    Args:
        ctx: The run context to finalize.
        status: Outcome status.
        error: Error message if any.
        outputs: Output data.

    Returns:
        Completed SpanRecord.
    """
    end_time = time.monotonic()
    return SpanRecord(
        run_id=ctx.run_id,
        parent_run_id=ctx.parent_run_id,
        name=ctx.name,
        run_type=ctx.run_type,
        start_time=ctx.start_time,
        end_time=end_time,
        duration_ms=int((end_time - ctx.start_time) * 1000),
        status=status,
        error=error,
        outputs=outputs or {},
        tags=list(ctx.tags),
        metadata=dict(ctx.metadata),
    )
