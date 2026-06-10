"""Tone system — per-customer response style management.

Loads tone prompt templates and injects them into the system
prompt to control AI response style (friendly, formal, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path

from brain_engine.customer.models import CustomerSettings, ToneType

logger = logging.getLogger(__name__)

_TONES_DIR = Path(__file__).parent.parent.parent / "config" / "tones"

# Core rules appended to every tone
_CORE_RULES = """
CORE RULES (always apply regardless of tone):
- Never invent or assume information not provided by tools or knowledge base
- Avoid bold formatting (**text**)
- NEVER include timestamps or date markers
- Do not include date/time references unrelated to booking info
- Respond in the same language as the guest
"""


def get_tone_prompt(settings: CustomerSettings) -> str:
    """Get the full tone prompt for a customer.

    Selects the appropriate tone template based on customer
    settings. Falls back to default if custom tone not found.

    Args:
        settings: Customer settings with tone_type.

    Returns:
        Complete tone prompt string for system prompt injection.
    """
    if settings.tone_type == ToneType.CUSTOM and settings.custom_tone_prompt:
        return settings.custom_tone_prompt + _CORE_RULES

    tone_text = _load_tone_file(settings.tone_type.value)
    return tone_text + _CORE_RULES


def _load_tone_file(tone_name: str) -> str:
    """Load a tone template file from config/tones/.

    Args:
        tone_name: Tone file name (without .txt extension).

    Returns:
        Tone template text, or default if file not found.
    """
    path = _TONES_DIR / f"{tone_name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    default_path = _TONES_DIR / "default.txt"
    if default_path.exists():
        return default_path.read_text(encoding="utf-8").strip()

    return _FALLBACK_DEFAULT_TONE


_FALLBACK_DEFAULT_TONE = """## Response Style

You are a friendly, professional property management assistant.

Guidelines:
- Be warm but concise
- Use a conversational tone
- Avoid excessive formality
- Match the guest's energy level
- Use the guest's name when available
- Keep responses focused and helpful
- Use emojis sparingly (1-2 max per message)
"""
