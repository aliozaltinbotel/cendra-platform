"""Message preprocessing — clean and normalize incoming messages.

Handles HTML stripping, internal message detection, whitespace
normalization, and extraction of the actual guest question.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Patterns for system/internal messages that should not be processed
_SYSTEM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"reservation\s+(confirmed|cancelled|modified)", re.IGNORECASE),
    re.compile(r"booking\s+#?\d+\s+(created|updated)", re.IGNORECASE),
    re.compile(r"payment\s+(received|processed|failed)", re.IGNORECASE),
    re.compile(r"auto-?reply", re.IGNORECASE),
]


def clean_message(raw_text: str) -> str:
    """Clean and normalize a raw message.

    Strips HTML tags, normalizes whitespace, removes email
    signatures and system markers.

    Args:
        raw_text: Raw message text (may contain HTML).

    Returns:
        Cleaned text ready for processing.
    """
    text = _strip_html(raw_text)
    text = _remove_email_signature(text)
    text = _normalize_whitespace(text)
    return text.strip()


def is_system_message(text: str) -> bool:
    """Check if a message is a system notification.

    Args:
        text: Message text.

    Returns:
        True if this is an automated system message.
    """
    return any(p.search(text) for p in _SYSTEM_PATTERNS)


def is_empty_or_media_only(text: str) -> bool:
    """Check if message has no meaningful text content.

    Args:
        text: Cleaned message text.

    Returns:
        True if message is empty or media-only.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in ("[Image]", "[Video]", "[Audio]", "[Document]"):
        return True
    return False


def _strip_html(text: str) -> str:
    """Remove HTML tags while preserving readable structure.

    Args:
        text: Text potentially containing HTML.

    Returns:
        Plain text with HTML removed.
    """
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<img[^>]*>", "[Image]", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return text


def _remove_email_signature(text: str) -> str:
    """Remove common email signature patterns.

    Args:
        text: Message text.

    Returns:
        Text without email signatures.
    """
    signature_markers = [
        "\n--\n",
        "\nSent from my iPhone",
        "\nSent from my Android",
        "\n___",
        "\nBest regards,",
        "\nKind regards,",
    ]
    for marker in signature_markers:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace: collapse multiple spaces/newlines.

    Args:
        text: Input text.

    Returns:
        Text with normalized whitespace.
    """
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
