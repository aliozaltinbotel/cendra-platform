"""Structured output middleware — enforces output format via parsers.

Injects format instructions into prompts and parses LLM responses
through the configured OutputParser. Supports automatic retry on
parsing failure.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest
from brain_engine.structured_output.protocol import OutputParsingError

logger = logging.getLogger(__name__)


class StructuredOutputMiddleware:
    """Middleware that enforces structured output from the LLM.

    Adds format instructions to the prompt and parses the response.
    Stores both raw and parsed output for downstream consumers.

    Args:
        parser: Any OutputParser with parse() and get_format_instructions().
        inject_instructions: Whether to auto-inject format instructions.
    """

    def __init__(
        self,
        parser: Any,
        inject_instructions: bool = True,
    ) -> None:
        self._parser = parser
        self._inject = inject_instructions
        self._last_parsed: Any = None
        self._last_raw: str = ""
        self._parse_errors: int = 0

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "structured_output"

    @property
    def last_parsed(self) -> Any:
        """Last successfully parsed output."""
        return self._last_parsed

    @property
    def parse_error_count(self) -> int:
        """Number of parsing errors encountered."""
        return self._parse_errors

    def get_tools(self) -> list[Tool]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """Return format instructions if configured.

        Returns:
            Format instruction string.
        """
        if self._inject and hasattr(self._parser, "get_format_instructions"):
            return self._parser.get_format_instructions()
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Inject format instructions into system message.

        Args:
            messages: Current message list.

        Returns:
            Modified messages with instructions.
        """
        if not self._inject:
            return messages

        instructions = self.get_prompt_additions()
        if not instructions:
            return messages

        return _inject_instructions(messages, instructions)

    async def post_model_call(self, response: Any) -> Any:
        """Parse LLM response through the output parser.

        Args:
            response: Model response.

        Returns:
            Original response (parsed result stored in last_parsed).
        """
        text = _extract_text(response)
        if not text:
            return response

        self._last_raw = text

        try:
            self._last_parsed = self._parser.parse(text)
        except OutputParsingError:
            self._parse_errors += 1
            logger.warning("[StructuredOutputMW] parsing failed")
        except Exception:
            self._parse_errors += 1
            logger.warning("[StructuredOutputMW] unexpected error", exc_info=True)

        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through tool calls unchanged."""
        return await handler(request)


def _extract_text(response: Any) -> str:
    """Extract text from model response.

    Args:
        response: Model response.

    Returns:
        Text content or empty string.
    """
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    if hasattr(response, "choices"):
        choices = response.choices
        if choices:
            return str(choices[0].message.content or "")
    return ""


def _inject_instructions(
    messages: list[dict[str, Any]],
    instructions: str,
) -> list[dict[str, Any]]:
    """Inject format instructions into system message.

    Args:
        messages: Original messages.
        instructions: Format instructions.

    Returns:
        Modified messages.
    """
    result = list(messages)
    section = f"\n\n## Output Format\n{instructions}"

    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            result[i] = {
                **msg,
                "content": str(msg.get("content", "")) + section,
            }
            return result

    result.insert(0, {"role": "system", "content": section.strip()})
    return result
