"""LLM-backed example-reply generator for the onboarding sandbox.

Wraps any :class:`brain_engine.models.base.BaseChatModel` (Anthropic,
OpenAI, …) so the bootstrap pipeline can swap the deterministic
:class:`TemplateExampleReplyGenerator` for a real LLM the moment a
provider key is configured.  Optional :class:`PropertyProfileStore`
injection grounds the reply in property-level facts (city, type,
amenities, base price) so the candidate is concrete instead of a
generic acknowledgement.

The generator is best-effort: if the model raises or returns an
empty reply, the bootstrap pipeline catches the exception, logs it,
and proceeds.  The caller is therefore free to use this generator
without an extra try/except wrapper at the call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

if TYPE_CHECKING:
    from brain_engine.models.base import BaseChatModel
    from brain_engine.profiles.models import PropertyProfile
    from brain_engine.profiles.store import PropertyProfileStore

__all__ = ["LLMExampleReplyGenerator"]


logger = structlog.get_logger(__name__)


_DEFAULT_SYSTEM_PROMPT: Final[str] = (
    "You are a short-term-rental property manager drafting a reply "
    "to a guest in their messaging app.  The reply will be reviewed "
    "by a human PM before it is sent — your job is to produce a "
    "useful starting draft, not to pretend you know facts that you "
    "do not.\n"
    "\n"
    "Grounding rules (violating any is a worse outcome than a vague "
    "reply):\n"
    "  • Use ONLY the concrete values that appear in the property "
    "context above (e.g. 'WiFi password: ...', 'Check-in time: ...', "
    "'Door code: ...').  When a value IS listed, you may quote it "
    "verbatim to answer the guest — that is the whole point of the "
    "context block.\n"
    "  • A line like 'WiFi password: (not configured in PMS)' or "
    "'Door code: (not configured in PMS)' means the PM has not "
    "provided that value yet.  In that case you MUST defer (e.g. "
    "'Bunu kontrol edip size en kısa sürede ileteceğim') and you "
    "MUST NOT invent a placeholder, sample, or guessed value.\n"
    "  • Never invent values that are NOT in the context: prices, "
    "fees, distances, addresses, time-of-day windows, dates, "
    "contact details, WiFi credentials, door / lockbox / access "
    "codes, parking spot numbers, or any other concrete detail.\n"
    "  • The 'PM-confirmed knowledge' block (when present) outranks "
    "the static profile.  If the PM's correction supplies a value "
    "the profile lacks (e.g. WiFi password, door code, special "
    "instructions), quote that PM correction verbatim.\n"
    "  • Door / lockbox / access / smart-lock codes are sensitive "
    "credentials.  Even when the context lists a concrete value you "
    "MUST NOT release it unless the 'PM-confirmed knowledge' block "
    "contains a line that explicitly confirms ID/passport "
    "verification has been completed for THIS guest (e.g. 'Passport "
    "verified', 'ID confirmed', 'Kimlik doğrulandı', 'Паспорт "
    "проверен').  When that confirmation is absent, refuse the "
    "code and use one of these exact phrasings so downstream "
    "guardrail tooling can detect the gate:\n"
    "      EN: 'I cannot share the door code without passport "
    "verification — please send a clear photo of your ID first.'\n"
    "      TR: 'Pasaport doğrulaması olmadan kapı kodunu "
    "paylaşamam — lütfen önce kimlik fotoğrafınızı gönderin.'\n"
    "      RU: 'Я не могу отправить код без паспорта — пришлите, "
    "пожалуйста, фото документа.'\n"
    "  • Never confirm or deny services (parking, late check-out, "
    "early check-in, pets, smoking, extra beds, transfers, "
    "cleaning add-ons, …) unless they are listed in the property "
    "context above.\n"
    "  • If the guest asks for a fact that is missing from the "
    "context, do NOT guess — defer politely (e.g. 'Let me "
    "double-check this and get back to you shortly') and stop "
    "there.\n"
    "  • Match the guest's language when it is obvious; otherwise "
    "reply in English.\n"
    "  • Stay warm, professional, and concise (1–3 short "
    "sentences).  Return only the reply text, with no extra "
    "headers or sign-off boilerplate."
)


_GUEST_MESSAGE_PREVIEW_LIMIT: Final[int] = 4_000


class LLMExampleReplyGenerator:
    """Produce a candidate reply by delegating to a chat model.

    The generator's :attr:`name` follows ``"llm:<provider>:<model>"``
    so the sandbox row's ``generated_by`` field stays self-describing
    when several backends are deployed in parallel (A/B comparisons).

    Attributes:
        name: Stable generator identifier (``"llm:<provider>:<model>"``).
        _model: The wrapped chat model.
        _profile_store: Optional store used to fetch grounding context.
        _system_prompt: Override for :data:`_DEFAULT_SYSTEM_PROMPT`.
        _log: Structured logger bound to this component.
    """

    def __init__(
        self,
        model: BaseChatModel,
        *,
        profile_store: PropertyProfileStore | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._profile_store = profile_store
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self.name: Final[str] = (
            f"llm:{getattr(model, 'provider', 'unknown')}:"
            f"{getattr(model, 'model', 'unknown')}"
        )
        self._log = logger.bind(component="sandbox_llm_generator")

    async def generate(
        self,
        *,
        property_id: str,
        guest_message: str,
        language: str = "",
        pm_facts: tuple[str, ...] = (),
    ) -> str:
        """Return the example reply for ``guest_message``.

        Returns an empty string when the model cannot produce any
        content; the bootstrap pipeline treats that as a soft failure
        and skips the row, so the caller does not need to special-case
        empty replies here.

        ``pm_facts`` carries PM-confirmed knowledge lines pulled by
        the caller from the active :class:`PmFactStore`.  Each entry
        is rendered verbatim into the prompt's grounding block so the
        model can quote them directly when answering the guest.
        """
        guest_text = guest_message.strip()
        if not guest_text:
            return ""

        # Defend against pathological histories — the chat APIs choke
        # on multi-MB prompts and the user's own message is the only
        # context this generator strictly needs.
        if len(guest_text) > _GUEST_MESSAGE_PREVIEW_LIMIT:
            guest_text = guest_text[:_GUEST_MESSAGE_PREVIEW_LIMIT]

        profile = await self._lookup_profile(property_id)
        user_payload = self._build_user_payload(
            property_id=property_id,
            guest_message=guest_text,
            language=language,
            profile=profile,
            pm_facts=pm_facts,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_payload},
        ]

        try:
            response = await self._model.invoke(messages)
        except Exception:  # noqa: BLE001 - sandbox is best-effort
            self._log.exception(
                "sandbox.llm_invoke_failed",
                property_id=property_id,
                generator=self.name,
            )
            return ""

        reply = (response.content or "").strip()
        self._log.debug(
            "sandbox.llm_reply_generated",
            property_id=property_id,
            generator=self.name,
            language=language or "—",
            reply_len=len(reply),
        )
        return reply

    # ── Helpers ───────────────────────────────────────────── #

    async def _lookup_profile(
        self,
        property_id: str,
    ) -> PropertyProfile | None:
        """Fetch the property profile if a store is configured."""
        if self._profile_store is None:
            return None
        try:
            return await self._profile_store.get(property_id)
        except Exception:  # noqa: BLE001 - grounding is best-effort
            self._log.warning(
                "sandbox.profile_lookup_failed",
                property_id=property_id,
            )
            return None

    def _build_user_payload(
        self,
        *,
        property_id: str,
        guest_message: str,
        language: str,
        profile: PropertyProfile | None,
        pm_facts: tuple[str, ...] = (),
    ) -> str:
        """Assemble the user-role message for the chat model."""
        lines: list[str] = []
        lines.append(f"Property id: {property_id}")
        if profile is not None:
            lines.extend(_render_profile_lines(profile))
        # PM-confirmed knowledge is the freshest grounding signal and
        # outranks the static profile (the manager corrected the
        # engine for a reason).  Newest-first ordering is the caller's
        # responsibility — the LLM weighs the top of the block more
        # heavily.
        if pm_facts:
            lines.append("")
            lines.append("PM-confirmed knowledge (use verbatim):")
            for fact in pm_facts:
                cleaned = fact.strip()
                if not cleaned:
                    continue
                lines.append(f"  - {cleaned}")
        if language:
            lines.append(f"Guest language hint: {language}")
        lines.append("")
        lines.append("Guest message:")
        lines.append(guest_message)
        lines.append("")
        lines.append("Draft the reply.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Profile rendering — kept module-local so the prompt shape lives next
# to the generator that owns it.
# ---------------------------------------------------------------------------


def _render_profile_lines(profile: PropertyProfile) -> list[str]:
    """Render the most useful profile facts as prompt-ready lines."""
    lines: list[str] = []
    if profile.title:
        lines.append(f"Title: {profile.title}")
    if profile.property_type:
        lines.append(f"Type: {profile.property_type}")
    location = ", ".join(
        part for part in (profile.city, profile.country) if part
    )
    if location:
        lines.append(f"Location: {location}")
    if profile.max_occupancy:
        lines.append(f"Max occupancy: {profile.max_occupancy}")
    if profile.bedrooms is not None:
        lines.append(f"Bedrooms: {profile.bedrooms}")
    if profile.bathrooms is not None:
        lines.append(f"Bathrooms: {profile.bathrooms}")
    if profile.base_price and profile.base_currency:
        lines.append(
            f"Base price: {profile.base_price} {profile.base_currency}"
        )
    if profile.amenity_codes:
        # Cap the amenity list so the prompt stays bounded even for
        # listings with dozens of codes.
        preview = ", ".join(profile.amenity_codes[:20])
        lines.append(f"Amenities: {preview}")
    # Surface the concrete service flags + check-in/out windows + a
    # short description from the unified-property payload.  Without
    # these lines the strict-grounding system prompt forces the model
    # into "let me double-check" replies even when the property
    # clearly advertises WiFi or paid parking.
    lines.extend(_render_static_payload_lines(profile.static_payload))
    return lines


_DESCRIPTION_PREVIEW_LIMIT: Final[int] = 800


def _render_static_payload_lines(
    static_payload: Mapping[str, Any],
) -> list[str]:
    """Render service-confirmation lines from the raw unified payload.

    The harvester keeps a bag of facts (``has_wifi``, ``has_parking``,
    ``check_in_time`` …) on :attr:`PropertyProfile.static_payload` that
    the strict-grounding LLM prompt needs to answer "is there WiFi?"
    questions confidently.  All reads are defensive — missing or
    malformed keys leave the line out instead of raising.
    """
    if not static_payload:
        return []
    lines: list[str] = []

    def _flag(key: str, label: str) -> None:
        value = static_payload.get(key)
        if value is True:
            lines.append(f"{label}: yes")
        elif value is False:
            lines.append(f"{label}: no")

    _flag("has_wifi", "WiFi available")
    _flag("has_parking", "Parking available")
    _flag("pets_allowed", "Pets allowed")
    _flag("instant_bookable", "Instant booking")

    # WiFi credentials — render BOTH known and unknown values
    # explicitly so the model never guesses.  When the listing
    # advertises ``has_wifi: yes`` but the PMS row has no concrete
    # network/password, surfacing "(not configured)" forces the
    # strict-grounding prompt down the deferral branch instead of
    # hallucinating a placeholder like "123!123".
    has_wifi = static_payload.get("has_wifi")
    wifi_network = static_payload.get("wifi_network")
    wifi_password = static_payload.get("wifi_password")
    if isinstance(wifi_network, str) and wifi_network.strip():
        lines.append(f"WiFi network name: {wifi_network.strip()}")
    elif has_wifi is True:
        lines.append("WiFi network name: (not configured in PMS)")
    if isinstance(wifi_password, str) and wifi_password.strip():
        lines.append(f"WiFi password: {wifi_password.strip()}")
    elif has_wifi is True:
        lines.append("WiFi password: (not configured in PMS)")
    # Door / lockbox code follows the same logic — explicit absence
    # blocks credential hallucination.
    door_code = static_payload.get("door_code")
    if isinstance(door_code, str) and door_code.strip():
        lines.append(f"Door code: {door_code.strip()}")
    else:
        lines.append("Door code: (not configured in PMS)")

    check_in = static_payload.get("check_in_time")
    if isinstance(check_in, str) and check_in.strip():
        lines.append(f"Check-in time: {check_in.strip()}")
    check_out = static_payload.get("check_out_time")
    if isinstance(check_out, str) and check_out.strip():
        lines.append(f"Check-out time: {check_out.strip()}")

    min_nights = static_payload.get("min_nights")
    if isinstance(min_nights, int) and min_nights > 0:
        lines.append(f"Minimum stay: {min_nights} nights")
    max_nights = static_payload.get("max_nights")
    if isinstance(max_nights, int) and max_nights > 0:
        lines.append(f"Maximum stay: {max_nights} nights")

    # Pull a single description preview — prefer the canonical
    # "Property_Description" entry, fall back to the first non-empty
    # text. Capped so the prompt stays bounded.
    descriptions = static_payload.get("descriptions")
    if isinstance(descriptions, list):
        chosen = ""
        for entry in descriptions:
            if not isinstance(entry, Mapping):
                continue
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if entry.get("typeCode") == "Property_Description":
                chosen = text.strip()
                break
            if not chosen:
                chosen = text.strip()
        if chosen:
            lines.append(f"Description: {chosen[:_DESCRIPTION_PREVIEW_LIMIT]}")

    # Human-readable amenity names complement the code list above; the
    # LLM does noticeably better with "Internet, Wireless, Paid
    # Parking" than the raw enum codes when answering plain-language
    # guest questions.
    amenities = static_payload.get("amenities")
    if isinstance(amenities, list):
        names: list[str] = []
        for entry in amenities:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        if names:
            lines.append(f"Amenities (names): {', '.join(names[:25])}")

    return lines
