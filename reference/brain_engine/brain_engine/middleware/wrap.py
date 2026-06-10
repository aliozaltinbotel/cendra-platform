"""Wrap-style middleware — intercept and transform LLM calls.

Unlike pre/post hooks, wrap middleware fully wraps the model call,
allowing interception, modification, caching, and short-circuiting.

Example::

    @wrap_model_call
    async def add_system_context(call, messages, **kwargs):
        messages = inject_property_context(messages)
        response = await call(messages, **kwargs)
        return audit_response(response)

Based on: LangChain wrap_model_call / awrap_model_call.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

ModelCallFn = Callable[..., Awaitable[Any]]
WrapperFn = Callable[
    [ModelCallFn, list[dict[str, str]]],
    Awaitable[Any],
]


def wrap_model_call(
    wrapper: WrapperFn,
) -> WrapperFn:
    """Decorate a function as a model call wrapper.

    The wrapper receives the original call function and messages,
    and must call ``await call(messages, **kwargs)`` to proceed.
    It can modify messages before and response after.

    Args:
        wrapper: Async function(call, messages, **kwargs) -> response.

    Returns:
        Decorated wrapper with metadata.
    """
    wrapper._is_wrap_middleware = True  # type: ignore[attr-defined]
    return wrapper


class WrapMiddleware:
    """Class-based wrap middleware for model calls.

    Provides a structured way to wrap LLM calls with full
    control over the request/response pipeline.

    Subclass and override ``wrap`` to implement custom logic.
    """

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return self.__class__.__name__

    async def wrap(
        self,
        call: ModelCallFn,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """Wrap a model call. Override in subclasses.

        Default implementation passes through unchanged.

        Args:
            call: The original async model call function.
            messages: Input messages.
            **kwargs: Additional model kwargs.

        Returns:
            Model response.
        """
        return await call(messages, **kwargs)


class WrapMiddlewareStack:
    """Stack of wrap-style middleware applied in order.

    Each wrapper wraps the next, creating a nested chain.
    The innermost wrapper calls the actual model.
    """

    def __init__(self) -> None:
        self._wrappers: list[WrapMiddleware | WrapperFn] = []

    def add(self, wrapper: WrapMiddleware | WrapperFn) -> None:
        """Add a wrapper to the stack.

        Args:
            wrapper: WrapMiddleware instance or decorated function.
        """
        self._wrappers.append(wrapper)

    async def execute(
        self,
        model_call: ModelCallFn,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """Execute the full wrap chain around a model call.

        Builds the chain inside-out: last added wraps outermost.

        Args:
            model_call: The actual model invoke function.
            messages: Input messages.
            **kwargs: Model kwargs.

        Returns:
            Final response after all wrappers.
        """
        chain = model_call
        for wrapper in reversed(self._wrappers):
            chain = _make_link(wrapper, chain)
        return await chain(messages, **kwargs)

    @property
    def size(self) -> int:
        """Number of wrappers in the stack."""
        return len(self._wrappers)


def _make_link(
    wrapper: WrapMiddleware | WrapperFn,
    next_call: ModelCallFn,
) -> ModelCallFn:
    """Create a chain link from a wrapper and next call.

    Args:
        wrapper: Middleware to apply.
        next_call: The next function in the chain.

    Returns:
        Async function that applies the wrapper.
    """
    if isinstance(wrapper, WrapMiddleware):
        async def _link(messages: list[dict[str, str]], **kw: Any) -> Any:
            return await wrapper.wrap(next_call, messages, **kw)
        return _link
    else:
        async def _fn_link(messages: list[dict[str, str]], **kw: Any) -> Any:
            return await wrapper(next_call, messages, **kw)
        return _fn_link
