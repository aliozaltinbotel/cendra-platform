"""Customer settings models for multi-tenant configuration.

Each customer (property manager) has their own AI settings:
guardrails, tone, tool toggles, custom instructions, and tags.
Stored in Redis, cached in memory with TTL.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GuardrailPriority(str, Enum):
    """When a guardrail should be applied."""

    ALWAYS = "ALWAYS"
    CONTEXTUAL = "CONTEXTUAL"
    CONDITIONAL = "CONDITIONAL"


class CustomerGuardrail(BaseModel):
    """A single guardrail rule.

    Guardrails are instructions injected into the system prompt
    to control AI behavior. They can be system defaults or
    customer-defined.

    Attributes:
        title: Short name (e.g. "No pricing confirmation").
        guardrail: The actual instruction text.
        flags: Which business flags activate this guardrail.
        priority: ALWAYS, CONTEXTUAL, or CONDITIONAL.
        is_default: True = system guardrail, False = customer-defined.
        label: Optional guest persona filter.
    """

    title: str = ""
    guardrail: str = ""
    flags: list[str] = Field(default_factory=list)
    priority: GuardrailPriority = GuardrailPriority.ALWAYS
    is_default: bool = True
    label: str = ""


class ToneType(str, Enum):
    """Predefined tone styles."""

    DEFAULT = "default"
    FRIENDLY = "friendly"
    FORMAL = "formal"
    PROFESSIONAL = "professional"
    NATURAL = "natural"
    CUSTOM = "custom"


class ToolToggle(BaseModel):
    """Per-customer tool enable/disable configuration."""

    search_internet: bool = True
    search_availability: bool = True
    suggest_alternative_listings: bool = True
    upsell_calculator: bool = True
    emergency_contact: bool = True
    rag_document_search: bool = True
    complaint_checker: bool = True
    ops_dispatcher: bool = True


class CustomerTag(BaseModel):
    """Customer-defined semantic tag for message categorization.

    Tags detect specific message patterns (e.g. "cleanliness complaint")
    and assign icons/labels for the PM dashboard.
    """

    title: str
    icon: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)


class CustomerSettings(BaseModel):
    """Full AI settings for one customer (property manager).

    Loaded from Redis on each request, cached with TTL.
    Drives guardrail selection, tone, tool availability,
    and post-processing behavior.
    """

    customer_id: str
    org_id: str = ""

    # AI behavior
    custom_instructions: str = Field(
        default="",
        description="Free-text system prompt override from customer",
    )
    signature: str = Field(
        default="",
        description="Staff name appended to responses",
    )
    tone_type: ToneType = ToneType.DEFAULT
    custom_tone_prompt: str = Field(
        default="",
        description="Full custom tone prompt (when tone_type=CUSTOM)",
    )
    respond_language: str = Field(
        default="",
        description="Force response language (empty = auto-detect)",
    )

    # Guardrails
    guardrails: list[CustomerGuardrail] = Field(default_factory=list)

    # Tools
    tools: ToolToggle = Field(default_factory=ToolToggle)

    # Tags
    tags: list[CustomerTag] = Field(default_factory=list)

    # Escalation
    escalation_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Knowledge base chunk IDs that trigger escalation",
    )

    def get_always_guardrails(self) -> list[CustomerGuardrail]:
        """Return guardrails that apply to every message."""
        return [g for g in self.guardrails if g.priority == GuardrailPriority.ALWAYS]

    def get_contextual_guardrails(
        self,
        active_flags: list[str],
    ) -> list[CustomerGuardrail]:
        """Return guardrails triggered by current business flags.

        Args:
            active_flags: List of active flag names (e.g. ["IS_EMERGENCY"]).

        Returns:
            Guardrails whose flags overlap with active_flags.
        """
        flag_set = set(active_flags)
        return [
            g for g in self.guardrails
            if g.priority == GuardrailPriority.CONTEXTUAL
            and bool(set(g.flags) & flag_set)
        ]

    def get_active_guardrails(
        self,
        active_flags: list[str],
    ) -> list[CustomerGuardrail]:
        """Return all guardrails that should be active for this request.

        Combines ALWAYS + matching CONTEXTUAL guardrails.

        Args:
            active_flags: Currently active business flags.

        Returns:
            Deduplicated list of applicable guardrails.
        """
        seen_titles: set[str] = set()
        result: list[CustomerGuardrail] = []

        for g in self.get_always_guardrails() + self.get_contextual_guardrails(active_flags):
            if g.title not in seen_titles:
                seen_titles.add(g.title)
                result.append(g)
        return result

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Check if a specific tool is enabled for this customer.

        Args:
            tool_name: Tool name matching ToolToggle field names.

        Returns:
            True if enabled, False otherwise.
        """
        return getattr(self.tools, tool_name, True)
