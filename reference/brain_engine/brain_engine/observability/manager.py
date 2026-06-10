"""Callback Manager — ordered dispatch of observability events.

Manages registered callbacks and dispatches events with parent/child
run ID tracking. Each callback receives a RunContext that maintains
the full hierarchy of nested runs (agent → llm → tool).

Inspired by LangChain's CallbackManager with UUID-based run trees.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.observability.models import RunContext
from brain_engine.observability.protocol import CallbackProtocol

logger = logging.getLogger(__name__)


class CallbackManager:
    """Dispatches observability events to registered callbacks.

    Callbacks are invoked in registration order. Errors in one
    callback do not prevent others from being called.

    Args:
        callbacks: Initial list of callbacks to register.
        tags: Default tags propagated to all RunContexts.
        metadata: Default metadata propagated to all RunContexts.
    """

    def __init__(
        self,
        callbacks: list[CallbackProtocol] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._callbacks: list[CallbackProtocol] = list(callbacks or [])
        self._default_tags: list[str] = list(tags or [])
        self._default_metadata: dict[str, Any] = dict(metadata or {})

    @property
    def count(self) -> int:
        """Number of registered callbacks."""
        return len(self._callbacks)

    @property
    def names(self) -> list[str]:
        """Names of all registered callbacks."""
        return [cb.name for cb in self._callbacks]

    def add(self, callback: CallbackProtocol) -> None:
        """Register a new callback.

        Args:
            callback: Callback to add.
        """
        self._callbacks.append(callback)
        logger.debug("Added callback: %s", callback.name)

    def remove(self, name: str) -> bool:
        """Remove a callback by name.

        Args:
            name: Callback name to remove.

        Returns:
            True if removed.
        """
        for i, cb in enumerate(self._callbacks):
            if cb.name == name:
                self._callbacks.pop(i)
                return True
        return False

    def create_run_context(
        self,
        name: str,
        run_type: str = "agent",
        parent: RunContext | None = None,
        extra_tags: list[str] | None = None,
    ) -> RunContext:
        """Create a new RunContext with default tags/metadata.

        Args:
            name: Human-readable run name.
            run_type: Run category (agent, llm, tool, chain).
            parent: Parent context for hierarchy.
            extra_tags: Additional tags.

        Returns:
            New RunContext.
        """
        if parent:
            return parent.child(name, run_type, extra_tags)

        tags = list(self._default_tags)
        if extra_tags:
            tags.extend(extra_tags)

        return RunContext(
            name=name,
            run_type=run_type,
            tags=tags,
            metadata=dict(self._default_metadata),
        )

    # ── LLM hooks ─────────────────────────────────────────────────────

    async def on_llm_start(
        self,
        ctx: RunContext,
        prompts: list[dict[str, str]],
        **kwargs: Any,
    ) -> None:
        """Dispatch on_llm_start to all callbacks."""
        await self._dispatch("on_llm_start", ctx, prompts=prompts, **kwargs)

    async def on_llm_end(
        self,
        ctx: RunContext,
        response: Any,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_llm_end to all callbacks."""
        await self._dispatch("on_llm_end", ctx, response=response, **kwargs)

    async def on_llm_error(
        self,
        ctx: RunContext,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_llm_error to all callbacks."""
        await self._dispatch("on_llm_error", ctx, error=error, **kwargs)

    async def on_llm_new_token(
        self,
        ctx: RunContext,
        token: str,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_llm_new_token to all callbacks."""
        await self._dispatch("on_llm_new_token", ctx, token=token, **kwargs)

    # ── Tool hooks ────────────────────────────────────────────────────

    async def on_tool_start(
        self,
        ctx: RunContext,
        tool_name: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Dispatch on_tool_start to all callbacks."""
        await self._dispatch(
            "on_tool_start", ctx,
            tool_name=tool_name, tool_input=tool_input, **kwargs,
        )

    async def on_tool_end(
        self,
        ctx: RunContext,
        tool_name: str,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_tool_end to all callbacks."""
        await self._dispatch(
            "on_tool_end", ctx,
            tool_name=tool_name, output=output, **kwargs,
        )

    async def on_tool_error(
        self,
        ctx: RunContext,
        tool_name: str,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_tool_error to all callbacks."""
        await self._dispatch(
            "on_tool_error", ctx,
            tool_name=tool_name, error=error, **kwargs,
        )

    # ── Agent hooks ───────────────────────────────────────────────────

    async def on_agent_action(
        self,
        ctx: RunContext,
        action: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Dispatch on_agent_action to all callbacks."""
        await self._dispatch(
            "on_agent_action", ctx,
            action=action, tool_input=tool_input, **kwargs,
        )

    async def on_agent_finish(
        self,
        ctx: RunContext,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Dispatch on_agent_finish to all callbacks."""
        await self._dispatch(
            "on_agent_finish", ctx, output=output, **kwargs,
        )

    # ── Internal dispatch ─────────────────────────────────────────────

    async def _dispatch(
        self,
        hook_name: str,
        ctx: RunContext,
        **kwargs: Any,
    ) -> None:
        """Call a named hook on all callbacks, catching errors.

        Args:
            hook_name: Method name to invoke.
            ctx: Run context.
            **kwargs: Hook-specific arguments.
        """
        for cb in self._callbacks:
            await _safe_call(cb, hook_name, ctx, **kwargs)


async def _safe_call(
    callback: CallbackProtocol,
    hook_name: str,
    ctx: RunContext,
    **kwargs: Any,
) -> None:
    """Safely invoke a hook on a callback, logging errors.

    Args:
        callback: The callback instance.
        hook_name: Method name.
        ctx: Run context.
        **kwargs: Hook arguments.
    """
    method = getattr(callback, hook_name, None)
    if method is None:
        return

    try:
        await method(ctx, **kwargs)
    except Exception:
        logger.warning(
            "Callback %s.%s failed",
            callback.name, hook_name,
            exc_info=True,
        )
