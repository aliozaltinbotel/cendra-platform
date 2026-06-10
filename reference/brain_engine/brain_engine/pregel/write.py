"""Channel Write — queues node output to channels.

Writes are buffered during node execution and applied atomically
at the end of each superstep by apply_writes(). This ensures all
nodes in a superstep see consistent channel state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brain_engine.pregel.types import Send, TaskWrites


@dataclass(frozen=True, slots=True)
class ChannelWriteEntry:
    """Specification for a single channel write.

    Attributes:
        channel: Target channel name.
        value: Value to write.
        skip_none: Whether to skip if value is None.
    """

    channel: str
    value: Any = None
    skip_none: bool = False


def collect_writes(
    node_name: str,
    output: Any,
    write_specs: list[ChannelWriteEntry],
) -> TaskWrites:
    """Collect channel writes from a node's output.

    Processes write specifications against the node output,
    separating regular writes from Send fan-outs.

    Args:
        node_name: Name of the producing node.
        output: Raw node output (dict or single value).
        write_specs: Write specifications for this node.

    Returns:
        TaskWrites with writes and sends.
    """
    writes: list[tuple[str, Any]] = []
    sends: list[Send] = []

    if isinstance(output, Send):
        sends.append(output)
        return TaskWrites(node=node_name, writes=writes, sends=sends)

    if isinstance(output, list) and output and isinstance(output[0], Send):
        sends.extend(output)
        return TaskWrites(node=node_name, writes=writes, sends=sends)

    for spec in write_specs:
        value = _resolve_value(spec, output)
        if value is _UNSET:
            continue
        if spec.skip_none and value is None:
            continue
        writes.append((spec.channel, value))

    return TaskWrites(node=node_name, writes=writes, sends=sends)


_UNSET = object()


def _resolve_value(spec: ChannelWriteEntry, output: Any) -> Any:
    """Resolve the write value from spec and node output.

    Args:
        spec: Write specification.
        output: Node output.

    Returns:
        Resolved value or _UNSET if key not in output.
    """
    if spec.value is not None:
        return spec.value

    if isinstance(output, dict):
        return output.get(spec.channel, _UNSET)

    return output
