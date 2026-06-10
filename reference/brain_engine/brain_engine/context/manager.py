"""ContextManager — main orchestrator for context lifecycle.

Coordinates offloading, summarization, and token budget management
across the Brain Engine's conversation pipeline. Integrates with
BrainZFS for storage and the middleware system for automatic
context management.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.context.offloader import Offloader, OffloadResult
from brain_engine.context.summarizer import ContextSummarizer, SummarizerLLM, SummaryResult
from brain_engine.context.token_counter import TokenCounter
from brain_engine.zfs.brain_zfs import BrainZFS

logger = logging.getLogger(__name__)


class ContextManager:
    """Orchestrates context engineering for a session.

    Provides a high-level API for:
      - Automatic offloading of large tool results.
      - Conversation summarization with rollback safety.
      - Token budget tracking.
      - Context loading from BrainZFS.

    Args:
        zfs: BrainZFS instance.
        max_input_tokens: Maximum context window size.
        trigger_ratio: Usage percentage that triggers offloading (0.0-1.0).
        tool_token_limit: Token threshold for tool result offloading.
        llm: Optional LLM for summarization.
    """

    def __init__(
        self,
        zfs: BrainZFS,
        max_input_tokens: int = 200_000,
        trigger_ratio: float = 0.85,
        tool_token_limit: int = 20_000,
        llm: SummarizerLLM | None = None,
    ) -> None:
        self._zfs = zfs
        self._max_input_tokens = max_input_tokens
        self._trigger_ratio = trigger_ratio

        self._counter = TokenCounter()
        self._offloader = Offloader(
            zfs=zfs,
            token_counter=self._counter,
            tool_token_limit=tool_token_limit,
        )
        self._summarizer = ContextSummarizer(
            zfs=zfs,
            token_counter=self._counter,
            llm=llm,
        )

    @property
    def max_input_tokens(self) -> int:
        """Return the maximum input token budget."""
        return self._max_input_tokens

    @property
    def counter(self) -> TokenCounter:
        """Return the token counter instance."""
        return self._counter

    # ── Token budget ─────────────────────────────────────────────────

    def get_token_budget(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute token budget for the current context.

        Args:
            messages: Current conversation messages.

        Returns:
            Dict with total, used, available, usage_pct, should_offload.
        """
        return self._counter.get_budget(messages, self._max_input_tokens)

    # ── Offloading ───────────────────────────────────────────────────

    async def offload_to_zfs(
        self,
        session_id: str,
        tool_name: str,
        tool_result: Any,
    ) -> OffloadResult:
        """Offload a large tool result to BrainZFS.

        Args:
            session_id: Session identifier.
            tool_name: Name of the tool.
            tool_result: Full tool result data.

        Returns:
            OffloadResult with reference path and preview.
        """
        return await self._offloader.offload(session_id, tool_name, tool_result)

    async def offload_large_results(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Scan and offload large tool results in messages.

        Args:
            session_id: Session identifier.
            messages: Message list to process.

        Returns:
            Processed messages with offloaded references.
        """
        budget = self.get_token_budget(messages)
        return await self._offloader.process_messages(
            session_id, messages, budget["used"],
        )

    # ── Summarization ────────────────────────────────────────────────

    async def summarize_with_snapshot(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> SummaryResult:
        """Summarize conversation with BrainZFS snapshot safety.

        Args:
            session_id: Session identifier.
            messages: Messages to summarize.

        Returns:
            SummaryResult with summary and metadata.
        """
        return await self._summarizer.summarize(session_id, messages)

    async def auto_summarize(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        keep_recent: int = 10,
    ) -> dict[str, Any]:
        """Auto-summarize oldest messages when context is large.

        Only summarizes if message count exceeds keep_recent threshold.

        Args:
            session_id: Session identifier.
            messages: Full message list.
            keep_recent: Number of recent messages to preserve.

        Returns:
            Dict with summary_message and processed messages.
        """
        return await self._summarizer.summarize_oldest(
            session_id, messages, keep_recent,
        )

    # ── Load from ZFS ────────────────────────────────────────────────

    async def load_from_zfs(
        self,
        path: str,
        offset: int = 0,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Load offloaded data from BrainZFS with chunking.

        Args:
            path: brain:// reference path.
            offset: Character offset for chunked reading.
            limit: Maximum characters to return.

        Returns:
            Dict with data, total_length, offset, has_more.
        """
        return await self._offloader.rehydrate(path, offset, limit)

    # ── Full pipeline ────────────────────────────────────────────────

    async def manage_context(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        auto_summarize_threshold: int = 50,
        keep_recent: int = 10,
    ) -> dict[str, Any]:
        """Run the full context management pipeline.

        Steps:
          1. Check token budget.
          2. Offload large tool results if needed.
          3. Auto-summarize if message count exceeds threshold.
          4. Return managed messages with metadata.

        Args:
            session_id: Session identifier.
            messages: Input messages.
            auto_summarize_threshold: Message count trigger for summarization.
            keep_recent: Messages to keep after summarization.

        Returns:
            Dict with managed messages, budget, and actions taken.
        """
        actions: list[str] = []

        budget = self.get_token_budget(messages)
        if budget["should_offload"]:
            messages = await self.offload_large_results(session_id, messages)
            actions.append("offloaded_large_results")
            budget = self.get_token_budget(messages)

        if len(messages) > auto_summarize_threshold:
            result = await self.auto_summarize(session_id, messages, keep_recent)
            messages = result["messages"]
            actions.append("summarized_old_messages")
            budget = self.get_token_budget(messages)

        return {
            "messages": messages,
            "budget": budget,
            "actions": actions,
            "session_id": session_id,
        }

    # ── Recovery ─────────────────────────────────────────────────────

    async def recover_history(
        self,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        """Recover full conversation history from ZFS.

        Args:
            session_id: Session identifier.

        Returns:
            Full message list or None.
        """
        return await self._summarizer.recover_full_history(session_id)
