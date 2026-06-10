"""Messages module — message management operations."""

from brain_engine.messages.ops import (
    RemoveMessage,
    filter_messages,
    merge_message_runs,
    trim_messages,
)

__all__ = [
    "RemoveMessage",
    "filter_messages",
    "merge_message_runs",
    "trim_messages",
]
