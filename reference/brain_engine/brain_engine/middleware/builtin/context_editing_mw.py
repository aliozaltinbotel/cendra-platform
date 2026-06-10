"""ContextEditingMiddleware — manage token budget by clearing old tool uses.

Monitors conversation token count and removes older tool call/result
pairs when a threshold is exceeded. Keeps the N most recent tool
interactions to preserve relevant context.

Example::

    mw = ContextEditingMiddleware(
        edits=[
            ClearToolUsesEdit(
                trigger=100000,
                keep=3,
                clear_tool_inputs=False,
                placeholder="[cleared]",
            ),
        ],
    )
    stack.add(mw)

Based on: LangChain ContextEditingMiddleware + ClearToolUsesEdit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ClearToolUsesEdit:
    """Configuration for clearing old tool uses from context.

    When the token count exceeds ``trigger``, removes tool
    call/result message pairs older than the ``keep`` most recent.

    Attributes:
        trigger: Token threshold that activates clearing.
        keep: Number of most recent tool interactions to preserve.
        clear_tool_inputs: Also clear the tool call arguments.
        exclude_tools: Tool names to never clear.
        placeholder: Text to replace cleared content with.
    """

    trigger: int = 100_000
    keep: int = 3
    clear_tool_inputs: bool = True
    exclude_tools: list[str] = field(default_factory=list)
    placeholder: str = "[cleared]"


class ContextEditingMiddleware:
    """Middleware that manages context size by editing messages.

    Applies a list of edit rules (currently ``ClearToolUsesEdit``)
    to the message list before each model call. Prevents token
    limit errors in long conversations with many tool calls.

    Args:
        edits: List of edit configurations to apply.
        token_counter: Custom token counting function.
    """

    def __init__(
        self,
        edits: list[ClearToolUsesEdit] | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self._edits = edits or [ClearToolUsesEdit()]
        self._counter = token_counter or _approx_token_count
        self._cleared_count: int = 0
        self._tokens_saved: int = 0

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "context_editing"

    @property
    def cleared_count(self) -> int:
        """Total tool messages cleared."""
        return self._cleared_count

    @property
    def tokens_saved(self) -> int:
        """Estimated tokens saved by clearing."""
        return self._tokens_saved

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """No prompt additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Apply context edits before the model call.

        Checks token count against each edit's trigger threshold.
        When triggered, clears old tool uses while keeping the
        most recent ones.

        Args:
            messages: Input message list.

        Returns:
            Edited message list (may be shorter).
        """
        total_tokens = _count_messages_tokens(messages, self._counter)

        for edit in self._edits:
            if total_tokens >= edit.trigger:
                messages = self._apply_clear_tool_uses(messages, edit)
                total_tokens = _count_messages_tokens(
                    messages, self._counter,
                )

        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        return response

    def _apply_clear_tool_uses(
        self,
        messages: list[dict[str, str]],
        edit: ClearToolUsesEdit,
    ) -> list[dict[str, str]]:
        """Clear old tool call/result pairs from messages.

        Finds tool result indices, keeps the ``edit.keep`` most
        recent, and clears the rest. Also clears corresponding
        assistant tool_call messages if configured.

        Args:
            messages: Full message list.
            edit: Clear configuration.

        Returns:
            Messages with old tool uses cleared.
        """
        result_indices = _find_tool_result_indices(
            messages, edit.exclude_tools,
        )

        if len(result_indices) <= edit.keep:
            return messages

        if edit.keep > 0:
            indices_to_clear = result_indices[:-edit.keep]
        else:
            indices_to_clear = result_indices

        return self._clear_indices(messages, indices_to_clear, edit)

    def _clear_indices(
        self,
        messages: list[dict[str, str]],
        indices: list[int],
        edit: ClearToolUsesEdit,
    ) -> list[dict[str, str]]:
        """Replace tool results at indices and their paired calls.

        For each tool result index, clears the content. Also finds
        the preceding assistant message with the matching tool_call
        and optionally clears its arguments.

        Args:
            messages: Message list.
            indices: Tool result indices to clear.
            edit: Configuration with placeholder text.

        Returns:
            Updated message list.
        """
        result = list(messages)
        clear_set = set(indices)

        for i in clear_set:
            if i >= len(result):
                continue
            msg = result[i]
            if not _is_tool_result(msg):
                continue

            old_content = msg.get("content", "")
            old_tokens = self._counter(old_content)
            result[i] = _make_cleared_message(msg, edit.placeholder)
            self._cleared_count += 1
            self._tokens_saved += old_tokens

            if edit.clear_tool_inputs:
                call_idx = _find_matching_call(result, i)
                if call_idx is not None:
                    call_tokens = self._counter(
                        str(result[call_idx].get("tool_calls", "")),
                    )
                    result[call_idx] = _clear_tool_call_args(
                        result[call_idx], edit.placeholder,
                    )
                    self._cleared_count += 1
                    self._tokens_saved += call_tokens

        return result

    def reset_stats(self) -> None:
        """Reset clearing statistics."""
        self._cleared_count = 0
        self._tokens_saved = 0


# ── Helpers ──────────────────────────────────────────────────────────── #


def _find_tool_result_indices(
    messages: list[dict[str, str]],
    exclude_tools: list[str],
) -> list[int]:
    """Find indices of tool result messages only.

    Args:
        messages: Message list.
        exclude_tools: Tool names to skip.

    Returns:
        Sorted list of tool result indices.
    """
    exclude_set = set(exclude_tools)
    indices: list[int] = []

    for i, msg in enumerate(messages):
        if _is_tool_result(msg):
            tool_name = msg.get("name", "")
            if tool_name not in exclude_set:
                indices.append(i)

    return indices


def _find_matching_call(
    messages: list[dict[str, Any]],
    tool_result_idx: int,
) -> int | None:
    """Find the assistant message with the matching tool call.

    Searches backwards from the tool result to find its
    corresponding assistant message.

    Args:
        messages: Message list.
        tool_result_idx: Index of the tool result message.

    Returns:
        Index of the matching assistant message, or None.
    """
    tool_call_id = messages[tool_result_idx].get("tool_call_id", "")
    for i in range(tool_result_idx - 1, -1, -1):
        msg = messages[i]
        if not _is_tool_call(msg):
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("id") == tool_call_id:
                return i
    return None


def _is_tool_result(msg: dict[str, Any]) -> bool:
    """Check if a message is a tool result.

    Args:
        msg: Message dict.

    Returns:
        True if role is 'tool'.
    """
    return msg.get("role") == "tool"


def _is_tool_call(msg: dict[str, Any]) -> bool:
    """Check if a message is an assistant message with tool calls.

    Args:
        msg: Message dict.

    Returns:
        True if assistant message has tool_calls.
    """
    return (
        msg.get("role") == "assistant"
        and bool(msg.get("tool_calls"))
    )


def _make_cleared_message(
    msg: dict[str, str],
    placeholder: str,
) -> dict[str, str]:
    """Replace tool result content with placeholder.

    Args:
        msg: Original tool message.
        placeholder: Replacement text.

    Returns:
        Message with cleared content.
    """
    return {**msg, "content": placeholder}


def _clear_tool_call_args(
    msg: dict[str, str],
    placeholder: str,
) -> dict[str, Any]:
    """Clear tool call arguments in an assistant message.

    Replaces each tool call's args with the placeholder while
    preserving the tool call structure.

    Args:
        msg: Assistant message with tool_calls.
        placeholder: Replacement for args.

    Returns:
        Message with cleared tool call arguments.
    """
    cleared_calls = []
    for tc in msg.get("tool_calls", []):
        cleared_calls.append({
            **tc,
            "args": {"_cleared": placeholder},
        })
    return {**msg, "tool_calls": cleared_calls}


def _count_messages_tokens(
    messages: list[dict[str, Any]],
    counter: Callable[[str], int],
) -> int:
    """Count total tokens across all messages.

    Args:
        messages: Message list.
        counter: Token counting function.

    Returns:
        Total estimated tokens.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += counter(content)
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            args = tc.get("args", {})
            total += counter(str(args))
    return total


def _approx_token_count(text: str) -> int:
    """Approximate token count (~1.3 tokens per word).

    Args:
        text: Text to count.

    Returns:
        Estimated token count.
    """
    return int(len(text.split()) * 1.3)
