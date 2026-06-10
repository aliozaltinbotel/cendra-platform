"""Tracing Callback — structured logging with timing for all events.

Records SpanRecords for every LLM call, tool call, and agent action.
Provides a queryable trace log for debugging and performance analysis.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from brain_engine.observability.models import (
    RunContext,
    SpanRecord,
    build_span_from_context,
)

logger = logging.getLogger(__name__)


class TracingCallback:
    """Records structured trace spans for all observability events.

    Maintains an in-memory list of SpanRecords that can be queried
    by run_id, run_type, or status.

    Attributes:
        name: Callback identifier.
    """

    name: str = "tracing"

    def __init__(self) -> None:
        """Initialize TracingCallback."""
        self._spans: list[SpanRecord] = []
        self._active: dict[str, RunContext] = {}

    @property
    def spans(self) -> list[SpanRecord]:
        """All recorded spans (read-only copy)."""
        return list(self._spans)

    @property
    def span_count(self) -> int:
        """Number of recorded spans."""
        return len(self._spans)

    # ── LLM hooks ─────────────────────────────────────────────────────

    async def on_llm_start(
        self,
        ctx: RunContext,
        prompts: list[dict[str, str]],
        **kwargs: Any,
    ) -> None:
        """Record LLM call start.

        Args:
            ctx: Run context.
            prompts: Input messages.
            **kwargs: Model parameters.
        """
        self._active[ctx.run_id] = ctx
        logger.debug(
            "[trace] llm_start run=%s parent=%s",
            ctx.run_id[:8], (ctx.parent_run_id or "root")[:8],
        )

    async def on_llm_end(
        self,
        ctx: RunContext,
        response: Any,
        **kwargs: Any,
    ) -> None:
        """Record LLM call completion.

        Args:
            ctx: Run context.
            response: Model response.
            **kwargs: Additional data.
        """
        span = self._finalize_span(ctx, "ok")
        if span:
            span.outputs = {"response_type": type(response).__name__}

    async def on_llm_error(
        self,
        ctx: RunContext,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Record LLM call failure.

        Args:
            ctx: Run context.
            error: Exception raised.
            **kwargs: Additional data.
        """
        self._finalize_span(ctx, "error", str(error))

    async def on_llm_new_token(
        self,
        ctx: RunContext,
        token: str,
        **kwargs: Any,
    ) -> None:
        """Track streaming tokens (no span, just logging).

        Args:
            ctx: Run context.
            token: Streamed token.
            **kwargs: Additional data.
        """
        logger.debug("[trace] token run=%s len=%d", ctx.run_id[:8], len(token))

    # ── Tool hooks ────────────────────────────────────────────────────

    async def on_tool_start(
        self,
        ctx: RunContext,
        tool_name: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Record tool call start.

        Args:
            ctx: Run context.
            tool_name: Tool being called.
            tool_input: Tool arguments.
            **kwargs: Additional data.
        """
        self._active[ctx.run_id] = ctx
        logger.debug("[trace] tool_start run=%s tool=%s", ctx.run_id[:8], tool_name)

    async def on_tool_end(
        self,
        ctx: RunContext,
        tool_name: str,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Record tool call completion.

        Args:
            ctx: Run context.
            tool_name: Tool name.
            output: Tool output.
            **kwargs: Additional data.
        """
        span = self._finalize_span(ctx, "ok")
        if span:
            span.outputs = {"tool": tool_name, "output_len": len(output)}

    async def on_tool_error(
        self,
        ctx: RunContext,
        tool_name: str,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Record tool call failure.

        Args:
            ctx: Run context.
            tool_name: Tool name.
            error: Exception raised.
            **kwargs: Additional data.
        """
        self._finalize_span(ctx, "error", str(error))

    # ── Agent hooks ───────────────────────────────────────────────────

    async def on_agent_action(
        self,
        ctx: RunContext,
        action: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Record agent action decision as a span.

        Args:
            ctx: Run context.
            action: Chosen action.
            tool_input: Action input.
            **kwargs: Additional data.
        """
        span = build_span_from_context(ctx, "ok")
        span.outputs = {"action": action}
        self._spans.append(span)
        logger.debug("[trace] agent_action run=%s action=%s", ctx.run_id[:8], action)

    async def on_agent_finish(
        self,
        ctx: RunContext,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Record agent completion.

        Args:
            ctx: Run context.
            output: Final output.
            **kwargs: Additional data.
        """
        span = self._finalize_span(ctx, "ok")
        if span:
            span.outputs = {"output_len": len(output)}

    # ── Query methods ─────────────────────────────────────────────────

    def find_by_run_id(self, run_id: str) -> SpanRecord | None:
        """Find a span by its run ID.

        Args:
            run_id: Run identifier.

        Returns:
            SpanRecord or None.
        """
        for span in self._spans:
            if span.run_id == run_id:
                return span
        return None

    def find_by_type(self, run_type: str) -> list[SpanRecord]:
        """Find all spans of a given type.

        Args:
            run_type: Type filter (llm, tool, agent).

        Returns:
            Matching spans.
        """
        return [s for s in self._spans if s.run_type == run_type]

    def find_errors(self) -> list[SpanRecord]:
        """Find all spans with error status.

        Returns:
            Error spans.
        """
        return [s for s in self._spans if s.status == "error"]

    def clear(self) -> None:
        """Clear all recorded spans and active contexts."""
        self._spans.clear()
        self._active.clear()

    # ── Internal ──────────────────────────────────────────────────────

    def _finalize_span(
        self,
        ctx: RunContext,
        status: str,
        error: str = "",
    ) -> SpanRecord | None:
        """Finalize an active span and add to recorded spans.

        Args:
            ctx: Run context.
            status: Outcome status.
            error: Error message if any.

        Returns:
            The created SpanRecord or None if not active.
        """
        self._active.pop(ctx.run_id, None)
        span = build_span_from_context(ctx, status, error)
        self._spans.append(span)
        return span
