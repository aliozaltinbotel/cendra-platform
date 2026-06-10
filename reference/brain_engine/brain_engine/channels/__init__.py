"""Channel system for Brain Engine Pregel execution.

Provides typed state containers with custom update semantics
for the BSP (Bulk Synchronous Parallel) execution model.

Channel types:
- LastValue: Single scalar, single-writer-per-step
- Topic: Multi-value accumulator (pub/sub)
- BinaryOperatorAggregate: Reducer-based aggregation
- EphemeralValue: Single-use, clears every step

Example::

    from brain_engine.channels import LastValue, Topic, BinaryOperatorAggregate
    import operator

    channels = {
        "counter": LastValue(int, default=0),
        "messages": BinaryOperatorAggregate(list, operator.add, default=list),
        "events": Topic(str, accumulate=False),
    }
"""

from brain_engine.channels.base import (
    BaseChannel,
    EmptyChannelError,
    InvalidUpdateError,
)
from brain_engine.channels.binop import BinaryOperatorAggregate, Overwrite
from brain_engine.channels.ephemeral import EphemeralValue
from brain_engine.channels.last_value import LastValue
from brain_engine.channels.topic import Topic

__all__ = [
    "BaseChannel",
    "BinaryOperatorAggregate",
    "EmptyChannelError",
    "EphemeralValue",
    "InvalidUpdateError",
    "LastValue",
    "Overwrite",
    "Topic",
]
