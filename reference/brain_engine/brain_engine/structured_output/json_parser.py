"""JSON Output Parser — parses raw LLM text into Python dicts.

Handles common LLM output quirks: markdown code fences, trailing
commas, and extra whitespace. Returns a validated dict.
"""

from __future__ import annotations

import json
import re
from typing import Any

from brain_engine.structured_output.protocol import OutputParsingError

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


class JsonOutputParser:
    """Parses LLM output as JSON dict.

    Strips markdown code fences and attempts robust JSON extraction.

    Attributes:
        name: Parser identifier.
    """

    name: str = "json"

    def parse(self, text: str) -> dict[str, Any]:
        """Parse text as JSON dict.

        Args:
            text: Raw LLM output.

        Returns:
            Parsed dict.

        Raises:
            OutputParsingError: If no valid JSON found.
        """
        cleaned = _strip_code_fence(text)
        return _parse_json_dict(cleaned, text)

    def get_format_instructions(self) -> str:
        """Return JSON format instructions for the LLM.

        Returns:
            Instruction string.
        """
        return (
            "Return your response as a valid JSON object. "
            "Do not include any text outside the JSON object. "
            "Do not wrap in markdown code fences."
        )


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences from text.

    Args:
        text: Input text possibly wrapped in ```json ... ```.

    Returns:
        Cleaned text.
    """
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_json_dict(cleaned: str, original: str) -> dict[str, Any]:
    """Parse cleaned text as a JSON dict.

    Falls back to extracting the first JSON object if direct
    parsing fails.

    Args:
        cleaned: Pre-cleaned text.
        original: Original raw text for error reporting.

    Returns:
        Parsed dict.

    Raises:
        OutputParsingError: If parsing fails.
    """
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
        raise OutputParsingError(original, f"Expected dict, got {type(result).__name__}")
    except json.JSONDecodeError:
        return _extract_json_object(cleaned, original)


def _extract_json_object(text: str, original: str) -> dict[str, Any]:
    """Extract the first JSON object from text using brace matching.

    Args:
        text: Text containing a JSON object somewhere.
        original: Original text for error reporting.

    Returns:
        Parsed dict.

    Raises:
        OutputParsingError: If no valid JSON object found.
    """
    start = text.find("{")
    if start == -1:
        raise OutputParsingError(original, "No JSON object found in output")

    # Find matching closing brace
    depth = 0
    for i, char in enumerate(text[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    break

    raise OutputParsingError(original, "Failed to extract valid JSON object")
