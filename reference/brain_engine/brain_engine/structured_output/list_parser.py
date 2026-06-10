"""List Output Parser — extracts a list of items from LLM output.

Handles numbered lists, bullet points, and JSON arrays. Provides
clean string items with whitespace normalization.
"""

from __future__ import annotations

import json
import re

from brain_engine.structured_output.protocol import OutputParsingError

_BULLET_RE = re.compile(r"^[\s]*[-*\u2022]\s+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^[\s]*\d+[.)]\s+", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


class ListOutputParser:
    """Parses LLM output as a list of string items.

    Supports three formats:
    - JSON arrays: ``["item1", "item2"]``
    - Numbered lists: ``1. item1\\n2. item2``
    - Bullet lists: ``- item1\\n- item2``

    Args:
        separator: Custom separator (if provided, splits by this).

    Attributes:
        name: Parser identifier.
    """

    name: str = "list"

    def __init__(self, separator: str | None = None) -> None:
        self._separator = separator

    def parse(self, text: str) -> list[str]:
        """Parse text as a list of strings.

        Args:
            text: Raw LLM output.

        Returns:
            List of cleaned string items.

        Raises:
            OutputParsingError: If no list structure found.
        """
        if self._separator:
            return _split_by_separator(text, self._separator)

        cleaned = _strip_code_fence(text)
        return _auto_detect_list(cleaned, text)

    def get_format_instructions(self) -> str:
        """Return list format instructions for the LLM.

        Returns:
            Instruction string.
        """
        if self._separator:
            return (
                f"Return items separated by '{self._separator}'. "
                "Do not number or bullet the items."
            )
        return (
            "Return your response as a numbered list:\n"
            "1. First item\n"
            "2. Second item\n"
            "Do not include any text outside the list."
        )


# ── Helpers ───────────────────────────────────────────────────────── #


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences.

    Args:
        text: Raw text.

    Returns:
        Cleaned text.
    """
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _split_by_separator(text: str, separator: str) -> list[str]:
    """Split text by a custom separator.

    Args:
        text: Raw text.
        separator: Separator string.

    Returns:
        Non-empty stripped items.
    """
    items = text.split(separator)
    return [item.strip() for item in items if item.strip()]


def _auto_detect_list(cleaned: str, original: str) -> list[str]:
    """Auto-detect list format and parse accordingly.

    Tries JSON array, numbered list, bullet list, then newline split.

    Args:
        cleaned: Pre-cleaned text.
        original: Original text for error reporting.

    Returns:
        List of string items.

    Raises:
        OutputParsingError: If no items extracted.
    """
    result = _try_json_array(cleaned)
    if result is not None:
        return result

    result = _try_numbered_list(cleaned)
    if result:
        return result

    result = _try_bullet_list(cleaned)
    if result:
        return result

    result = _try_newline_split(cleaned)
    if result:
        return result

    raise OutputParsingError(original, "Could not parse as list")


def _try_json_array(text: str) -> list[str] | None:
    """Try parsing text as a JSON array.

    Args:
        text: Cleaned text.

    Returns:
        List of strings or None.
    """
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return None


def _try_numbered_list(text: str) -> list[str]:
    """Extract items from numbered list format.

    Args:
        text: Cleaned text.

    Returns:
        List of items (may be empty).
    """
    if not _NUMBERED_RE.search(text):
        return []
    items = _NUMBERED_RE.sub("", text).strip().split("\n")
    return [item.strip() for item in items if item.strip()]


def _try_bullet_list(text: str) -> list[str]:
    """Extract items from bullet list format.

    Args:
        text: Cleaned text.

    Returns:
        List of items (may be empty).
    """
    if not _BULLET_RE.search(text):
        return []
    items = _BULLET_RE.sub("", text).strip().split("\n")
    return [item.strip() for item in items if item.strip()]


def _try_newline_split(text: str) -> list[str]:
    """Split by newlines as last resort.

    Args:
        text: Cleaned text.

    Returns:
        Non-empty lines.
    """
    lines = text.strip().split("\n")
    return [line.strip() for line in lines if line.strip()]
