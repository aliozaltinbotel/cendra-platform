"""Custom Tone Generator — creates tone prompts from free-text PM instructions.

Takes a PM's natural language description of desired tone and
generates a structured tone prompt that can be injected into
the system prompt. Supports per-customer custom tones.
"""

from __future__ import annotations

import json
import logging

import litellm
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o"
_TEMPERATURE = 0.3

# Core rules that apply regardless of custom tone
_CORE_RULES = [
    "Never invent or assume information not provided by tools or knowledge base",
    "Avoid bold formatting (**text**)",
    "NEVER include timestamps or date markers",
    "Do not include date/time references unrelated to booking info",
    "Respond in the same language as the guest",
]


class CustomToneRequest(BaseModel):
    """Input to POST /api/v1/custom-tone."""

    customer_id: str
    org_id: str = ""
    instructions: str = Field(
        ..., description="PM's free-text tone description",
    )
    example_messages: list[str] = Field(
        default_factory=list,
        description="Example messages in desired tone",
    )
    base_tone: str = Field(
        default="default",
        description="Base tone to start from: default, friendly, formal, etc.",
    )


class CustomToneResponse(BaseModel):
    """Output of custom tone generation."""

    status: bool = True
    tone_prompt: str = ""
    tone_name: str = ""
    error: str | None = None


async def generate_custom_tone(
    request: CustomToneRequest,
) -> CustomToneResponse:
    """Generate a structured tone prompt from PM instructions.

    Takes free-text instructions like "Be very friendly, use
    emojis, speak in first person" and produces a structured
    tone prompt ready for system prompt injection.

    Args:
        request: Tone generation request with PM instructions.

    Returns:
        Generated tone prompt and name.
    """
    examples_text = ""
    if request.example_messages:
        examples_text = "\n\nExample messages in desired tone:\n" + "\n".join(
            f'- "{msg}"' for msg in request.example_messages[:5]
        )

    prompt = (
        f"Base tone: {request.base_tone}\n\n"
        f"PM instructions:\n{request.instructions}"
        f"{examples_text}\n\n"
        "Generate a tone prompt based on these instructions."
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=600,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        tone_prompt = data.get("tone_prompt", "")

        # Append core rules
        core_block = "\n\nCORE RULES (always apply):\n" + "\n".join(
            f"- {r}" for r in _CORE_RULES
        )
        full_prompt = tone_prompt + core_block

        return CustomToneResponse(
            tone_prompt=full_prompt,
            tone_name=data.get("tone_name", "custom"),
        )
    except Exception as exc:
        logger.error("Custom tone generation failed: %s", exc)
        return CustomToneResponse(status=False, error=str(exc))


_SYSTEM_PROMPT = """Generate a structured tone prompt for an AI property management assistant.

The PM describes how they want the AI to sound. Convert their description
into a clear, actionable tone prompt with specific guidelines.

Cover these aspects:
- Formality level (casual, balanced, formal)
- Emoji usage (none, sparse 0-1, moderate 2-3, generous)
- Greeting style
- How to use guest's name
- Sentence length and structure
- Specific words/phrases to use or avoid
- Sign-off style

Return JSON:
{
    "tone_name": "warm_professional",
    "tone_prompt": "## Response Style: Warm Professional\\n\\nGuidelines:\\n- Be warm and approachable..."
}

The tone_prompt should be ready to inject into a system prompt.
Start with '## Response Style: {tone_name}' header.
Include 6-10 specific guidelines.
"""
