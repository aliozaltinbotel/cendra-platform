"""Structured output system for Brain Engine.

Provides output parsers that validate and structure LLM responses
using JSON Schema, Pydantic models, and list extraction.

Example::

    from brain_engine.structured_output import PydanticOutputParser
    from pydantic import BaseModel

    class Decision(BaseModel):
        action: str
        reason: str

    parser = PydanticOutputParser(Decision)
    instructions = parser.get_format_instructions()
    result = parser.parse(llm_response)  # -> Decision(action=..., reason=...)
"""

from brain_engine.structured_output.json_parser import JsonOutputParser
from brain_engine.structured_output.list_parser import ListOutputParser
from brain_engine.structured_output.protocol import (
    OutputParser,
    OutputParsingError,
)
from brain_engine.structured_output.pydantic_parser import PydanticOutputParser
from brain_engine.structured_output.retry import OutputRetryParser

__all__ = [
    "JsonOutputParser",
    "ListOutputParser",
    "OutputParser",
    "OutputParsingError",
    "OutputRetryParser",
    "PydanticOutputParser",
]
