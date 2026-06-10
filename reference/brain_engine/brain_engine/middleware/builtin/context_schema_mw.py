"""Context schema middleware — typed runtime context injection.

Validates and injects typed context into agent execution.
Ensures that runtime context conforms to a Pydantic schema
before the agent starts processing.

Example::

    class PropertyContext(BaseModel):
        property_id: str
        owner_id: str
        timezone: str = "UTC"

    mw = ContextSchemaMiddleware(schema=PropertyContext)
    stack.add(mw)

Based on: LangChain context_schema parameter on create_agent.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ContextValidationError(Exception):
    """Raised when context fails schema validation.

    Attributes:
        errors: Pydantic validation errors.
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        details = "; ".join(
            f"{e.get('loc', '?')}: {e.get('msg', '?')}"
            for e in errors
        )
        super().__init__(f"Context validation failed: {details}")


class ContextSchemaMiddleware:
    """Middleware that validates runtime context against a schema.

    Extracts context from the system message or config, validates
    it against a Pydantic model, and makes the validated context
    available to downstream middleware and tools.

    Args:
        schema: Pydantic model class for context validation.
        context_key: Key in config dict holding context data.
        inject_into_prompt: Whether to add context summary to prompt.
    """

    def __init__(
        self,
        schema: type[BaseModel],
        context_key: str = "context",
        inject_into_prompt: bool = True,
    ) -> None:
        self._schema = schema
        self._context_key = context_key
        self._inject = inject_into_prompt
        self._validated: BaseModel | None = None

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "context_schema"

    @property
    def validated_context(self) -> BaseModel | None:
        """Return the last validated context, if any."""
        return self._validated

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools."""
        return []

    def get_prompt_additions(self) -> str:
        """Inject validated context into prompt.

        Returns:
            Context summary string.
        """
        if not self._inject or not self._validated:
            return ""
        return _format_context(self._validated)

    def validate(self, data: dict[str, Any]) -> BaseModel:
        """Validate context data against the schema.

        Args:
            data: Raw context dict.

        Returns:
            Validated Pydantic model instance.

        Raises:
            ContextValidationError: If validation fails.
        """
        try:
            self._validated = self._schema(**data)
            return self._validated
        except ValidationError as exc:
            raise ContextValidationError(exc.errors()) from exc

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Pass through (validation happens at init/config time).

        Args:
            messages: Input messages.

        Returns:
            Unmodified messages.
        """
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        return response


class DynamicPromptMiddleware:
    """Middleware that generates system prompts dynamically.

    Calls a prompt generator function with the current state
    and config to produce a fresh system prompt before each
    model call.

    Args:
        generator: Async function(state, config) -> str.
    """

    def __init__(
        self,
        generator: Any,
    ) -> None:
        self._generator = generator
        self._last_prompt: str = ""

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "dynamic_prompt"

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools."""
        return []

    def get_prompt_additions(self) -> str:
        """Return the last generated prompt addition."""
        return self._last_prompt

    async def generate(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> str:
        """Generate a dynamic prompt from state and config.

        Args:
            state: Current agent state.
            config: Runtime configuration.

        Returns:
            Generated prompt string.
        """
        import inspect

        if inspect.iscoroutinefunction(self._generator):
            self._last_prompt = await self._generator(
                state, config or {},
            )
        else:
            self._last_prompt = self._generator(state, config or {})
        return self._last_prompt

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Inject dynamic prompt as system message.

        Args:
            messages: Input messages.

        Returns:
            Messages with dynamic prompt injected.
        """
        if not self._last_prompt:
            return messages
        return _inject_system_prompt(messages, self._last_prompt)

    async def post_model_call(self, response: Any) -> Any:
        """Pass through."""
        return response


def _format_context(model: BaseModel) -> str:
    """Format a Pydantic model as a prompt context block.

    Args:
        model: Validated context model.

    Returns:
        Formatted context string.
    """
    lines = ["\n[Runtime Context]"]
    for field_name, value in model.model_dump().items():
        lines.append(f"  {field_name}: {value}")
    return "\n".join(lines) + "\n"


def _inject_system_prompt(
    messages: list[dict[str, str]],
    prompt: str,
) -> list[dict[str, str]]:
    """Inject or append to system message.

    Args:
        messages: Message list.
        prompt: Prompt text to inject.

    Returns:
        Updated message list.
    """
    result = list(messages)
    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            result[i] = {
                **msg,
                "content": msg["content"] + "\n\n" + prompt,
            }
            return result
    result.insert(0, {"role": "system", "content": prompt})
    return result
