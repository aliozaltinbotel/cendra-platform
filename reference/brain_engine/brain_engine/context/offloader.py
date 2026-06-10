"""Offloader — automatic tool result offloading to BrainZFS.

Detects large tool results and offloads them to COW storage,
replacing the inline content with a brain:// reference path.
Supports chunked retrieval and automatic deduplication.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from brain_engine.context.token_counter import TokenCounter
from brain_engine.zfs.brain_zfs import BrainZFS

logger = logging.getLogger(__name__)


class OffloadResult(BaseModel):
    """Result of an offloading operation.

    Attributes:
        offloaded: Whether the result was offloaded.
        reference_path: brain:// path if offloaded, empty otherwise.
        original_tokens: Token count of the original result.
        preview: First N lines of the content for inline display.
        block_hash: Content hash for dedup tracking.
    """

    offloaded: bool = False
    reference_path: str = ""
    original_tokens: int = 0
    preview: str = ""
    block_hash: str = ""


class Offloader:
    """Manages offloading of large tool results to BrainZFS.

    Decides when to offload based on token count and budget,
    then stores data in COW storage with automatic dedup.

    Args:
        zfs: BrainZFS instance for storage.
        token_counter: TokenCounter for size estimation.
        tool_token_limit: Results exceeding this are offloaded.
        preview_lines: Number of lines to keep inline as preview.
    """

    def __init__(
        self,
        zfs: BrainZFS,
        token_counter: TokenCounter | None = None,
        tool_token_limit: int = 20_000,
        preview_lines: int = 10,
    ) -> None:
        self._zfs = zfs
        self._counter = token_counter or TokenCounter()
        self._tool_token_limit = tool_token_limit
        self._preview_lines = preview_lines

    @property
    def tool_token_limit(self) -> int:
        """Return the token threshold for offloading."""
        return self._tool_token_limit

    # ── Decision ─────────────────────────────────────────────────────

    def should_offload(
        self,
        result: Any,
        session_token_usage: int = 0,
        max_tokens: int = 200_000,
    ) -> bool:
        """Decide whether a tool result should be offloaded.

        Offloads if:
          1. Result tokens exceed tool_token_limit, OR
          2. Session is at >=85% of max token budget.

        Args:
            result: Tool call result to evaluate.
            session_token_usage: Current session token usage.
            max_tokens: Maximum token budget.

        Returns:
            True if the result should be offloaded.
        """
        tokens = self._counter.count_tool_result(result)
        if tokens > self._tool_token_limit:
            return True

        usage_pct = (session_token_usage / max_tokens * 100) if max_tokens > 0 else 0
        return usage_pct >= 85.0

    # ── Offload ──────────────────────────────────────────────────────

    async def offload(
        self,
        session_id: str,
        tool_name: str,
        result: Any,
    ) -> OffloadResult:
        """Offload a tool result to BrainZFS storage.

        Stores the full result at a brain:// path and returns
        a reference with a preview for inline display.

        Args:
            session_id: Current session identifier.
            tool_name: Name of the tool that produced the result.
            result: The full tool result.

        Returns:
            OffloadResult with reference path and preview.
        """
        tokens = self._counter.count_tool_result(result)
        path = self._build_path(session_id, tool_name)

        write_result = await self._zfs.write(path, result)

        preview = self._extract_preview(result)

        logger.info(
            "Offloaded %s: %d tokens → %s (dedup=%s)",
            tool_name, tokens, path, write_result.deduplicated,
        )
        return OffloadResult(
            offloaded=True,
            reference_path=path,
            original_tokens=tokens,
            preview=preview,
            block_hash=write_result.block_hash,
        )

    # ── Rehydrate ────────────────────────────────────────────────────

    async def rehydrate(
        self,
        reference_path: str,
        offset: int = 0,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Load offloaded data from BrainZFS with optional chunking.

        Args:
            reference_path: brain:// path to load.
            offset: Character offset for chunked reading.
            limit: Maximum characters to return.

        Returns:
            Dict with data, total_length, offset, and has_more.
        """
        data = await self._zfs.read(reference_path)
        if data is None:
            return {"data": None, "total_length": 0, "offset": 0, "has_more": False}

        text = json.dumps(data, default=str) if not isinstance(data, str) else data
        total = len(text)
        chunk = text[offset:offset + limit]

        return {
            "data": chunk,
            "total_length": total,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    # ── Batch processing ─────────────────────────────────────────────

    async def process_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        session_token_usage: int = 0,
    ) -> list[dict[str, Any]]:
        """Scan messages and offload large tool results.

        Replaces inline tool content with references where appropriate.

        Args:
            session_id: Current session identifier.
            messages: Message list to process.
            session_token_usage: Current token usage.

        Returns:
            Processed message list (may contain references instead of data).
        """
        processed: list[dict[str, Any]] = []
        for msg in messages:
            processed.append(await self._process_single(msg, session_id, session_token_usage))
        return processed

    async def _process_single(
        self,
        msg: dict[str, Any],
        session_id: str,
        session_token_usage: int,
    ) -> dict[str, Any]:
        """Process a single message for potential offloading.

        Args:
            msg: Message dict.
            session_id: Session identifier.
            session_token_usage: Current token usage.

        Returns:
            Original or modified message.
        """
        if msg.get("role") != "tool":
            return msg

        content = msg.get("content", "")
        tool_name = msg.get("name", "unknown_tool")

        if not self.should_offload(content, session_token_usage):
            return msg

        offload_result = await self.offload(session_id, tool_name, content)
        return {
            **msg,
            "content": (
                f"[Offloaded to {offload_result.reference_path}]\n"
                f"Preview:\n{offload_result.preview}"
            ),
            "_offload_ref": offload_result.reference_path,
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _build_path(self, session_id: str, tool_name: str) -> str:
        """Build a brain:// storage path for an offloaded result.

        Args:
            session_id: Session identifier.
            tool_name: Tool name.

        Returns:
            Formatted brain:// path.
        """
        ts = int(time.time())
        return f"sessions/{session_id}/tools/{tool_name}_{ts}"

    def _extract_preview(self, result: Any) -> str:
        """Extract first N lines as an inline preview.

        Args:
            result: Tool result to preview.

        Returns:
            Preview string (first N lines).
        """
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, default=str, indent=2)

        lines = text.split("\n")
        preview_lines = lines[:self._preview_lines]
        preview = "\n".join(preview_lines)

        if len(lines) > self._preview_lines:
            preview += f"\n... ({len(lines) - self._preview_lines} more lines)"

        return preview
