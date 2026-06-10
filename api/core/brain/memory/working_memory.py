"""Working Memory - In-memory scratchpad for the current session.

Stores the active conversation context, current slot values, recent
turns, and ephemeral data needed during a single processing cycle.
Implements automatic truncation when the conversation exceeds a
configurable maximum turn count.

Inspired by MemGPT's concept of a "main context" that the agent
actively works with, distinct from archival/retrieval memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, override

from core.brain.memory.observe import emit_memory_retrieved

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ConversationTurn:
    """A single turn in the conversation.

    Attributes:
        role: The speaker role ('user', 'assistant', 'system', 'tool').
        content: The message content.
        metadata: Optional metadata (timestamps, token counts, etc.).
    """

    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkingMemory:
    """In-memory scratchpad for the current agent session.

    Maintains the live conversation buffer, active slot state, and
    scratch variables. Automatically truncates older turns when the
    buffer exceeds max_turns, preserving the system prompt and most
    recent interactions.

    Args:
        max_turns: Maximum number of conversation turns to retain.
            When exceeded, the oldest non-system turns are dropped.
        session_id: Identifier for the current session.
    """

    def __init__(
        self,
        max_turns: int = 50,
        session_id: str = "",
    ) -> None:
        self.max_turns = max_turns
        self.session_id = session_id

        self._turns: list[ConversationTurn] = []
        self._active_slots: dict[str, Any] = {}
        self._context: dict[str, Any] = {}
        self._scratch: dict[str, Any] = {}

    # ── Conversation turns ──────────────────────────────────────────

    def add_turn(
        self,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a conversation turn and auto-truncate if needed.

        Args:
            role: Speaker role ('user', 'assistant', 'system', 'tool').
            content: The message content.
            metadata: Optional metadata for the turn.
        """
        turn = ConversationTurn(
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self._turns.append(turn)
        self._truncate_if_needed()
        logger.debug("Added turn: role=%s, len=%d", role, len(content))

    def _truncate_if_needed(self) -> None:
        """Remove oldest non-system turns to stay within max_turns."""
        if len(self._turns) <= self.max_turns:
            return

        # Separate system turns (always keep) from conversation turns
        system_turns = [t for t in self._turns if t.role == "system"]
        non_system = [t for t in self._turns if t.role != "system"]

        # Keep the most recent non-system turns
        keep_count = self.max_turns - len(system_turns)
        if keep_count < 1:
            keep_count = 1

        trimmed = non_system[-keep_count:]
        self._turns = system_turns + trimmed

        logger.debug(
            "Truncated working memory to %d turns (max=%d)",
            len(self._turns),
            self.max_turns,
        )

    def get_turns(self, last_n: int | None = None) -> list[ConversationTurn]:
        """Get conversation turns, optionally limited to the last N.

        Args:
            last_n: If provided, return only the last N turns.

        Returns:
            List of ConversationTurn objects.
        """
        if last_n is not None:
            return list(self._turns[-last_n:])
        return list(self._turns)

    def get_messages(self, last_n: int | None = None) -> list[dict[str, str]]:
        """Get turns formatted as LLM message dicts.

        Args:
            last_n: If provided, return only the last N turns.

        Returns:
            List of {"role": ..., "content": ...} dicts.
        """
        t0 = time.perf_counter()
        turns = self.get_turns(last_n)
        messages = [{"role": t.role, "content": t.content} for t in turns]
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emit_memory_retrieved(
            tier="working",
            query=f"last_n:{last_n}" if last_n is not None else "all",
            hits=[{"id": str(i), "score": 1.0, "excerpt": m["content"]} for i, m in enumerate(messages)],
            latency_ms=latency_ms,
        )
        return messages

    @property
    def last_user_message(self) -> str | None:
        """The most recent user message, or None."""
        for turn in reversed(self._turns):
            if turn.role == "user":
                return turn.content
        return None

    @property
    def last_assistant_message(self) -> str | None:
        """The most recent assistant message, or None."""
        for turn in reversed(self._turns):
            if turn.role == "assistant":
                return turn.content
        return None

    @property
    def turn_count(self) -> int:
        """Total number of turns currently in memory."""
        return len(self._turns)

    # ── Active slots ────────────────────────────────────────────────

    def set_active_slots(self, slots: dict[str, Any]) -> None:
        """Update the active slot snapshot in working memory.

        Args:
            slots: Dictionary of slot name to current value.
        """
        self._active_slots = dict(slots)

    def get_active_slots(self) -> dict[str, Any]:
        """Get the current active slot snapshot."""
        return dict(self._active_slots)

    # ── Context and scratch space ───────────────────────────────────

    def set_context(self, key: str, value: Any) -> None:
        """Store a context value (persists across turns within session).

        Args:
            key: Context key.
            value: Context value.
        """
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """Retrieve a context value.

        Args:
            key: Context key.
            default: Default if key not found.
        """
        return self._context.get(key, default)

    def set_scratch(self, key: str, value: Any) -> None:
        """Store a temporary scratch value (cleared between tasks).

        Args:
            key: Scratch key.
            value: Scratch value.
        """
        self._scratch[key] = value

    def get_scratch(self, key: str, default: Any = None) -> Any:
        """Retrieve a scratch value."""
        return self._scratch.get(key, default)

    def clear_scratch(self) -> None:
        """Clear all scratch data."""
        self._scratch.clear()

    # ── Lifecycle ───────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset all working memory state."""
        self._turns.clear()
        self._active_slots.clear()
        self._context.clear()
        self._scratch.clear()
        logger.info("Working memory reset for session=%s", self.session_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize working memory state for debugging or persistence."""
        return {
            "session_id": self.session_id,
            "turn_count": len(self._turns),
            "max_turns": self.max_turns,
            "active_slots": self._active_slots,
            "context": self._context,
            "turns": [{"role": t.role, "content": t.content, "metadata": t.metadata} for t in self._turns],
        }

    @override
    def __repr__(self) -> str:
        return f"WorkingMemory(turns={len(self._turns)}/{self.max_turns}, session={self.session_id!r})"
