"""Output Parser Protocol — defines the interface for structured parsing.

All output parsers implement this protocol, enabling the LLM to produce
validated, typed responses. Inspired by LangChain's BaseOutputParser
with parse() + get_format_instructions() pattern.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class OutputParser(Protocol[T]):
    """Protocol for structured output parsing.

    Generic over the output type T. Implementations must provide
    both parsing and format instruction generation.
    """

    def parse(self, text: str) -> T:
        """Parse raw LLM output text into structured type T.

        Args:
            text: Raw text from the LLM response.

        Returns:
            Parsed and validated output of type T.

        Raises:
            OutputParsingError: If parsing fails.
        """
        ...

    def get_format_instructions(self) -> str:
        """Generate format instructions for the LLM.

        Returns instructions that should be appended to the prompt
        so the LLM knows how to format its output.

        Returns:
            Format instruction string.
        """
        ...


class OutputParsingError(Exception):
    """Raised when output parsing fails.

    Attributes:
        raw_output: The original text that failed to parse.
        reason: Why parsing failed.
    """

    def __init__(self, raw_output: str, reason: str) -> None:
        self.raw_output = raw_output
        self.reason = reason
        super().__init__(f"Parsing failed: {reason}")
