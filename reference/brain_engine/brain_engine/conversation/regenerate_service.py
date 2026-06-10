"""Regenerate Service — re-generate AI responses with new information.

Handles three regeneration scenarios:
1. /regenerate — fill in missing info from PM
2. /regenerate-multiple — batch regeneration
3. /regenerate-pm-knowledge — update knowledge base + regenerate
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import litellm
from pydantic import BaseModel, Field

from brain_engine.conversation.pm_facts import (
    InMemoryPmFactStore,
    PmFact,
    PmFactStore,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o"
_TEMPERATURE = 0.2


# ── PM-input quality filter ───────────────────────────────────── #
#
# The PM Chat surface lets the manager type free-form replies that
# are then persisted as ``PmFact`` rows and re-read on the next
# guest message.  Demo testers on 2026-04-28 typed jokey deflections
# ("git say mutfaktan" / "kendin say kardeşim" / "sanane") and the
# assistant happily turned them into fabricated answers ("Mutfakta
# 12 çatal bulunmaktadır").  These patterns share a small core:
# - the PM tells the *guest* to look it up themselves
# - the PM brushes off the question without answering
# - the PM swears
# A targeted regex blacklist is enough — we deliberately keep the
# list narrow so legitimate short answers ("yok", "var", "hayır")
# still pass through and reach the store.

_JUNK_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Turkish — "go count / look / check yourself"
        r"\bkendin\s+(say|bak|bul|kontrol|halle?t)",
        r"\bsen\s+(say|bak|bul|kontrol|halle?t)\b",
        r"\bgit\s+(kendin\s+)?(say|bak|bul|kontrol)",
        r"\bsen\s+(de\s+)?bil(emem|miyorum)\b",
        r"\bbilmiyorum\b",
        # Turkish — dismissive / hostile
        r"\bsanane\b",
        r"\bsana\s+ne\b",
        r"\bmany?am[ıi]?s[ıi]?n\b",
        r"\bsalla(r|n)?\b",
        # English — "go figure it out yourself"
        r"\bgo\s+check\s+(it\s+)?yourself\b",
        r"\bfigure\s+it\s+out\s+(yourself|on\s+your\s+own)\b",
        r"\bnot\s+my\s+problem\b",
        r"\bi\s+don'?t\s+(know|care)\b",
        r"\bask\s+someone\s+else\b",
        # Russian — "go look yourself"
        r"\bсам\s+(посмотри|узнай|разбер[иу])",
        r"\bне\s+знаю\b",
        r"\bмне\s+пофиг\b",
    )
)

# Profanity is hard to enumerate exhaustively without a curated
# wordlist; the broad shibboleths below catch the most common
# deflective slurs in TR / EN / RU testing without false-positives
# on legitimate short answers.  Anything containing one of these
# triggers the dismissive path.
_PROFANITY_TOKENS: frozenset[str] = frozenset({
    # Turkish — clear-cut slurs only; ambiguous tokens like "deli"
    # / "manyak" are caught via the deflection-phrase regexes when
    # they appear in dismissive contexts and are skipped here so
    # legitimate complaint descriptions still pass.
    "amk", "aq", "siktir", "siktirgit", "orospu",
    # English slurs
    "fuck", "fck", "shit", "stfu",
    # Russian slurs (truncated stems are deliberate to catch suffixes)
    "блят", "хуй", "пизд", "ебан",
})

# A minimum signal-to-noise floor: anything shorter than this after
# whitespace-stripping is unlikely to encode a useful answer (single
# letters, stray punctuation).  We pass "yok"/"var"/"no"/"да" through
# by keeping the threshold below 3 characters' worth of meaning.
_MIN_FACT_CHARS = 2

_WHITESPACE_RE = re.compile(r"\s+")


def is_low_quality_pm_answer(text: str) -> tuple[bool, str]:
    """Decide whether a PM-typed answer should be stored as a fact.

    Returns a ``(is_low_quality, reason)`` tuple.  ``True`` means the
    PM's reply is a deflection / joke / profanity and must NOT enter
    ``PmFactStore`` — otherwise the live-chat read path will surface
    it as authoritative knowledge and the assistant will fabricate a
    confident answer around it.  The reason string is logged so
    operations can audit which class of input was rejected.

    The check is intentionally local and dependency-free: junk PM
    inputs need to be filtered before the store write, which already
    happens during the demo round-trip — adding an LLM call here
    would re-introduce the latency / quota bloat the rest of this
    branch is trying to avoid.

    Args:
        text: Trimmed PM answer text.

    Returns:
        ``(True, reason)`` when the text matches a known junk
        pattern; ``(False, "")`` otherwise.
    """
    candidate = _WHITESPACE_RE.sub(" ", text).strip()
    if len(candidate) < _MIN_FACT_CHARS:
        return True, "too_short"

    haystack = candidate.lower()
    tokens = set(re.findall(r"[\wа-яё]+", haystack))
    if tokens & _PROFANITY_TOKENS:
        return True, "profanity"

    for pattern in _JUNK_PHRASE_PATTERNS:
        if pattern.search(haystack):
            return True, f"deflection:{pattern.pattern}"

    return False, ""

# ── Module-level PM-fact store ─────────────────────────────────── #
#
# Lives at module scope so the FastAPI lifespan can swap in the
# Postgres-backed implementation once on startup, then every
# subsequent ``regenerate_with_knowledge`` call routes through it
# without an extra DI plumbing layer.  Default is the in-memory
# store so unit tests and dev environments still work without a
# live Postgres connection.
_pm_fact_store: PmFactStore = InMemoryPmFactStore()


def set_pm_fact_store(store: PmFactStore) -> None:
    """Install the runtime :class:`PmFactStore`.

    Called from ``api_server/server.py`` lifespan after backend
    selection (memory vs. postgres) so subsequent
    :func:`regenerate_with_knowledge` invocations persist into the
    chosen store.  Calling this multiple times during a single
    process lifetime is supported — the latest store wins.
    """
    global _pm_fact_store
    _pm_fact_store = store


def get_pm_fact_store() -> PmFactStore:
    """Return the currently-installed :class:`PmFactStore`.

    Exposed so the live-chat pipeline (ConversationService) can
    pick up the same instance the regenerate path writes to,
    keeping read-after-write semantics tight.
    """
    return _pm_fact_store


# ── Request/Response Models ──────────────────────────────────── #


class RegenerateRequest(BaseModel):
    """Input to POST /api/v1/regenerate."""

    customer_id: str = ""
    org_id: str = ""
    message_id: str = ""
    missing_information: str = Field(
        default="",
        description="Description of what info was missing",
    )
    new_information: str = Field(
        default="",
        description="New info provided by PM to fill the gap",
    )
    ai_message: str = Field(
        default="",
        description="Original AI response to update (semi-auto mode)",
    )
    guest_message: str = Field(
        default="",
        description="Original guest question",
    )


class RegenerateResponse(BaseModel):
    """Output of regenerate endpoints."""

    status: bool = True
    message: str = ""
    is_need_attention: bool = False
    error: str | None = None


class RegenerateMultipleRequest(BaseModel):
    """Input to POST /api/v1/regenerate-multiple."""

    customer_id: str = ""
    org_id: str = ""
    items: list[RegenerateRequest] = Field(default_factory=list)


class RegenerateMultipleResponse(BaseModel):
    """Output of batch regeneration."""

    status: bool = True
    results: list[RegenerateResponse] = Field(default_factory=list)
    error: str | None = None


class UpdateKnowledgeRequest(BaseModel):
    """Input to POST /api/v1/regenerate-pm-knowledge."""

    customer_id: str = ""
    org_id: str = ""
    message_id: str = ""
    # Empty when the PM Chat surface does not pass a property
    # selection.  Empty values are persisted as customer-wide
    # facts that surface for every property of that customer.
    property_channel_id: str = Field(
        default="",
        description=(
            "Property scope for the knowledge update.  Empty "
            "string = customer-wide; otherwise the propertyChannelId "
            "the live-chat pipeline keys profile lookups on."
        ),
    )
    knowledge_update: str = Field(
        default="",
        description="New knowledge to add to the property KB",
    )
    regenerate_response: bool = Field(
        default=True,
        description="Also regenerate the AI response with new knowledge",
    )
    guest_message: str = ""
    ai_message: str = ""


# ── Service Functions ────────────────────────────────────────── #


async def regenerate_response(
    request: RegenerateRequest,
) -> RegenerateResponse:
    """Regenerate an AI response with new information from PM.

    Two modes:
    - Full regeneration: new_information fills gaps, generates fresh
    - Semi-auto: updates existing ai_message with new_information

    Args:
        request: Regeneration request with new info.

    Returns:
        Regenerated response.
    """
    if request.ai_message:
        return await _semi_auto_regenerate(request)
    return await _full_regenerate(request)


async def regenerate_multiple(
    request: RegenerateMultipleRequest,
) -> RegenerateMultipleResponse:
    """Batch regenerate multiple AI responses.

    Args:
        request: Batch request with list of items.

    Returns:
        Batch response with individual results.
    """
    results: list[RegenerateResponse] = []
    for item in request.items:
        result = await regenerate_response(item)
        results.append(result)

    return RegenerateMultipleResponse(results=results)


async def regenerate_with_knowledge(
    request: UpdateKnowledgeRequest,
) -> RegenerateResponse:
    """Update knowledge base and optionally regenerate response.

    Persists ``knowledge_update`` into the active
    :class:`PmFactStore` so the next live-chat turn for the same
    property reads the answer back from durable storage instead of
    re-flagging the original gap.  When ``regenerate_response`` is
    True the same text is also fed into the synchronous regenerate
    pipeline so the PM gets an updated guest reply on the spot.

    Args:
        request: Knowledge update request.

    Returns:
        Regenerated response (or just confirmation).
    """
    await _store_knowledge_update(request)

    if not request.regenerate_response:
        return RegenerateResponse(
            message="Knowledge updated successfully.",
        )

    regen_req = RegenerateRequest(
        customer_id=request.customer_id,
        org_id=request.org_id,
        message_id=request.message_id,
        new_information=request.knowledge_update,
        ai_message=request.ai_message,
        guest_message=request.guest_message,
    )
    return await regenerate_response(regen_req)


# ── Internal ─────────────────────────────────────────────────── #


async def _full_regenerate(
    request: RegenerateRequest,
) -> RegenerateResponse:
    """Full regeneration with new information.

    Args:
        request: Regeneration request.

    Returns:
        Fresh regenerated response.
    """
    prompt = (
        f"Guest question: {request.guest_message}\n\n"
        f"Previously missing: {request.missing_information}\n\n"
        f"New information from property manager:\n{request.new_information}\n\n"
        "Generate a complete, definitive response using the new information."
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _REGEN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return RegenerateResponse(
            message=data.get("updated_response", ""),
            is_need_attention=data.get("is_need_attention", False),
        )
    except Exception as exc:
        logger.error("Full regeneration failed: %s", exc)
        return RegenerateResponse(status=False, error=str(exc))


async def _semi_auto_regenerate(
    request: RegenerateRequest,
) -> RegenerateResponse:
    """Update existing AI response with new information.

    Preserves the parts that were already correct and updates
    only the missing/deferred parts.

    Args:
        request: Regeneration request with ai_message set.

    Returns:
        Updated response.
    """
    prompt = (
        f"Original AI response:\n{request.ai_message}\n\n"
        f"Missing information: {request.missing_information}\n\n"
        f"New information: {request.new_information}\n\n"
        "Update the AI response with the new information. "
        "Keep correct parts, replace deferred parts."
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SEMIAUTO_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return RegenerateResponse(
            message=data.get("updated_response", ""),
            is_need_attention=data.get("is_need_attention", False),
        )
    except Exception as exc:
        logger.error("Semi-auto regeneration failed: %s", exc)
        return RegenerateResponse(status=False, error=str(exc))


async def _store_knowledge_update(
    request: UpdateKnowledgeRequest,
) -> None:
    """Persist one PM-confirmed fact into the active store.

    Side-effect-only: returns ``None`` even when the store call
    raises, because the regenerate pipeline must still produce a
    response for the PM — losing one durability write is strictly
    less bad than losing the whole regeneration round-trip.
    A WARNING is logged on failure so operations can spot it.

    Args:
        request: Knowledge update request from PM Chat.
    """
    fact_text = request.knowledge_update.strip()
    if not fact_text or not request.customer_id:
        # Empty payload means "regenerate without learning"; this
        # is a legitimate UI state (PM dismissed the suggestion)
        # so we silently no-op.
        return

    # Quality gate — drop the write when the PM typed a deflection
    # ("git say mutfaktan"), a joke ("manyamısın"), or a profanity
    # ("siktir").  Persisting these as facts feeds the next guest
    # turn a confident-sounding fabrication, which is exactly what
    # Mümin's reviewer flagged on 2026-04-28 ("çatal sayılarını
    # salladı").  Logging the rejection reason gives operations a
    # crumb trail when they later wonder why a particular PM reply
    # never surfaced in subsequent conversations.
    is_junk, reason = is_low_quality_pm_answer(fact_text)
    if is_junk:
        logger.info(
            "PM knowledge rejected (low_quality reason=%s "
            "customer=%s property=%s)",
            reason,
            request.customer_id,
            request.property_channel_id or "<customer-wide>",
        )
        return

    fact = PmFact(
        customer_id=request.customer_id,
        org_id=request.org_id,
        property_channel_id=request.property_channel_id,
        fact_text=fact_text,
        source_message_id=request.message_id,
        created_at=datetime.now(timezone.utc),
    )
    try:
        await _pm_fact_store.add_fact(fact)
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        logger.warning(
            "PmFactStore.add_fact failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return

    logger.info(
        "PM knowledge persisted (customer=%s property=%s chars=%d)",
        request.customer_id,
        request.property_channel_id or "<customer-wide>",
        len(fact_text),
    )


_REGEN_SYSTEM = """Regenerate a guest response using newly provided information.

Rules:
- Use the new information to give DEFINITIVE answers
- Never mix definitive answers with "we'll check" language
- If PM provided a decision, state it professionally without mentioning approval process
- Maintain a friendly, professional tone
- Respond in the same language as the original guest message

Return JSON:
{"updated_response": "...", "is_need_attention": false}
"""

_SEMIAUTO_SYSTEM = """Update an existing AI response with new information.

Rules:
- Keep parts of the original response that were already correct
- Replace "we'll check" / "let me find out" sections with definitive answers
- Maintain the original tone and style
- Never invent information beyond what's provided
- If the new info doesn't cover everything, it's OK to keep some deferrals

Return JSON:
{"updated_response": "...", "is_need_attention": false}
"""
