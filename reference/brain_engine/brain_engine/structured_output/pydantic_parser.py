"""Pydantic Output Parser — validates LLM output against Pydantic models.

Generates JSON Schema from Pydantic models, injects format instructions
into prompts, and parses + validates LLM responses with full error detail.
Supports both Pydantic v1 and v2 model schemas.
"""

from __future__ import annotations

import json
import re
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from brain_engine.structured_output.protocol import OutputParsingError

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


class PydanticOutputParser(Generic[T]):
    """Parses and validates LLM output against a Pydantic model.

    Generates JSON Schema instructions for the LLM and validates
    the parsed response against the model's field definitions.

    Args:
        model_class: The Pydantic model class to validate against.

    Attributes:
        name: Parser identifier.
    """

    name: str = "pydantic"

    def __init__(self, model_class: type[T]) -> None:
        self._model = model_class

    @property
    def schema(self) -> dict[str, Any]:
        """JSON Schema derived from the Pydantic model.

        Returns:
            JSON Schema dict.
        """
        return _get_model_schema(self._model)

    def parse(self, text: str) -> T:
        """Parse and validate text as a Pydantic model instance.

        Args:
            text: Raw LLM output.

        Returns:
            Validated Pydantic model instance.

        Raises:
            OutputParsingError: If parsing or validation fails.
        """
        cleaned = _strip_code_fence(text)
        data = _parse_json(cleaned, text)
        return _validate_model(self._model, data, text)

    def get_format_instructions(self) -> str:
        """Generate Pydantic-aware format instructions for the LLM.

        Includes the full JSON Schema so the LLM knows exactly
        what fields are required and their types.

        Returns:
            Format instruction string with schema.
        """
        schema_str = json.dumps(self.schema, indent=2)
        return (
            "Return your response as a JSON object matching this schema:\n\n"
            f"```json\n{schema_str}\n```\n\n"
            "Ensure all required fields are present and types match."
        )

    def parse_with_raw(self, text: str) -> dict[str, Any]:
        """Parse and return both the model and raw text.

        Args:
            text: Raw LLM output.

        Returns:
            Dict with 'parsed' (model instance) and 'raw' (original text).

        Raises:
            OutputParsingError: If parsing fails.
        """
        parsed = self.parse(text)
        return {"parsed": parsed, "raw": text}


# ── Helpers ───────────────────────────────────────────────────────── #


def _get_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Extract JSON Schema from a Pydantic model.

    Args:
        model: Pydantic model class.

    Returns:
        JSON Schema dict.
    """
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()  # type: ignore[union-attr]


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences from text.

    Args:
        text: Raw text.

    Returns:
        Cleaned text.
    """
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_json(cleaned: str, original: str) -> dict[str, Any]:
    """Parse cleaned text as JSON dict.

    Args:
        cleaned: Pre-cleaned text.
        original: Original text for error reporting.

    Returns:
        Parsed dict.

    Raises:
        OutputParsingError: If JSON parsing fails.
    """
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise OutputParsingError(original, f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise OutputParsingError(original, f"Expected dict, got {type(data).__name__}")
    return data


def _validate_model(
    model: type[T],
    data: dict[str, Any],
    original: str,
) -> T:
    """Validate data against a Pydantic model.

    Args:
        model: Pydantic model class.
        data: Parsed dict.
        original: Original text for error reporting.

    Returns:
        Validated model instance.

    Raises:
        OutputParsingError: If validation fails.
    """
    try:
        if hasattr(model, "model_validate"):
            return model.model_validate(data)
        return model(**data)
    except ValidationError as exc:
        raise OutputParsingError(original, f"Validation error: {exc}") from exc
