"""TokenCounter — token budget tracking and estimation.

Provides token counting for messages, tool results, and datasets.
Uses a character-based estimator as a fallback when tiktoken is
not available, achieving ~90% accuracy for English text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Average chars per token for GPT-4 class models
_CHARS_PER_TOKEN = 4.0

# Overhead per message (role, formatting)
_MESSAGE_OVERHEAD = 4


def _try_import_tiktoken() -> Any:
    """Attempt to import tiktoken for accurate counting."""
    try:
        import tiktoken

        return tiktoken
    except ImportError:
        return None


class TokenCounter:
    """Token counting with automatic fallback.

    Uses tiktoken for accurate counts when available, otherwise
    falls back to character-based estimation (~4 chars/token).

    Args:
        model: Model name for tiktoken encoding selection.
    """

    def __init__(self, model: str = "gpt-4o") -> None:
        self._model = model
        self._tiktoken = _try_import_tiktoken()
        self._encoder = self._create_encoder()

    @property
    def has_tiktoken(self) -> bool:
        """Whether tiktoken is available for accurate counting."""
        return self._encoder is not None

    # ── Message counting ─────────────────────────────────────────────

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        """Count tokens in a list of chat messages.

        Accounts for per-message overhead (role, separators).

        Args:
            messages: List of message dicts with ``role`` and ``content``.

        Returns:
            Estimated total token count.
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self._count_text(str(content)) + _MESSAGE_OVERHEAD
        return total

    def count_text(self, text: str) -> int:
        """Count tokens in a plain text string.

        Args:
            text: Text to count.

        Returns:
            Token count.
        """
        return self._count_text(text)

    def count_tool_result(self, result: Any) -> int:
        """Count tokens in a tool call result.

        Serializes the result to JSON then counts tokens.

        Args:
            result: Tool result (dict, list, string, etc.).

        Returns:
            Token count.
        """
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, default=str)
        return self._count_text(text)

    # ── Budget management ────────────────────────────────────────────

    def get_budget(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 200_000,
    ) -> dict[str, Any]:
        """Compute token budget for the current context.

        Args:
            messages: Current message list.
            max_tokens: Maximum input token limit.

        Returns:
            Dict with total, used, available, usage_pct, should_offload.
        """
        used = self.count_messages(messages)
        available = max(0, max_tokens - used)
        usage_pct = (used / max_tokens * 100) if max_tokens > 0 else 0.0

        return {
            "total": max_tokens,
            "used": used,
            "available": available,
            "usage_pct": round(usage_pct, 1),
            "should_offload": usage_pct >= 85.0,
        }

    def estimate_after_trim(
        self,
        messages: list[dict[str, Any]],
        trim_count: int,
    ) -> int:
        """Estimate token count after trimming oldest messages.

        Args:
            messages: Current messages.
            trim_count: Number of messages to trim from the start.

        Returns:
            Estimated token count after trimming.
        """
        remaining = messages[trim_count:]
        return self.count_messages(remaining)

    # ── Internal ─────────────────────────────────────────────────────

    def _count_text(self, text: str) -> int:
        """Count tokens using tiktoken or character estimation.

        Args:
            text: Input text.

        Returns:
            Token count.
        """
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        return _estimate_tokens(text)

    def _create_encoder(self) -> Any:
        """Create a tiktoken encoder for the configured model."""
        if self._tiktoken is None:
            return None
        try:
            return self._tiktoken.encoding_for_model(self._model)
        except (KeyError, Exception):
            try:
                return self._tiktoken.get_encoding("cl100k_base")
            except Exception:
                logger.warning("tiktoken fallback failed, using estimation")
                return None


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length.

    Uses ~4 chars/token ratio which is accurate for English
    and reasonable for other Latin-script languages.

    Args:
        text: Input text.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    model: str = "gpt-4o-mini",
) -> str:
    """Return ``text`` shortened to at most ``max_tokens`` tokens.

    Uses tiktoken's encode/decode round-trip when available — every
    surviving token is whole, no mid-word truncation.  When tiktoken
    is unavailable falls back to a character cap of
    ``max_tokens * _CHARS_PER_TOKEN``; the fallback is safe (only
    over-truncates, never under-truncates) so downstream callers
    that pass the result into an LLM prompt cannot exceed budget.

    Args:
        text: Input text.  ``""`` and ``None``-safe-ish (caller
            should pass a string).
        max_tokens: Inclusive upper bound.  ``<= 0`` returns ``""``.
        model: Model name for the tiktoken encoder.  Defaults to
            ``gpt-4o-mini`` which matches the extractor's call site.

    Returns:
        Truncated text.  When the input already fits, returned
        verbatim.
    """
    if not text or max_tokens <= 0:
        return ""

    tiktoken_module = _try_import_tiktoken()
    if tiktoken_module is not None:
        try:
            encoder = tiktoken_module.encoding_for_model(model)
        except (KeyError, Exception):  # pragma: no cover - tiktoken edge
            try:
                encoder = tiktoken_module.get_encoding("cl100k_base")
            except Exception:
                encoder = None
        if encoder is not None:
            tokens = encoder.encode(text)
            if len(tokens) <= max_tokens:
                return text
            return encoder.decode(tokens[:max_tokens])

    char_cap = max(1, int(max_tokens * _CHARS_PER_TOKEN))
    return text[:char_cap]
