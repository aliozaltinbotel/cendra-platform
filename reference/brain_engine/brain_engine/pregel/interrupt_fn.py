"""In-node interrupt function for human-in-the-loop workflows.

Provides the ``interrupt()`` function that can be called from inside
a graph node to pause execution and request human input.

Example::

    async def approval_node(state: dict) -> dict:
        decision = interrupt(
            value="Approve refund of $150?",
            node="approval_node",
        )
        # After resume, 'decision' contains the human's response
        return {"approved": decision}

Based on: LangGraph ``interrupt()`` (langgraph/types.py).
"""

from __future__ import annotations

import uuid
from typing import Any

from brain_engine.pregel.types import GraphInterrupt, Interrupt


def interrupt(
    value: Any = None,
    *,
    node: str = "",
) -> Any:
    """Pause graph execution and request human input.

    Raises ``GraphInterrupt`` which is caught by the executor.
    When the graph is resumed via ``Command(resume=...)``, the
    resume value is returned by this function.

    Must be called from inside a graph node function.

    Args:
        value: Data to show to the human (question, context, etc.).
        node: Node name for interrupt tracking.

    Returns:
        The resume value provided by the human (only after resume).

    Raises:
        GraphInterrupt: Always raised to pause execution.
    """
    interrupt_id = str(uuid.uuid4())[:12]

    exc = GraphInterrupt(
        value=value,
        node=node,
        interrupt_type="explicit",
    )
    exc.add_interrupt(Interrupt(
        value=value,
        interrupt_id=interrupt_id,
        node=node,
        interrupt_type="explicit",
    ))
    raise exc
