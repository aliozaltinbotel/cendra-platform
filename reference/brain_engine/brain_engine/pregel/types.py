"""Pregel types — Send, GraphInterrupt, and related primitives.

Defines the core communication types for the BSP execution model:
Send for dynamic fan-out, GraphInterrupt for pausable execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Send:
    """Dynamically route execution to a node with custom input.

    Used for fan-out patterns: a node can return multiple Send
    objects to spawn parallel executions in the next superstep.

    Attributes:
        node: Target node name.
        arg: State/input to pass to that node.
    """

    node: str
    arg: Any = None


class GraphInterrupt(Exception):
    """Raised to pause graph execution for human input.

    Carries a value that is returned to the client. Execution
    can resume via ``Command(resume=...)``.

    Attributes:
        value: Data to communicate to the client.
        interrupts: List of Interrupt objects for multi-interrupt.
        resumable: Whether this interrupt supports resumption.
        node: The node that caused the interrupt.
        interrupt_type: Origin — ``before``, ``after``, or ``explicit``.
    """

    def __init__(
        self,
        value: Any = None,
        *,
        node: str = "",
        interrupt_type: str = "explicit",
    ) -> None:
        self.value = value
        self.interrupts: list[Interrupt] = []
        self.resumable: bool = True
        self.node = node
        self.interrupt_type = interrupt_type
        super().__init__(str(value))

    def add_interrupt(self, interrupt: Interrupt) -> None:
        """Register an interrupt detail.

        Args:
            interrupt: Interrupt to add.
        """
        self.interrupts.append(interrupt)


@dataclass(slots=True)
class Interrupt:
    """Information about a single interrupt within a node.

    Attributes:
        value: Data communicated to the user.
        interrupt_id: Unique ID for resume matching.
        node: Node that raised the interrupt.
        interrupt_type: Origin — ``before``, ``after``, or ``explicit``.
    """

    value: Any = None
    interrupt_id: str = ""
    node: str = ""
    interrupt_type: str = "explicit"


@dataclass(frozen=True, slots=True)
class Command:
    """Resume command for interrupted graph execution.

    Attributes:
        resume: Value to inject when resuming.
        goto: Optional node to jump to on resume.
    """

    resume: Any = None
    goto: str | None = None


@dataclass
class PregelTask:
    """A planned task for one superstep (non-executable).

    Attributes:
        task_id: Unique task identifier.
        node: Node name to execute.
        input_data: Input for the node.
        triggers: Channels that triggered this task.
    """

    task_id: str = ""
    node: str = ""
    input_data: Any = None
    triggers: list[str] = field(default_factory=list)


@dataclass
class TaskWrites:
    """Collected writes from a completed task.

    Attributes:
        node: Node that produced the writes.
        writes: List of (channel_name, value) pairs.
        sends: List of Send objects for fan-out.
    """

    node: str = ""
    writes: list[tuple[str, Any]] = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
