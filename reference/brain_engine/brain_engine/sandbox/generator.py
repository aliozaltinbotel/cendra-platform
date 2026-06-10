"""Example-reply generators for the onboarding sandbox.

The generator Protocol decouples the reply surface from the concrete
LLM backend: when OpenAI / Anthropic credentials are configured the
pipeline can wire an LLM-backed generator; otherwise the deterministic
:class:`TemplateExampleReplyGenerator` keeps the sandbox running.
"""

from __future__ import annotations

from typing import Final, Protocol, runtime_checkable

import structlog

__all__ = [
    "ExampleReplyGenerator",
    "TemplateExampleReplyGenerator",
]


logger = structlog.get_logger(__name__)


_TEMPLATE_ID: Final[str] = "template"


@runtime_checkable
class ExampleReplyGenerator(Protocol):
    """Produce a candidate reply for one unanswered guest message.

    Implementations must always return a non-empty string; callers
    treat an empty return value as a generator bug and will refuse
    to store the row.
    """

    name: str

    async def generate(
        self,
        *,
        property_id: str,
        guest_message: str,
        language: str = "",
        pm_facts: tuple[str, ...] = (),
    ) -> str:
        """Return the example reply for ``guest_message``.

        ``pm_facts`` carries PM-confirmed knowledge lines pulled from
        the active fact store at request time.  Generators that want
        to ground the reply (e.g. an LLM) must fold them into their
        prompt; deterministic generators (templates) may ignore them
        but MUST accept the keyword to keep the protocol uniform.
        """
        ...


class TemplateExampleReplyGenerator:
    """Deterministic fallback generator.

    Emits a neutral acknowledgement that mirrors the guest's language
    hint.  Intended as a safety net so the sandbox endpoint can still
    demo even when no LLM is plugged in; production wiring should
    prefer a real LLM-backed generator.
    """

    name: Final[str] = _TEMPLATE_ID

    def __init__(self) -> None:
        self._log = logger.bind(component="sandbox_template_generator")

    async def generate(
        self,
        *,
        property_id: str,
        guest_message: str,
        language: str = "",
        pm_facts: tuple[str, ...] = (),
    ) -> str:
        """Return a neutral acknowledgement reply.

        ``pm_facts`` is accepted for protocol uniformity but ignored —
        the template generator has no way to splice grounding facts
        into its three hard-coded acknowledgement strings.
        """
        del pm_facts  # template path stays deterministic
        trimmed = guest_message.strip()
        preview = trimmed if len(trimmed) < 120 else trimmed[:117] + "…"
        if language.lower().startswith("tr"):
            body = (
                "Merhaba! Mesajınız için teşekkür ederim. "
                "Detayları kontrol edip size en kısa sürede dönüş yapacağım."
            )
        elif language.lower().startswith("ru"):
            body = (
                "Здравствуйте! Спасибо за сообщение. "
                "Я уточню детали и вернусь к вам как можно скорее."
            )
        else:
            body = (
                "Hi there, thanks for your message! "
                "I'll check the details and get back to you shortly."
            )
        self._log.debug(
            "sandbox.template_reply_generated",
            property_id=property_id,
            guest_preview=preview,
            language=language or "—",
        )
        return body
