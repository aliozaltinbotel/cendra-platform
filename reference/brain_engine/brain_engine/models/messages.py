"""Message types for unified LLM responses.

Defines the canonical message and tool-call models returned by all
provider implementations, regardless of the underlying API format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token consumption for a single LLM call.

    Attributes:
        input_tokens: Prompt / input tokens consumed.
        output_tokens: Completion / output tokens generated.
        total_tokens: Sum of input and output tokens.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model.

    Attributes:
        id: Provider-assigned tool-call identifier.
        name: Name of the tool to invoke.
        args: Arguments to pass to the tool.
    """

    id: str
    name: str
    args: dict[str, object]


@dataclass(slots=True)
class AIMessage:
    """Unified response from any LLM provider.

    Attributes:
        content: Generated text content (may be empty if tool_calls).
        tool_calls: List of tool invocations requested by the model.
        usage: Token usage statistics (``None`` when unavailable).
        model: Model identifier that produced this response.
        finish_reason: Why the model stopped generating.
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None
    model: str = ""
    finish_reason: str = "stop"
