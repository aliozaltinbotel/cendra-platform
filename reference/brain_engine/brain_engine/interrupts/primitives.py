"""Interrupt primitives — the interrupt() function and InterruptError.

The interrupt() function is the core primitive that pauses execution
and returns a value to the client. It works by raising an InterruptError
that is caught by the execution engine.
"""

from __future__ import annotations

import uuid
from typing import Any


class InterruptError(Exception):
    """Raised by interrupt() to pause execution.

    The execution engine catches this, saves state via checkpoint,
    and returns the interrupt value to the client. When the human
    responds, execution resumes from the interrupt point.

    Attributes:
        value: Data to send to the client.
        interrupt_id: Unique identifier for resumption matching.
        tool_name: Tool that triggered the interrupt.
        tool_args: Tool arguments at interrupt time.
    """

    def __init__(
        self,
        value: Any,
        interrupt_id: str | None = None,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        self.value = value
        self.interrupt_id = interrupt_id or str(uuid.uuid4())
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        super().__init__(f"Interrupt: {self.interrupt_id}")


def interrupt(
    value: Any,
    tool_name: str = "",
    tool_args: dict[str, Any] | None = None,
) -> None:
    """Pause execution and send a value to the client for human review.

    This function raises an InterruptError which is caught by the
    execution engine. The engine saves a checkpoint, returns the
    interrupt value to the client, and waits for a resume command.

    When the human responds via Command(resume=...), execution
    continues from after this interrupt() call.

    Args:
        value: Data to present to the human (action description,
            approval request, etc.).
        tool_name: Name of the tool that requires approval.
        tool_args: Tool arguments to show the human.

    Raises:
        InterruptError: Always raised to pause execution.

    Example::

        def risky_tool(args):
            interrupt(
                value={"action": "delete_file", "path": args["path"]},
                tool_name="delete_file",
                tool_args=args,
            )
            # Execution resumes here after human approves
            os.remove(args["path"])
    """
    raise InterruptError(
        value=value,
        tool_name=tool_name,
        tool_args=tool_args,
    )
