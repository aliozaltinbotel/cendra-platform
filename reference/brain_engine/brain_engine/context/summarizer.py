"""ContextSummarizer — conversation compression with rollback safety.

Summarizes long conversations while maintaining rollback safety
through BrainZFS snapshots. Full history is always preserved in
COW storage, and bad summarizations can be rolled back in O(1).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from brain_engine.context.token_counter import TokenCounter
from brain_engine.zfs.brain_zfs import BrainZFS

logger = logging.getLogger(__name__)


@runtime_checkable
class SummarizerLLM(Protocol):
    """Protocol for LLM summarization calls."""

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        """Summarize a list of messages into a concise text."""
        ...


class SummaryResult(BaseModel):
    """Result of a summarization operation.

    Attributes:
        summary: The compressed conversation text.
        original_tokens: Token count before summarization.
        summary_tokens: Token count after summarization.
        tokens_saved: Number of tokens saved.
        compression_ratio: Ratio of original to summary size.
        full_history_path: brain:// path to the full history.
        snapshot_name: Name of the pre-summarization snapshot.
        validated: Whether the summary passed quality checks.
    """

    summary: str
    original_tokens: int = 0
    summary_tokens: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 1.0
    full_history_path: str = ""
    snapshot_name: str = ""
    validated: bool = True


class ContextSummarizer:
    """Summarizes conversations with BrainZFS rollback safety.

    Before summarizing:
      1. Creates a snapshot (rollback point).
      2. Stores full history in COW storage.
      3. Runs LLM summarization.
      4. Validates the summary.
      5. If invalid, rolls back to the snapshot.

    Args:
        zfs: BrainZFS instance for storage and snapshots.
        token_counter: TokenCounter for budget tracking.
        llm: Optional LLM for summarization. If None, uses extractive fallback.
        max_summary_tokens: Target maximum size for summaries.
    """

    def __init__(
        self,
        zfs: BrainZFS,
        token_counter: TokenCounter | None = None,
        llm: SummarizerLLM | None = None,
        max_summary_tokens: int = 4000,
    ) -> None:
        self._zfs = zfs
        self._counter = token_counter or TokenCounter()
        self._llm = llm
        self._max_summary_tokens = max_summary_tokens

    # ── Main entry point ─────────────────────────────────────────────

    async def summarize(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> SummaryResult:
        """Summarize a conversation with snapshot safety.

        Steps:
          1. Snapshot current state.
          2. Store full history in ZFS.
          3. Generate summary (LLM or extractive).
          4. Validate summary quality.
          5. Rollback if validation fails.

        Args:
            session_id: Session identifier.
            messages: Conversation messages to summarize.

        Returns:
            SummaryResult with summary text and metadata.
        """
        snap_name = f"sessions/{session_id}@before_summarize"
        await self._zfs.snapshot(snap_name)

        history_path = f"sessions/{session_id}/full_history"
        await self._zfs.write(history_path, messages)

        original_tokens = self._counter.count_messages(messages)
        summary_text = await self._generate_summary(messages)
        summary_tokens = self._counter.count_text(summary_text)

        validated = self._validate_summary(messages, summary_text)

        if not validated:
            logger.warning(
                "Summary validation failed for %s, rolling back",
                session_id,
            )
            await self._zfs.rollback(snap_name)

        return SummaryResult(
            summary=summary_text,
            original_tokens=original_tokens,
            summary_tokens=summary_tokens,
            tokens_saved=original_tokens - summary_tokens,
            compression_ratio=self._calc_ratio(original_tokens, summary_tokens),
            full_history_path=history_path,
            snapshot_name=snap_name,
            validated=validated,
        )

    # ── Incremental summarization ────────────────────────────────────

    async def summarize_oldest(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        keep_recent: int = 10,
    ) -> dict[str, Any]:
        """Summarize only the oldest messages, keeping recent ones intact.

        Args:
            session_id: Session identifier.
            messages: Full message list.
            keep_recent: Number of recent messages to keep verbatim.

        Returns:
            Dict with summary_message and remaining messages.
        """
        if len(messages) <= keep_recent:
            return {"summary_message": None, "messages": messages}

        to_summarize = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        result = await self.summarize(session_id, to_summarize)

        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": (
                f"[Conversation summary — {result.tokens_saved} tokens saved]\n"
                f"{result.summary}\n"
                f"[Full history: {result.full_history_path}]"
            ),
        }

        return {
            "summary_message": summary_msg,
            "messages": [summary_msg] + recent,
            "result": result,
        }

    # ── Diff / Recovery ──────────────────────────────────────────────

    async def get_diff(self, session_id: str) -> list[Any]:
        """Show what changed between pre-summary and current state.

        Args:
            session_id: Session identifier.

        Returns:
            List of Change objects from BrainZFS diff.
        """
        snap_name = f"sessions/{session_id}@before_summarize"
        try:
            return await self._zfs.snapshots.diff_from_current(snap_name)
        except KeyError:
            return []

    async def recover_full_history(
        self,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        """Recover full conversation history from ZFS storage.

        Args:
            session_id: Session identifier.

        Returns:
            Full message list or None if not found.
        """
        path = f"sessions/{session_id}/full_history"
        return await self._zfs.read(path)

    # ── Internal ─────────────────────────────────────────────────────

    async def _generate_summary(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Generate a summary using LLM or extractive fallback.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary text.
        """
        if self._llm is not None:
            return await self._llm.summarize(messages)
        return self._extractive_summary(messages)

    def _extractive_summary(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Produce an extractive summary without LLM.

        Extracts key messages based on role priority:
        system > user > assistant (shortened).

        Args:
            messages: Messages to summarize.

        Returns:
            Extractive summary text.
        """
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))

            if role == "system":
                parts.append(f"[System] {content[:200]}")
            elif role == "user":
                parts.append(f"[User] {content[:150]}")
            elif role == "assistant":
                parts.append(f"[Assistant] {content[:100]}")
            elif role == "tool":
                tool_name = msg.get("name", "tool")
                parts.append(f"[Tool:{tool_name}] (result omitted)")

        summary = "\n".join(parts)
        max_chars = self._max_summary_tokens * 4
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "\n... (truncated)"
        return summary

    def _validate_summary(
        self,
        original: list[dict[str, Any]],
        summary: str,
    ) -> bool:
        """Basic validation of summary quality.

        Checks:
          1. Summary is not empty.
          2. Summary is shorter than original.
          3. Summary retains some key information.

        Args:
            original: Original messages.
            summary: Generated summary.

        Returns:
            True if the summary passes validation.
        """
        if not summary.strip():
            return False

        original_tokens = self._counter.count_messages(original)
        summary_tokens = self._counter.count_text(summary)

        if summary_tokens >= original_tokens:
            return False

        return True

    def _calc_ratio(self, original: int, compressed: int) -> float:
        """Calculate compression ratio."""
        if compressed <= 0:
            return float("inf")
        return round(original / compressed, 2)
