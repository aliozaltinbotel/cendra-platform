"""Metrics Callback — collects latency, token usage, and cost data.

Aggregates quantitative metrics across all LLM and tool calls for
dashboards, alerting, and cost tracking. Integrates with the
CallbackManager as a standard callback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from brain_engine.observability.models import RunContext, TokenMetrics

# Cost per 1M tokens (USD) — update as pricing changes
_COST_TABLE: list[tuple[str, float, float]] = [
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-4-turbo", 10.00, 30.00),
    ("claude-3-opus", 15.00, 75.00),
    ("claude-3-sonnet", 3.00, 15.00),
    ("claude-3-haiku", 0.25, 1.25),
]


@dataclass
class AggregatedMetrics:
    """Aggregated metrics snapshot.

    Attributes:
        total_llm_calls: Total LLM invocations.
        total_tool_calls: Total tool invocations.
        total_tokens: Cumulative token count.
        total_cost_usd: Cumulative estimated cost.
        avg_llm_latency_ms: Average LLM call latency.
        avg_tool_latency_ms: Average tool call latency.
        error_count: Total errors across all calls.
        token_details: Per-call token breakdowns.
    """

    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_llm_latency_ms: float = 0.0
    avg_tool_latency_ms: float = 0.0
    error_count: int = 0
    token_details: list[TokenMetrics] = field(default_factory=list)


class MetricsCallback:
    """Collects quantitative metrics from all observability events.

    Tracks latency, token usage, cost estimates, and error rates.

    Attributes:
        name: Callback identifier.
    """

    name: str = "metrics"

    def __init__(self) -> None:
        """Initialize MetricsCallback."""
        self._llm_latencies: list[float] = []
        self._tool_latencies: list[float] = []
        self._token_records: list[TokenMetrics] = []
        self._error_count: int = 0
        self._llm_starts: dict[str, float] = {}
        self._tool_starts: dict[str, float] = {}

    # ── LLM hooks ─────────────────────────────────────────────────────

    async def on_llm_start(
        self,
        ctx: RunContext,
        prompts: list[dict[str, str]],
        **kwargs: Any,
    ) -> None:
        """Record LLM call start time.

        Args:
            ctx: Run context.
            prompts: Input messages.
            **kwargs: Model parameters.
        """
        self._llm_starts[ctx.run_id] = time.monotonic()

    async def on_llm_end(
        self,
        ctx: RunContext,
        response: Any,
        **kwargs: Any,
    ) -> None:
        """Record LLM call latency and token usage.

        Args:
            ctx: Run context.
            response: Model response (may contain usage data).
            **kwargs: Additional data.
        """
        latency = self._calc_latency(self._llm_starts, ctx.run_id)
        self._llm_latencies.append(latency)

        token_metrics = _extract_token_metrics(response, kwargs)
        if token_metrics.total_tokens > 0:
            self._token_records.append(token_metrics)

    async def on_llm_error(
        self,
        ctx: RunContext,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Record LLM error.

        Args:
            ctx: Run context.
            error: Exception raised.
            **kwargs: Additional data.
        """
        self._error_count += 1
        latency = self._calc_latency(self._llm_starts, ctx.run_id)
        self._llm_latencies.append(latency)

    async def on_llm_new_token(
        self,
        ctx: RunContext,
        token: str,
        **kwargs: Any,
    ) -> None:
        """No-op for streaming tokens in metrics.

        Args:
            ctx: Run context.
            token: Streamed token.
            **kwargs: Additional data.
        """

    # ── Tool hooks ────────────────────────────────────────────────────

    async def on_tool_start(
        self,
        ctx: RunContext,
        tool_name: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Record tool call start time.

        Args:
            ctx: Run context.
            tool_name: Tool name.
            tool_input: Tool arguments.
            **kwargs: Additional data.
        """
        self._tool_starts[ctx.run_id] = time.monotonic()

    async def on_tool_end(
        self,
        ctx: RunContext,
        tool_name: str,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Record tool call latency.

        Args:
            ctx: Run context.
            tool_name: Tool name.
            output: Tool output.
            **kwargs: Additional data.
        """
        latency = self._calc_latency(self._tool_starts, ctx.run_id)
        self._tool_latencies.append(latency)

    async def on_tool_error(
        self,
        ctx: RunContext,
        tool_name: str,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Record tool call error and latency.

        Args:
            ctx: Run context.
            tool_name: Tool name.
            error: Exception raised.
            **kwargs: Additional data.
        """
        self._error_count += 1
        latency = self._calc_latency(self._tool_starts, ctx.run_id)
        self._tool_latencies.append(latency)

    # ── Agent hooks (pass-through) ────────────────────────────────────

    async def on_agent_action(
        self, ctx: RunContext, action: str,
        tool_input: dict[str, Any], **kwargs: Any,
    ) -> None:
        """No-op — agent actions are tracked via tool hooks."""

    async def on_agent_finish(
        self, ctx: RunContext, output: str, **kwargs: Any,
    ) -> None:
        """No-op — agent finish does not generate metrics."""

    # ── Aggregation ───────────────────────────────────────────────────

    def get_metrics(self) -> AggregatedMetrics:
        """Build an aggregated metrics snapshot.

        Returns:
            AggregatedMetrics with all collected data.
        """
        total_tokens = sum(t.total_tokens for t in self._token_records)
        total_cost = sum(t.cost_usd for t in self._token_records)

        return AggregatedMetrics(
            total_llm_calls=len(self._llm_latencies),
            total_tool_calls=len(self._tool_latencies),
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            avg_llm_latency_ms=_safe_avg(self._llm_latencies),
            avg_tool_latency_ms=_safe_avg(self._tool_latencies),
            error_count=self._error_count,
            token_details=list(self._token_records),
        )

    def reset(self) -> None:
        """Reset all collected metrics."""
        self._llm_latencies.clear()
        self._tool_latencies.clear()
        self._token_records.clear()
        self._error_count = 0
        self._llm_starts.clear()
        self._tool_starts.clear()

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _calc_latency(
        starts: dict[str, float],
        run_id: str,
    ) -> float:
        """Calculate latency in ms from a start record.

        Args:
            starts: Dict of run_id -> start timestamp.
            run_id: Run to calculate for.

        Returns:
            Latency in milliseconds.
        """
        start = starts.pop(run_id, None)
        if start is None:
            return 0.0
        return (time.monotonic() - start) * 1000


# ── Helpers ───────────────────────────────────────────────────────── #


def _extract_token_metrics(
    response: Any,
    kwargs: dict[str, Any],
) -> TokenMetrics:
    """Extract token usage from an LLM response.

    Args:
        response: Model response object.
        kwargs: Additional callback kwargs.

    Returns:
        TokenMetrics (may have zero values if not available).
    """
    usage = _get_usage_dict(response, kwargs)
    model = kwargs.get("model", getattr(response, "model", ""))

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total = prompt_tokens + completion_tokens

    return TokenMetrics(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        model=model,
        cost_usd=_estimate_cost(model, prompt_tokens, completion_tokens),
    )


def _get_usage_dict(
    response: Any,
    kwargs: dict[str, Any],
) -> dict[str, int]:
    """Extract usage dict from response or kwargs.

    Args:
        response: Model response.
        kwargs: Additional data.

    Returns:
        Usage dict with token counts.
    """
    if hasattr(response, "usage") and response.usage:
        usage = response.usage
        if isinstance(usage, dict):
            return usage
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
        }

    return kwargs.get("usage", {})


def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate USD cost for a model call.

    Args:
        model: Model identifier.
        prompt_tokens: Input tokens.
        completion_tokens: Output tokens.

    Returns:
        Estimated cost in USD.
    """
    for key, input_rate, output_rate in _COST_TABLE:
        if key in model.lower():
            input_cost = (prompt_tokens / 1_000_000) * input_rate
            output_cost = (completion_tokens / 1_000_000) * output_rate
            return round(input_cost + output_cost, 6)
    return 0.0


def _safe_avg(values: list[float]) -> float:
    """Calculate average, returning 0.0 for empty lists.

    Args:
        values: Numeric values.

    Returns:
        Average or 0.0.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)
