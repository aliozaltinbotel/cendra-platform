"""Context Compressor - Summarizes long conversations using LLM.

When the conversation history exceeds token limits, the compressor
generates a concise summary of older turns while preserving the most
recent messages verbatim. This allows the agent to maintain awareness
of the full conversation without exceeding the LLM context window.

Inspired by MemGPT's virtual context management:
- Automatic detection of when compression is needed
- LLM-powered summarization preserving key facts and decisions
- Configurable thresholds and recent-turn preservation
"""

from __future__ import annotations

import logging
from typing import Any

import litellm

logger = logging.getLogger(__name__)

_COMPRESSION_PROMPT = """You are a conversation summarizer. Your task is to create a concise summary of the conversation history below, preserving:
1. Key facts and decisions made
2. Important user preferences or requirements
3. Current state of any on
going task or request
4. Any commitments or promises made by the assistant

Conversation to summarize:
{conversation}

Provide a concise summary in 2-5 sentences. Focus on information that would be needed to continue the conversation naturally."""


class ContextCompressor:
    """Compresses long conversation histories using LLM summarization.

    Monitors conversation length and automatically generates summaries
    when the token count exceeds a threshold. Recent turns are always
    preserved verbatim to maintain conversational coherence.

    Args:
        model: LiteLLM model identifier for summarization.
        max_tokens: Maximum token budget for the full context.
        keep_recent: Number of recent turns to always preserve verbatim.
        compression_threshold: Number of turns that triggers compression.
        chars_per_token: Approximate characters per token for estimation.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        max_tokens: int = 4000,
        keep_recent: int = 10,
        compression_threshold: int = 20,
        chars_per_token: float = 4.0,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.keep_recent = keep_recent
        self.compression_threshold = compression_threshold
        self.chars_per_token = chars_per_token

    def estimate_tokens(self, messages: list[dict[str, str]]) -> int:
        """Estimate the token count for a list of messages.

        Uses a character-based heuristic. For precise counting, integrate
        tiktoken or the model's own tokenizer.

        Args:
            messages: List of message dicts with 'role' and 'content'.

        Returns:
            Estimated token count.
        """
        total_chars = sum(
            len(m.get("role", "")) + len(m.get("content", "")) + 4  # role/content overhead
            for m in messages
        )
        return int(total_chars / self.chars_per_token)

    def should_compress(self, messages: list[dict[str, str]]) -> bool:
        """Determine whether the conversation needs compression.

        Compression is triggered when either:
        - The number of turns exceeds compression_threshold
        - The estimated token count exceeds max_tokens

        Args:
            messages: The full conversation history.

        Returns:
            True if compression should be performed.
        """
        if len(messages) > self.compression_threshold:
            return True
        if self.estimate_tokens(messages) > self.max_tokens:
            return True
        return False

    async def compress(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> list[dict[str, str]]:
        """Compress a conversation by summarizing older turns.

        Splits the conversation into older turns (to be summarized) and
        recent turns (kept verbatim). The summary is generated via an
        LLM call and injected as a system message at the start.

        Args:
            messages: Full conversation history as message dicts.
            max_tokens: Override for the max token budget.

        Returns:
            Compressed message list with a summary system message
            followed by the most recent turns.
        """
        effective_max = max_tokens or self.max_tokens

        if not self.should_compress(messages):
            return list(messages)

        # Separate system messages from conversation
        system_msgs = [m for m in messages if m.get("role") == "system"]
        conversation_msgs = [m for m in messages if m.get("role") != "system"]

        if len(conversation_msgs) <= self.keep_recent:
            return list(messages)

        # Split into old (to summarize) and recent (to keep)
        old_msgs = conversation_msgs[: -self.keep_recent]
        recent_msgs = conversation_msgs[-self.keep_recent:]

        # Format old messages for the summarization prompt
        conversation_text = "\n".join(
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
            for m in old_msgs
        )

        summary = await self._generate_summary(conversation_text)

        # Build compressed output
        compressed: list[dict[str, str]] = []

        # Preserve original system messages
        compressed.extend(system_msgs)

        # Add the summary as a system context message
        compressed.append({
            "role": "system",
            "content": (
                f"[CONVERSATION SUMMARY - {len(old_msgs)} earlier turns]\n"
                f"{summary}"
            ),
        })

        # Append recent turns verbatim
        compressed.extend(recent_msgs)

        estimated = self.estimate_tokens(compressed)
        logger.info(
            "Compressed %d messages to %d (~%d tokens, budget=%d)",
            len(messages),
            len(compressed),
            estimated,
            effective_max,
        )

        return compressed

    async def _generate_summary(self, conversation_text: str) -> str:
        """Generate an LLM summary of the conversation text.

        Args:
            conversation_text: Formatted conversation to summarize.

        Returns:
            The summary string.
        """
        prompt = _COMPRESSION_PROMPT.format(conversation=conversation_text)

        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise conversation summarizer.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=512,
            )
            summary = response.choices[0].message.content or ""
            return summary.strip()

        except Exception as exc:
            logger.error("LLM summarization failed: %s", exc)
            # Fallback: simple truncation-based summary
            return self._fallback_summary(conversation_text)

    @staticmethod
    def _fallback_summary(conversation_text: str) -> str:
        """Create a simple extractive summary when the LLM is unavailable.

        Takes the first and last few lines of the conversation as a
        crude but functional fallback.
        """
        lines = conversation_text.strip().split("\n")
        if len(lines) <= 6:
            return conversation_text

        head = lines[:3]
        tail = lines[-3:]
        return (
            "\n".join(head)
            + f"\n... ({len(lines) - 6} turns omitted) ...\n"
            + "\n".join(tail)
        )
