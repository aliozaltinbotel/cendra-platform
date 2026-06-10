"""Thanks response generator tool.

Generates a polite courtesy reply when guest's message
is just a thank-you with no further questions.
"""

from __future__ import annotations

import logging

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


@tool(description=(
    "Generate a polite courtesy reply when the guest's message "
    "is ONLY a thank you or acknowledgment with no questions. "
    "Use only when IS_THANKS_ONLY flag is true. "
    "Do NOT use when guest has any question, complaint, or request alongside thanks. "
    "Do NOT use for greetings without thanks. "
    "Do NOT use when guest says 'ok' or 'got it' with a follow-up question."
))
async def thanks_response_generator(
    guest_name: str = "",
    language: str = "en",
    runtime: ToolRuntime | None = None,
) -> str:
    """Generate a courtesy thank-you response.

    Args:
        guest_name: Guest's name for personalization.
        language: Response language code.
        runtime: Injected runtime context.

    Returns:
        Polite courtesy response.
    """
    return _get_thanks_response(guest_name, language)


def _get_thanks_response(name: str, language: str) -> str:
    """Select language-appropriate thanks response.

    Args:
        name: Guest name for personalization.
        language: ISO 639-1 language code.

    Returns:
        Courtesy response text.
    """
    name_part = f" {name}" if name else ""

    responses = {
        "en": f"You're welcome{name_part}! Don't hesitate to reach out if you need anything else.",
        "tr": f"Rica ederim{name_part}! Başka bir şeye ihtiyacınız olursa çekinmeden yazın.",
        "de": f"Gerne geschehen{name_part}! Zögern Sie nicht, sich zu melden.",
        "fr": f"De rien{name_part} ! N'hésitez pas si vous avez besoin de quoi que ce soit.",
        "es": f"De nada{name_part}! No dude en contactarnos si necesita algo más.",
        "ru": f"Пожалуйста{name_part}! Обращайтесь, если что-то понадобится.",
        "pt": f"De nada{name_part}! Não hesite em entrar em contato.",
    }

    return responses.get(language, responses["en"])
