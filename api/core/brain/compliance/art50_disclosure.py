"""EU AI Act Art. 50 chatbot disclosure helper.

Article 50 obliges providers of AI systems "intended to interact
directly with natural persons" to inform the persons that they
are interacting with an AI.  This module ships:

- :class:`DisclosureLocale` — canonical locale identifiers.
- :data:`DEFAULT_DISCLOSURES` — minimal disclosure strings per
  locale, sourced from EU Commission guidance May 2026.
- :class:`Art50Disclosure` — value object; pairs the locale with
  the rendered text the engine inserts on the first outbound
  message of every conversation.
- :func:`disclosure_for` — helper used by the conversation
  service to fetch the right text (or fall back to English).

The module deliberately stops at producing the text — *where* the
disclosure is inserted (the first outbound message, the SSE
opening event, the WhatsApp sticky greeting, ...) is the caller's
choice.  Centralising the wording here means a single review by
legal updates every channel at once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_DISCLOSURES",
    "Art50Disclosure",
    "DisclosureLocale",
    "disclosure_for",
]


class DisclosureLocale(StrEnum):
    """Canonical disclosure locales."""

    EN = "en"
    ES = "es"
    DE = "de"
    FR = "fr"
    PT = "pt"


DEFAULT_DISCLOSURES: Final[Mapping[DisclosureLocale, str]] = {
    DisclosureLocale.EN: (
        "You're chatting with an AI assistant on behalf of your host.  A human can take over at any time — just ask."
    ),
    DisclosureLocale.ES: (
        "Estás hablando con un asistente de IA en nombre de tu "
        "anfitrión.  Una persona puede intervenir en cualquier "
        "momento — solo dilo."
    ),
    DisclosureLocale.DE: (
        "Sie chatten mit einem KI-Assistenten im Namen Ihres "
        "Gastgebers.  Ein Mensch kann jederzeit übernehmen — "
        "sagen Sie einfach Bescheid."
    ),
    DisclosureLocale.FR: (
        "Vous discutez avec un assistant IA au nom de votre hôte. "
        "Une personne peut prendre le relais à tout moment — "
        "dites-le simplement."
    ),
    DisclosureLocale.PT: (
        "Está a conversar com um assistente de IA em nome do seu "
        "anfitrião.  Uma pessoa pode assumir a qualquer momento "
        "— basta pedir."
    ),
}


@dataclass(frozen=True, slots=True)
class Art50Disclosure:
    """Pair of (locale, text) the engine inserts.

    Frozen so callers can pass the value through middleware
    without worrying about late mutation.
    """

    locale: DisclosureLocale
    text: str


def disclosure_for(
    locale: str,
    *,
    fallback: DisclosureLocale = DisclosureLocale.EN,
) -> Art50Disclosure:
    """Return the disclosure for ``locale``, falling back to English.

    Args:
        locale: BCP-47 / ISO-639 short code (case-insensitive).
        fallback: Locale used when ``locale`` is unrecognised or
            absent from :data:`DEFAULT_DISCLOSURES`.

    Returns:
        :class:`Art50Disclosure` carrying the resolved locale and
        the rendered text.
    """
    candidate = locale.strip().lower()
    try:
        resolved = DisclosureLocale(candidate)
    except ValueError:
        resolved = fallback
    if resolved not in DEFAULT_DISCLOSURES:
        resolved = fallback
    return Art50Disclosure(
        locale=resolved,
        text=DEFAULT_DISCLOSURES[resolved],
    )
