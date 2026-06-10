"""Format Check - Validates response format including JSON schema validation.

Ensures agent responses meet structural requirements: length constraints,
forbidden patterns, JSON schema compliance, and required field presence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)


class FormatCheck:
    """Validates response format, structure, and JSON schema compliance.

    Supports plain text validation (length, patterns) and structured
    JSON response validation against a JSON schema.

    Args:
        max_length: Maximum response length in characters.
        min_length: Minimum response length in characters.
        forbidden_patterns: Regex patterns that must not appear in responses.
        json_schema: Optional JSON schema dict to validate structured responses.
        required_fields: Optional list of field names that must be present
            in JSON responses (simpler alternative to full schema).
    """

    def __init__(
        self,
        max_length: int = 2000,
        min_length: int = 10,
        forbidden_patterns: list[str] | None = None,
        json_schema: dict[str, Any] | None = None,
        required_fields: list[str] | None = None,
    ) -> None:
        self._max_length = max_length
        self._min_length = min_length
        self._forbidden_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (forbidden_patterns or [])
        ]
        self._json_schema = json_schema
        self._required_fields = required_fields or []

    def check(self, response: str) -> list[str]:
        """Run all format checks on a response.

        Args:
            response: The agent's proposed response text.

        Returns:
            List of issue descriptions. Empty list means all checks passed.
        """
        issues: list[str] = []

        issues.extend(self._check_length(response))
        issues.extend(self._check_forbidden_patterns(response))
        issues.extend(self._check_artifacts(response))

        if self._json_schema or self._required_fields:
            issues.extend(self._check_json(response))

        if issues:
            logger.warning("Format check issues: %s", issues)

        return issues

    def _check_length(self, response: str) -> list[str]:
        """Validate response length constraints."""
        issues: list[str] = []
        stripped = response.strip()

        if len(stripped) > self._max_length:
            issues.append(
                f"Response too long: {len(stripped)} chars (max {self._max_length})"
            )
        if len(stripped) < self._min_length:
            issues.append(
                f"Response too short: {len(stripped)} chars (min {self._min_length})"
            )
        return issues

    def _check_forbidden_patterns(self, response: str) -> list[str]:
        """Check for forbidden regex patterns."""
        issues: list[str] = []
        for pattern in self._forbidden_patterns:
            match = pattern.search(response)
            if match:
                issues.append(
                    f"Forbidden pattern '{pattern.pattern}' found: "
                    f"'{match.group()[:50]}'"
                )
        return issues

    @staticmethod
    def _check_artifacts(response: str) -> list[str]:
        """Detect common LLM output artifacts."""
        issues: list[str] = []
        stripped = response.strip()

        if stripped.startswith("```"):
            issues.append("Response starts with code block markers")

        ai_phrases = [
            "as an ai",
            "i'm an ai",
            "as a language model",
            "i don't have feelings",
            "i cannot browse the internet",
        ]
        lower = stripped.lower()
        for phrase in ai_phrases:
            if phrase in lower:
                issues.append(f"Response contains AI self-reference: '{phrase}'")
                break

        return issues

    def _check_json(self, response: str) -> list[str]:
        """Validate JSON structure and schema compliance."""
        issues: list[str] = []

        # Extract JSON from potential markdown code fences
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:])
            if json_str.endswith("```"):
                json_str = json_str[:-3]
            json_str = json_str.strip()

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            issues.append(f"Invalid JSON: {exc}")
            return issues

        # Check required fields
        if isinstance(parsed, dict):
            for field_name in self._required_fields:
                if field_name not in parsed:
                    issues.append(f"Missing required field: '{field_name}'")
                elif parsed[field_name] is None or parsed[field_name] == "":
                    issues.append(f"Required field '{field_name}' is empty")

        # Validate against JSON schema if provided
        if self._json_schema:
            try:
                jsonschema.validate(instance=parsed, schema=self._json_schema)
            except jsonschema.ValidationError as exc:
                issues.append(f"JSON schema validation failed: {exc.message}")
            except jsonschema.SchemaError as exc:
                logger.error("Invalid JSON schema: %s", exc)
                issues.append(f"Invalid JSON schema configuration: {exc.message}")

        return issues

    def is_valid(self, response: str) -> bool:
        """Check whether a response passes all format validations.

        Args:
            response: The response to validate.

        Returns:
            True if no issues were found.
        """
        return len(self.check(response)) == 0
