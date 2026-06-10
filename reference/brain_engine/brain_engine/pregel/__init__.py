"""Pregel BSP execution engine for Brain Engine.

Implements Bulk Synchronous Parallel execution with channel-based
state management. Nodes execute in parallel supersteps with
atomic state updates between steps.

Example::

    from brain_engine.pregel import PregelExecutor, PregelNode, Send
    from brain_engine.channels import LastValue, Topic

    nodes = {
        "classify": PregelNode(
            name="classify", func=classify_fn,
            channels=["input"], triggers=["input"],
        ),
    }
    channels = {"input": LastValue(str), "output": LastValue(str)}
    executor = PregelExecutor(nodes, channels)
    result = await executor.run({"input": "hello"})
"""

from brain_engine.pregel.algo import (
    apply_writes,
    finish_channels,
    prepare_next_tasks,
)
from brain_engine.pregel.executor import (
    PregelExecutor,
    PregelNode,
    PregelResult,
)
from brain_engine.pregel.read import read_available, read_channels
from brain_engine.pregel.types import (
    GraphInterrupt,
    Interrupt,
    PregelTask,
    Send,
    TaskWrites,
)
from brain_engine.pregel.write import ChannelWriteEntry, collect_writes

__all__ = [
    "ChannelWriteEntry",
    "GraphInterrupt",
    "Interrupt",
    "PregelExecutor",
    "PregelNode",
    "PregelResult",
    "PregelTask",
    "Send",
    "TaskWrites",
    "apply_writes",
    "collect_writes",
    "finish_channels",
    "prepare_next_tasks",
    "read_available",
    "read_channels",
]
