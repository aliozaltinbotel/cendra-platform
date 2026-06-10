"""Message operations — trim, remove, filter, merge.

Provides utilities for managing message lists in LLM conversations:
token-based trimming, targeted removal, role-based filtering, and
consecutive message merging.

Based on: LangChain trim_messages, RemoveMessage, filter_messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RemoveMessage:
    """Marker for removing a specific message by ID.

    Used with ``add_messages`` reducer: when the reducer encounters
    a RemoveMessage, it removes the matching message instead of
    appending.

    Attributes:
        id: ID of the message to remove.
    """

    id: str


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 4096,
    token_counter: Callable[[str], int] | None = None,
    strategy: str = "last",
    include_system: bool = True,
    start_on: str | None = "human",
) -> list[dict[str, Any]]:
    """Trim a message list to fit within a token budget.

    Two strategies:
    - ``last``: Keep the most recent messages (default).
    - ``first``: Keep the earliest messages.

    System messages are always preserved when ``include_system``
    is True.

    Args:
        messages: Full message list.
        max_tokens: Maximum token budget.
        token_counter: Function that counts tokens in a string.
            Defaults to word-based approximation.
        strategy: ``"last"`` or ``"first"``.
        include_system: Whether to always keep system messages.
        start_on: Role that the result should start with.

    Returns:
        Trimmed message list within the token budget.
    """
    counter = token_counter or _approx_token_count
    system_msgs, non_system = _split_system(messages, include_system)
    budget = max_tokens - _count_messages_tokens(system_msgs, counter)

    if budget <= 0:
        return system_msgs

    if strategy == "first":
        trimmed = _trim_first(non_system, budget, counter)
    else:
        trimmed = _trim_last(non_system, budget, counter)

    if start_on:
        trimmed = _ensure_starts_with(trimmed, start_on)

    return system_msgs + trimmed


def filter_messages(
    messages: list[dict[str, Any]],
    *,
    include_roles: list[str] | None = None,
    exclude_roles: list[str] | None = None,
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter messages by role or type.

    Args:
        messages: Message list.
        include_roles: Only keep these roles.
        exclude_roles: Remove these roles.
        include_types: Only keep these message types.
        exclude_types: Remove these message types.

    Returns:
        Filtered message list.
    """
    result = list(messages)

    if include_roles:
        result = [m for m in result if m.get("role") in include_roles]
    if exclude_roles:
        result = [m for m in result if m.get("role") not in exclude_roles]
    if include_types:
        result = [m for m in result if m.get("type") in include_types]
    if exclude_types:
        result = [m for m in result if m.get("type") not in exclude_types]

    return result


def merge_message_runs(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge consecutive messages from the same role.

    Combines adjacent messages with the same role into a single
    message with concatenated content.

    Args:
        messages: Message list with possible consecutive same-role msgs.

    Returns:
        Message list with consecutive runs merged.
    """
    if not messages:
        return []

    merged: list[dict[str, Any]] = [_copy_message(messages[0])]

    for msg in messages[1:]:
        if msg.get("role") == merged[-1].get("role"):
            merged[-1] = _merge_two(merged[-1], msg)
        else:
            merged.append(_copy_message(msg))

    return merged


def apply_remove_messages(
    messages: list[dict[str, Any]],
    removals: list[RemoveMessage],
) -> list[dict[str, Any]]:
    """Apply RemoveMessage markers to a message list.

    Args:
        messages: Current message list.
        removals: RemoveMessage markers to apply.

    Returns:
        Message list with specified messages removed.
    """
    remove_ids = {r.id for r in removals}
    return [m for m in messages if m.get("id") not in remove_ids]


# ── Internal helpers ─────────────────────────────────────────────────── #


def _approx_token_count(text: str) -> int:
    """Approximate token count by word splitting.

    Args:
        text: Input text.

    Returns:
        Approximate token count (~1.3 tokens per word).
    """
    words = len(text.split())
    return int(words * 1.3)


def _split_system(
    messages: list[dict[str, Any]],
    include_system: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into system and non-system.

    Args:
        messages: Full message list.
        include_system: Whether to separate system messages.

    Returns:
        Tuple of (system_messages, non_system_messages).
    """
    if not include_system:
        return [], list(messages)
    system = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    return system, non_system


def _count_messages_tokens(
    messages: list[dict[str, Any]],
    counter: Callable[[str], int],
) -> int:
    """Count total tokens across messages.

    Args:
        messages: Messages to count.
        counter: Token counting function.

    Returns:
        Total token count.
    """
    return sum(counter(m.get("content", "")) for m in messages)


def _trim_last(
    messages: list[dict[str, Any]],
    budget: int,
    counter: Callable[[str], int],
) -> list[dict[str, Any]]:
    """Keep messages from the end within budget.

    Args:
        messages: Non-system messages.
        budget: Token budget.
        counter: Token counting function.

    Returns:
        Trimmed messages (most recent first within budget).
    """
    result: list[dict[str, Any]] = []
    used = 0
    for msg in reversed(messages):
        tokens = counter(msg.get("content", ""))
        if used + tokens > budget:
            break
        result.insert(0, msg)
        used += tokens
    return result


def _trim_first(
    messages: list[dict[str, Any]],
    budget: int,
    counter: Callable[[str], int],
) -> list[dict[str, Any]]:
    """Keep messages from the start within budget.

    Args:
        messages: Non-system messages.
        budget: Token budget.
        counter: Token counting function.

    Returns:
        Trimmed messages (earliest first within budget).
    """
    result: list[dict[str, Any]] = []
    used = 0
    for msg in messages:
        tokens = counter(msg.get("content", ""))
        if used + tokens > budget:
            break
        result.append(msg)
        used += tokens
    return result


def _ensure_starts_with(
    messages: list[dict[str, Any]],
    role: str,
) -> list[dict[str, Any]]:
    """Ensure the message list starts with a specific role.

    Drops leading messages until the desired role is found.

    Args:
        messages: Message list.
        role: Required starting role.

    Returns:
        Possibly shortened message list.
    """
    for i, msg in enumerate(messages):
        if msg.get("role") == role:
            return messages[i:]
    return messages


def _copy_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Create a shallow copy of a message dict.

    Args:
        msg: Message to copy.

    Returns:
        Copy of the message.
    """
    return dict(msg)


def _merge_two(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """Merge two same-role messages by concatenating content.

    Args:
        first: First message.
        second: Second message to merge into first.

    Returns:
        Merged message.
    """
    content_a = first.get("content", "")
    content_b = second.get("content", "")
    return {**first, "content": f"{content_a}\n{content_b}"}
