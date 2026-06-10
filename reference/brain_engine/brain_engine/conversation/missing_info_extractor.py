# ruff: noqa: RUF001, RUF003
# RUF001 / RUF002 / RUF003 (ambiguous unicode) suppressed file-wide
# because the live LLM emits, and the prompt instructs the model to
# emit, Turkish letters ``ı`` / ``ş`` / ``ğ`` and Cyrillic.  The
# substring matcher in `_DEFERRAL_PHRASES` and the worked examples
# in `_SYSTEM_PROMPT` lose their meaning if those letters get
# flattened to Latin equivalents.
"""Missing Information Extractor — identifies unresolved guest inquiries.

Analyzes the *latest* AI response (not full history) to find a single
gap the AI deferred on.  Returns structured payload feeding the BRAIN
flag in PM Chat.

Two-stage gating keeps spurious flags out:

1. :func:`response_has_deferral` — fast string check on the latest AI
   response in TR/EN/RU.  When the AI gave a definitive answer the
   extractor LLM is **not** called at all — saves tokens and silences
   false positives that the extractor's history-aware view used to
   produce.
2. The extractor LLM itself is now scoped to the *latest exchange*
   only (system prompt rewrite); historical deferrals leaking into
   every subsequent turn was the root cause of the "4 BRAIN flags
   for one WiFi question" symptom in PM Chat.
"""

from __future__ import annotations

import json
import logging
import re

import litellm
from pydantic import BaseModel, Field

from brain_engine.context.token_counter import truncate_to_tokens
from brain_engine.conversation.deferral_phrases import load_deferral_phrases
from brain_engine.conversation.extractor_settings import (
    guest_message_token_budget,
)
from brain_engine.patterns.language_detector import (
    get_shared_language_detector,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1

# Substring-matched against the lowercased AI response — when NONE
# match the AI gave a definitive answer and the extractor LLM is
# NOT called (cost / latency guard).  The actual phrases live in
# ``deferral_phrases.yaml`` next to the loader so an operator can
# add / remove entries without a code release.
_DEFERRAL_PHRASES: tuple[str, ...] = load_deferral_phrases()

_WHITESPACE_RE = re.compile(r"\s+")


def response_has_deferral(ai_message: str) -> bool:
    """Return True when the latest AI response defers to a human.

    Pure string match in TR / EN / RU — intentionally kept fast and
    dependency-free so it can guard the extractor LLM call without
    adding latency or cost.  Returns False on empty input so the
    caller can decide whether to flag empty responses elsewhere.

    Args:
        ai_message: The latest AI response text.

    Returns:
        True if a known deferral phrase appears in ``ai_message``.
    """
    if not ai_message:
        return False
    haystack = _WHITESPACE_RE.sub(" ", ai_message.lower())
    return any(phrase in haystack for phrase in _DEFERRAL_PHRASES)


class MissingInfoRequest(BaseModel):
    """Input to POST /api/v1/extract-missing-information."""

    customer_id: str = ""
    org_id: str = ""
    message_id: str = ""
    ai_message: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)


class MissingInfoResponse(BaseModel):
    """Output of missing information extraction."""

    status: bool = True
    missing_information: str = ""
    answered_questions: str = ""
    intervention_reason: str = ""
    pm_question: str = ""
    error: str | None = None


async def extract_missing_information(
    request: MissingInfoRequest,
) -> MissingInfoResponse:
    """Extract unresolved guest inquiries from conversation.

    Scans the full conversation history to identify questions
    that remain unanswered or were only deferred.

    Args:
        request: Conversation data with messages.

    Returns:
        Missing info, answered items, and intervention reason.
    """
    if not response_has_deferral(request.ai_message):
        # Fast path: the AI gave a definitive answer.  History-leaking
        # false positives (a 5-turn-old WiFi deferral re-flagged on the
        # current "kac yatak" turn) used to spam PM Chat — gating on
        # the latest response cuts that off without an LLM call.
        return MissingInfoResponse()

    last_guest = _last_guest_message(request.messages)
    language = _detect_conversation_language(request.ai_message or last_guest)
    prompt = (
        f"Conversation language (ISO 639-1): {language}\n\n"
        f"Latest guest question:\n{last_guest or '(none)'}\n\n"
        f"Latest AI response:\n{request.ai_message or '(none)'}"
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return MissingInfoResponse(
            missing_information=data.get("missing_information", ""),
            answered_questions=data.get("answered_questions", ""),
            intervention_reason=data.get("intervention_reason", ""),
            pm_question=data.get("pm_question", ""),
        )
    except Exception as exc:
        logger.error("Missing info extraction failed: %s", exc)
        return MissingInfoResponse(status=False, error=str(exc))


def _detect_conversation_language(text: str) -> str:
    """Return the ISO 639-1 code of the conversation language.

    The PM escalation (``pm_question``) must be written in the same
    language the guest converses in.  Letting the extractor LLM
    *infer* that language proved unreliable: a Turkish property
    context biased the model into Turkish even on an English thread
    (tester 2026-06-10).  Detecting the language deterministically
    with the shared offline detector and pinning it into the prompt
    removes the guesswork — the AI's own reply (already rendered in
    the guest's language) is the authoritative signal.

    Args:
        text: Text in the conversation language — the latest AI
            response, falling back to the latest guest message.

    Returns:
        The detected ISO 639-1 code, or the detector's default
        (``"en"``) for empty / low-confidence input.
    """
    return get_shared_language_detector().detect(text).language


def _last_guest_message(messages: list[dict[str, str]]) -> str:
    """Return the latest guest message text, or empty string.

    Walks the history in reverse to find the most recent message
    whose role / sender_type marks it as guest-originated.  Truncates
    the result to :func:`guest_message_token_budget` tokens so the
    extractor prompt stays within budget for very long messages —
    operator-tunable via :data:`GUEST_MSG_MAX_TOKENS_ENV`, no magic
    number in code.

    Args:
        messages: Raw history rows as ``{role, content}`` /
            ``{senderType, text}`` dicts.

    Returns:
        Latest guest text truncated to the configured token cap, or
        ``""`` when no guest message is found.
    """
    for msg in reversed(messages):
        role = (msg.get("role") or msg.get("senderType") or "").lower()
        if role in {"user", "guest"}:
            content = msg.get("content") or msg.get("text") or ""
            return truncate_to_tokens(
                content,
                max_tokens=guest_message_token_budget(),
                model=_MODEL,
            )
    return ""


_SYSTEM_PROMPT = """\
You decide whether the latest AI response answered the latest guest \
question or deferred to a human.

Decision rules — apply strictly to the LATEST exchange only.  Older \
turns are NOT in scope: even if the AI deferred earlier in the \
conversation, that is irrelevant here.

- If the latest AI response gives a concrete, definitive answer to \
the latest guest question (a number, a name, an address, a yes/no, \
a password, a policy statement, etc.) → answered.
- If the latest AI response promises follow-up ("I'll check", \
"kontrol edip dönerim", "уточню и вернусь", "üzgünüm … bilgi yok", \
"I don't have …") → deferred.
- If the AI politely says a feature is unavailable that is also \
answered (the guest got their answer: "no, we don't have that").

Confirmation-offer rule (Aybüke 2026-05-18 bug) — a polite \
closing offer at the END of a concrete answer is NOT a deferral.  \
When the AI has already given the substantive answer (price, time, \
yes/no, policy) and only invites the guest to confirm next steps, \
classify as ANSWERED even if the closing sentence contains phrases \
like:
- "Let me know if you'd like me to arrange this for you"
- "Bana haber verin / söyleyin"
- "Just let me know how you'd like to proceed"
- "Дайте знать, если хотите оформить"
The presence of an offer phrase ALONE does not flip the verdict — \
only the AI's substantive content does.  Concrete number / time / \
yes-no / policy in the response = answered.

Examples:
- AI: "A late check-out until 12:00 is possible for an additional \
fee of €20. Let me know if you'd like me to arrange this for you." \
→ ANSWERED (time + fee given; the offer is a closing, not a \
deferral).
- AI: "Yes, parking is free. Let me know if you need directions." \
→ ANSWERED.
- AI: "I'll check the late-checkout availability and get back to \
you." → DEFERRED (no substantive answer, only a promise).

Return JSON.

When the AI deferred:
{
    "missing_information": "- short bullet of the gap",
    "answered_questions": "",
    "intervention_reason": "<topic>",
    "pm_question": "<full question to the property manager>"
}

When the AI answered:
{
    "missing_information": "",
    "answered_questions": "- short bullet of what was answered",
    "intervention_reason": "",
    "pm_question": ""
}

Hard rule: when missing_information is empty, intervention_reason \
AND pm_question MUST also be empty.  Never invent a gap to justify \
a flag.

Intervention-reason shape — intervention_reason MUST be JUST the \
bare topic noun phrase, copied from the guest's latest message in \
the SAME language they used.  Do NOT wrap it in any English \
template, do NOT prepend "Guest needs", do NOT append "which is \
not in the knowledge base" or any equivalent suffix.  PM Chat's UI \
already labels the field — extra framing is noise.

Examples — intervention_reason ONLY (other fields omitted):
- Guest (TR) asks about late check-out cost → \
"intervention_reason": "geç çıkış ücreti"
- Guest (EN) asks about parking → \
"intervention_reason": "parking"
- Guest (RU) asks about the Wi-Fi password → \
"intervention_reason": "пароль Wi-Fi"

PM-question shape — pm_question is the message the property manager \
reads in PM Chat, so it MUST be a complete, natural sentence, NOT a \
bare noun phrase.  Write it in the SAME language the guest used — \
specifically the language given as "Conversation language \
(ISO 639-1)" at the top of the user message: that exact language \
and no other.  If the conversation language is "en", pm_question \
MUST be English even when the property, the topic, or the examples \
below are associated with another country or written in another \
language; the same applies to "tr", "fr", "ru", and every other \
code.  State what the guest is asking about and ask the PM to \
provide the missing information so we can answer.  One or two polite \
sentences, specific to the guest's actual question.  Do NOT collapse \
it to two words and do NOT mix languages.

Examples — pm_question ONLY (other fields omitted):
- Guest (EN) asks if early check-in at noon is possible → \
"pm_question": "The guest is asking whether an early check-in at \
noon is possible, but I don't have this information. Could you let \
me know how I should respond?"
- Guest (EN) says they left a phone charger last month and asks us \
to check → "pm_question": "The guest says they left a phone charger \
at the property last month and would like us to check. Could you \
confirm whether it was found?"
- Guest (TR) asks about the late check-out cost → "pm_question": \
"Misafir geç çıkış ücretini soruyor ancak bu bilgi bende yok. Nasıl \
yanıtlamam gerektiğini iletebilir misiniz?"
- Guest (RU) asks for the Wi-Fi password → "pm_question": "Гость \
спрашивает пароль от Wi-Fi, но у меня нет этой информации. \
Подскажите, пожалуйста, как ответить?"

Topic rule — the bare topic MUST be the exact subject the guest \
raised in the latest message, copied verbatim (not paraphrased to \
a related concept).  Do NOT infer adjacent topics from the guest's \
question:
- Guest asks "can I check in early?" → topic is "early check-in", \
NOT "pricing", NOT "availability fee", NOT any inferred concept.
- Guest asks "is parking free?" → topic is "parking", NOT "fees".
- Guest asks "what is the Wi-Fi password?" → topic is "Wi-Fi \
password", NOT "internet speed".
If the guest's subject is ambiguous, use the most literal noun \
phrase from their message; never substitute a related-but-different \
concept.
"""
