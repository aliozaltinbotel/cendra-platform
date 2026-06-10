"""Middleware system for Brain Engine pipeline processing.

Provides a protocol-based middleware stack where each middleware can
intercept model calls, tool calls, and prompt assembly. Middlewares
execute in order, with automatic snapshot support via BrainZFS.

Example::

    from brain_engine.middleware import MiddlewareStack
    from brain_engine.middleware.builtin.logging_mw import LoggingMiddleware

    stack = MiddlewareStack()
    stack.add(LoggingMiddleware())
    result = await stack.execute_pipeline(session_id, messages)
"""

from brain_engine.middleware.protocol import MiddlewareProtocol, Tool
from brain_engine.middleware.stack import MiddlewareStack

__all__ = [
    "MiddlewareProtocol",
    "MiddlewareStack",
    "Tool",
]
