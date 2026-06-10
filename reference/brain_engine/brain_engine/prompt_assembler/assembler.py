"""Prompt Assembler - Constructs LLM prompts from Jinja2 templates and runtime context.

Combines system prompts, memory context (semantic + episodic), slot status,
conversation history, and domain-specific rules into a final prompt ready
for the LLM. Uses Jinja2 templating for flexible, maintainable prompt
construction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, BaseLoader

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"


class PromptAssembler:
    """Assembles LLM prompts from Jinja2 templates and dynamic context.

    Loads prompt templates from a directory and renders them with runtime
    data including role, memory context, slot state, conversation history,
    and domain rules.

    Args:
        template_dir: Directory containing Jinja2 template files.
            Defaults to the built-in templates directory.
        autoescape: Whether to enable Jinja2 autoescaping. Disabled by
            default since we are generating prompts, not HTML.
    """

    def __init__(
        self,
        template_dir: str | Path | None = None,
        autoescape: bool = False,
    ) -> None:
        self._template_dir = Path(template_dir) if template_dir else _DEFAULT_TEMPLATE_DIR

        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            autoescape=autoescape,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

        # Cache for string-based templates (no file)
        self._string_env = Environment(
            loader=BaseLoader(),
            autoescape=autoescape,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def assemble(
        self,
        template_name: str = "base_system_prompt.txt",
        role: str = "a helpful AI assistant",
        context: str = "",
        slots: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
        rules: list[str] | None = None,
        memory_context: str = "",
        extra_vars: dict[str, Any] | None = None,
    ) -> str:
        """Assemble a complete prompt from a template and context variables.

        Args:
            template_name: Name of the template file to load from template_dir.
            role: The agent's role description.
            context: Domain-specific context or situation description.
            slots: Slot state dict mapping slot names to SlotInfo-like dicts
                with keys: value, required, filled, description.
            history: Conversation history as list of {"role": ..., "content": ...}.
            rules: List of rules or constraints for the agent.
            memory_context: Relevant knowledge from semantic/episodic memory.
            extra_vars: Additional template variables for custom templates.

        Returns:
            The fully rendered prompt string.
        """
        template = self._env.get_template(template_name)

        variables: dict[str, Any] = {
            "role": role,
            "context": context,
            "slots": slots or {},
            "history": history or [],
            "rules": rules or [],
            "memory_context": memory_context,
        }
        if extra_vars:
            variables.update(extra_vars)

        rendered = template.render(**variables)

        logger.debug(
            "Assembled prompt from template=%s, len=%d chars",
            template_name,
            len(rendered),
        )
        return rendered.strip()

    def assemble_from_string(
        self,
        template_string: str,
        **variables: Any,
    ) -> str:
        """Assemble a prompt from an inline Jinja2 template string.

        Useful for one-off or dynamically constructed templates that
        do not have a corresponding file on disk.

        Args:
            template_string: A Jinja2 template as a raw string.
            **variables: Template variables to render with.

        Returns:
            The rendered prompt string.
        """
        template = self._string_env.from_string(template_string)
        rendered = template.render(**variables)
        return rendered.strip()

    def assemble_messages(
        self,
        template_name: str = "base_system_prompt.txt",
        role: str = "a helpful AI assistant",
        context: str = "",
        slots: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
        rules: list[str] | None = None,
        memory_context: str = "",
        extra_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Assemble a complete message list ready for LLM API calls.

        Returns the system prompt as a system message, followed by the
        conversation history messages. This is the format expected by
        OpenAI/LiteLLM chat completion APIs.

        Args:
            Same as assemble().

        Returns:
            List of message dicts [{"role": "system", "content": ...}, ...].
        """
        system_prompt = self.assemble(
            template_name=template_name,
            role=role,
            context=context,
            slots=slots,
            # History is included in the message list, not the system prompt
            history=None,
            rules=rules,
            memory_context=memory_context,
            extra_vars=extra_vars,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        if history:
            messages.extend(history)

        return messages

    def list_templates(self) -> list[str]:
        """List all available template files in the template directory.

        Returns:
            List of template filenames.
        """
        if not self._template_dir.exists():
            return []
        return [
            f.name
            for f in self._template_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ]
