"""Checkpoint persistence system for Brain Engine graphs.

Provides pluggable checkpoint backends for saving and restoring
graph state across executions. Supports in-memory, SQLite, and
PostgreSQL storage.

Example::

    from brain_engine.checkpointer import MemoryCheckpointer

    ckpt = MemoryCheckpointer()
    graph = StateGraph(MyState)
    # ... build graph ...
    app = graph.compile(checkpointer=ckpt)
    result = await app.ainvoke(input, config={"thread_id": "t1"})
"""

from brain_engine.checkpointer.base import BaseCheckpointer
from brain_engine.checkpointer.memory import MemoryCheckpointer
from brain_engine.checkpointer.models import Checkpoint, CheckpointTuple, StateSnapshot
from brain_engine.checkpointer.postgres import PostgresCheckpointer

__all__ = [
    "BaseCheckpointer",
    "Checkpoint",
    "CheckpointTuple",
    "MemoryCheckpointer",
    "PostgresCheckpointer",
    "StateSnapshot",
]
