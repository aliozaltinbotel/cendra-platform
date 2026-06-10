"""Business Flag Classifier — Cendra-compatible intent classification.

Classifies guest messages into business flags matching Cendra's
existing classification system. Uses GPT-4o-mini at temperature=0.1
for fast, deterministic classification.

Business flags:
    IS_EMERGENCY          — fire, health emergency, break-in, gas leak
    IS_PROPERTY_RELATED   — amenities, wifi, rules, appliances, keys
    IS_AVAILABILITY_RELATED — dates, pricing, calendar queries
    IS_RESERVATION_RELATED — existing booking modifications
    IS_COMPLAINT          — negative experience, dissatisfaction
    IS_CHECK_IN_OUT_RELATED — check-in/out instructions, timing
    IS_NAVIGATION_QUERY   — can't find property, directions, location, how to get
    IS_DISCOUNT_REQUEST   — price negotiation, coupon, deal
    IS_INVOICE_REQUEST    — receipt, billing, documentation
    IS_CLEANING_ISSUE     — dirty, stains, smell, hygiene
    IS_MAINTENANCE_ISSUE  — broken, leaking, not working
    IS_NOISE_COMPLAINT    — loud neighbors, construction
    IS_SECURITY_ISSUE     — lock broken, suspicious person
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)

_CLASSIFIER_MODEL = "gpt-4o-mini"
_CLASSIFIER_TEMPERATURE = 0.1
_CLASSIFIER_MAX_TOKENS = 500

# Fallback reply language used ONLY when the language of the current
# guest message cannot be determined (empty / malformed classifier
# output).  The assistant is NOT restricted to a fixed language set:
# it mirrors the guest's own language for ANY language of the world —
# the reply instruction lives in ``conversation/service.py`` and the
# detected code is sanitised (format only, no whitelist) below.
_DEFAULT_RESPONSE_LANGUAGE = "en"


def _normalize_language(raw: Any) -> str:
    """Sanitise a classifier-emitted language code without a whitelist.

    Accepts any ISO 639-1 style two-letter alphabetic code so the engine
    can reply in any language; only empty or malformed output falls back
    to :data:`_DEFAULT_RESPONSE_LANGUAGE`.

    Args:
        raw: Whatever the LLM (or fallback) put in ``response_language``.

    Returns:
        A two-letter language code, or ``"en"`` when the input is empty
        or not a well-formed code.
    """
    code = str(raw or "").strip().lower()[:2]
    if len(code) == 2 and code.isalpha():
        return code
    return _DEFAULT_RESPONSE_LANGUAGE


@dataclass
class ClassificationResult:
    """Result of business flag classification.

    Attributes:
        flags: Dict of flag name -> bool.
        response_language: Detected ISO 639-1 language code of the
            current guest message, sanitised by
            :func:`_normalize_language`.  Any language is allowed; the
            engine replies in the guest's own language.  ``"en"`` is
            used only when detection is empty / malformed.
        confidence: Overall classification confidence (0.0-1.0).
        sentiment_score: Sentiment from 1 (very negative) to 5 (very positive).
        urgency: Urgency level (low, normal, high, critical).
        detected_issues: List of specific issues detected.
        suggested_category: Main issue category for ops routing.
        suggested_subcategory: Sub-category for detailed routing.
        scenario_hint: LLM-suggested ``Scenario`` value (e.g.
            ``"access_code_release"``) consumed by
            :class:`~brain_engine.patterns.classifier.DecisionClassifier`
            as a multilingual replacement for the keyword chain.
            Empty when the LLM did not commit to a scenario;
            DecisionClassifier then falls back to keywords —
            preserving pre-Stage 2 behaviour.
        decision_type_hint: LLM-suggested ``DecisionType`` value
            (``"approve"`` / ``"deny"`` / ``"defer"`` / ``"inform"``
            / ``"quote"`` / ``"offer"`` / ``"ask"``) for the assistant
            response.  Same fallback semantics as ``scenario_hint``.
    """

    flags: dict[str, bool] = field(default_factory=dict)
    response_language: str = "en"
    confidence: float = 0.8
    sentiment_score: int = 3
    urgency: str = "normal"
    detected_issues: list[str] = field(default_factory=list)
    suggested_category: str = ""
    suggested_subcategory: str = ""
    scenario_hint: str = ""
    decision_type_hint: str = ""

    @property
    def is_emergency(self) -> bool:
        """Check if this is an emergency."""
        return self.flags.get("IS_EMERGENCY", False)

    @property
    def needs_ops(self) -> bool:
        """Check if this needs ops agent (not just guest reply)."""
        ops_flags = {
            "IS_CLEANING_ISSUE", "IS_MAINTENANCE_ISSUE",
            "IS_SECURITY_ISSUE", "IS_EMERGENCY",
        }
        return any(self.flags.get(f, False) for f in ops_flags)

    @property
    def is_thanks_only(self) -> bool:
        """Check if this is just a thank-you message."""
        return self.flags.get("IS_THANKS_ONLY", False)

    @property
    def active_flags(self) -> list[str]:
        """Return list of flag names that are True."""
        return [name for name, val in self.flags.items() if val]

    def to_business_flags(self) -> dict[str, bool]:
        """Convert to conversation.models.BusinessFlags-compatible dict.

        Returns:
            Dict with snake_case keys matching BusinessFlags fields.
        """
        mapping: dict[str, str] = {
            "IS_EMERGENCY": "is_emergency",
            "IS_PROPERTY_RELATED": "is_property_related",
            "IS_AVAILABILITY_RELATED": "is_availability_related",
            "IS_RESERVATION_RELATED": "is_reservation_related",
            "IS_PRICE_RELATED": "is_price_related",
            "IS_CHECK_IN_OUT_RELATED": "is_check_in_out_related",
            "IS_LOCATION_BASED": "is_location_based",
            "IS_ALTERNATIVE_PROPERTY_REQUESTED": "is_alternative_property_requested",
            "IS_INVOICE_REQUEST": "is_invoice_request",
            "IS_DISCOUNT_REQUEST": "is_discount_request",
            "IS_ADDITIONAL_SERVICES": "is_additional_services",
            "IS_THANKS_ONLY": "is_thanks_only",
            "IS_COMPLAINT": "is_complaint",
            "IS_CLEANING_ISSUE": "is_cleaning_issue",
            "IS_MAINTENANCE_ISSUE": "is_maintenance_issue",
            "IS_NOISE_COMPLAINT": "is_noise_complaint",
            "IS_SECURITY_ISSUE": "is_security_issue",
            "IS_NAVIGATION_QUERY": "is_navigation_query",
        }
        return {
            snake: self.flags.get(upper, False)
            for upper, snake in mapping.items()
        }

    @property
    def is_complaint(self) -> bool:
        """Check if this is a complaint."""
        return self.flags.get("IS_COMPLAINT", False)


# All supported business flags (16 — Cendra business taxonomy)
ALL_FLAGS: list[str] = [
    "IS_EMERGENCY",
    "IS_PROPERTY_RELATED",
    "IS_AVAILABILITY_RELATED",
    "IS_RESERVATION_RELATED",
    "IS_PRICE_RELATED",
    "IS_CHECK_IN_OUT_RELATED",
    "IS_LOCATION_BASED",
    "IS_ALTERNATIVE_PROPERTY_REQUESTED",
    "IS_INVOICE_REQUEST",
    "IS_DISCOUNT_REQUEST",
    "IS_ADDITIONAL_SERVICES",
    "IS_THANKS_ONLY",
    "IS_COMPLAINT",
    "IS_CLEANING_ISSUE",
    "IS_MAINTENANCE_ISSUE",
    "IS_NOISE_COMPLAINT",
    "IS_SECURITY_ISSUE",
    "IS_NAVIGATION_QUERY",
]


class BusinessFlagClassifier:
    """Classifies messages into Cendra-compatible business flags.

    Uses a fast LLM call (gpt-4o-mini, temp=0.1) for deterministic
    classification. Returns structured flags, sentiment, urgency,
    and suggested categories for ops routing.

    Args:
        model: LLM model to use for classification.
    """

    def __init__(
        self,
        model: str = _CLASSIFIER_MODEL,
        *,
        intelligent_classifier: Any | None = None,
    ) -> None:
        """Construct the classifier.

        Args:
            model: LLM model to use for the primary flag-extraction
                call.
            intelligent_classifier: Optional
                :class:`~brain_engine.patterns.intelligent_classifier.
                IntelligentClassifier`.  When wired, the classifier
                runs after the primary LLM call and enriches the
                :attr:`ClassificationResult.decision_type_hint`
                field (and, when the foundation registry id maps
                back into the canonical :class:`Scenario` enum,
                the ``scenario_hint`` field too).  ``None`` keeps
                the pre-Phase-5 behaviour bit-for-bit so callers
                can roll the migration out at their own pace.
        """
        self._model = model
        self._intelligent = intelligent_classifier

    async def classify(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
        guest_labels: list[str] | None = None,
    ) -> ClassificationResult:
        """Classify a guest message into business flags.

        Args:
            message: The guest message text.
            conversation_history: Previous messages for context.
            guest_labels: Guest persona labels (e.g., 'Family', 'VIP').

        Returns:
            ClassificationResult with all flags and metadata.
        """
        context = self._build_context(conversation_history, guest_labels)

        try:
            result = await self._classify_via_llm(message, context)
        except Exception:
            logger.error("LLM classification failed", exc_info=True)
            result = self._fallback_classify(message)

        # Phase 5 — enrich with IntelligentClassifier when wired.
        # Defensive: the IC layer must never break the primary
        # classification path, so every failure mode collapses to
        # a no-op (result returned unchanged).
        if self._intelligent is not None and message and message.strip():
            try:
                ic_result = await self._intelligent.classify(message)
            except Exception:
                logger.warning(
                    "intelligent_classifier enrichment failed; "
                    "using BFC result unchanged",
                    exc_info=True,
                )
            else:
                # Only fill blanks — never overwrite a hint the
                # upstream LLM already committed to.
                if (
                    not result.decision_type_hint
                    and ic_result.decision_type
                ):
                    result.decision_type_hint = (
                        ic_result.decision_type
                    )
        return result

    async def _classify_via_llm(
        self,
        message: str,
        context: str,
    ) -> ClassificationResult:
        """Run LLM classification.

        Args:
            message: Guest message.
            context: Additional context string.

        Returns:
            Parsed ClassificationResult.
        """
        prompt = _CLASSIFICATION_PROMPT.format(
            message=message,
            context=context,
            flags=", ".join(ALL_FLAGS),
        )

        response = await litellm.acompletion(
            model=self._model,
            messages=[
                {"role": "system", "content": _CLASSIFICATION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=_CLASSIFIER_TEMPERATURE,
            max_tokens=_CLASSIFIER_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        text = response.choices[0].message.content or ""
        return self._parse_classification(text)

    @staticmethod
    def _build_context(
        history: list[dict[str, str]] | None,
        labels: list[str] | None,
    ) -> str:
        """Build context string from history and labels.

        Args:
            history: Conversation history.
            labels: Guest persona labels.

        Returns:
            Context string for the prompt.
        """
        parts: list[str] = []

        if history:
            recent = history[-3:]  # last 3 messages
            for msg in recent:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:200]
                parts.append(f"[{role}]: {content}")

        if labels:
            parts.append(f"Guest labels: {', '.join(labels)}")

        return "\n".join(parts) if parts else "No additional context"

    @staticmethod
    def _parse_classification(text: str) -> ClassificationResult:
        """Parse LLM JSON response into ClassificationResult.

        Args:
            text: JSON string from LLM.

        Returns:
            Parsed ClassificationResult.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return ClassificationResult(confidence=0.3)
            else:
                return ClassificationResult(confidence=0.3)

        flags_raw = data.get("flags", {})
        flags = {f: bool(flags_raw.get(f, False)) for f in ALL_FLAGS}

        return ClassificationResult(
            flags=flags,
            response_language=_normalize_language(data.get("response_language")),
            confidence=float(data.get("confidence", 0.8)),
            sentiment_score=int(data.get("sentiment_score", 3)),
            urgency=data.get("urgency", "normal"),
            detected_issues=data.get("detected_issues", []),
            suggested_category=data.get("suggested_category", ""),
            suggested_subcategory=data.get("suggested_subcategory", ""),
            scenario_hint=str(data.get("scenario_hint") or "").strip(),
            decision_type_hint=str(
                data.get("decision_type_hint") or "",
            ).strip(),
        )

    @staticmethod
    def _fallback_classify(message: str) -> ClassificationResult:
        """Keyword-based fallback when LLM is unavailable.

        Args:
            message: Guest message text.

        Returns:
            ClassificationResult from keyword matching.
        """
        lower = message.lower()
        flags: dict[str, bool] = dict.fromkeys(ALL_FLAGS, False)

        # Emergency
        if any(kw in lower for kw in [
            "fire", "flood", "gas leak", "emergency", "ambulance", "police",
            "yangın", "sel", "gaz kaçağı", "acil", "пожар", "наводнение",
        ]):
            flags["IS_EMERGENCY"] = True

        # Cleaning
        if any(kw in lower for kw in [
            "dirty", "clean", "stain", "smell", "dust", "hygiene",
            "kirli", "leke", "koku", "грязн", "убор", "пятно",
        ]):
            flags["IS_CLEANING_ISSUE"] = True
            flags["IS_COMPLAINT"] = True

        # Maintenance
        if any(kw in lower for kw in [
            "broken", "leak", "not working", "fix", "repair",
            "bozuk", "kırık", "çalışmıyor", "tamir", "сломан", "течет",
        ]):
            flags["IS_MAINTENANCE_ISSUE"] = True

        # Check-in/out
        if any(kw in lower for kw in [
            "check-in", "check in", "checkout", "check out", "key", "code", "access",
            "early check", "late check", "giriş", "çıkış", "anahtar",
        ]):
            flags["IS_CHECK_IN_OUT_RELATED"] = True

        # Navigation (multi-language)
        if any(kw in lower for kw in [
            "can't find", "cant find", "where is", "how to get", "directions",
            "address", "lost", "entrance", "which building", "google maps",
            "bulamıyorum", "nerede", "nasıl gidilir", "не могу найти",
            "как добраться", "no encuentro", "cómo llegar", "wo ist",
        ]):
            flags["IS_NAVIGATION_QUERY"] = True
            flags["IS_CHECK_IN_OUT_RELATED"] = True

        # Location-based
        if any(kw in lower for kw in [
            "restaurant", "cafe", "near", "nearby", "pharmacy", "supermarket",
            "restoran", "yakın", "eczane", "market", "ресторан", "рядом",
        ]):
            flags["IS_LOCATION_BASED"] = True

        # Property info
        if any(kw in lower for kw in [
            "wifi", "password", "parking", "pool", "amenity", "towel",
            "şifre", "havuz", "otopark", "парковка", "пароль", "бассейн",
        ]):
            flags["IS_PROPERTY_RELATED"] = True

        # Availability
        if any(kw in lower for kw in [
            "available", "availability", "dates", "book", "reserve",
            "müsait", "tarih", "rezervasyon", "свободно", "бронь",
        ]):
            flags["IS_AVAILABILITY_RELATED"] = True

        # Price
        if any(kw in lower for kw in [
            "price", "cost", "how much", "rate", "per night",
            "fiyat", "kaç", "ücret", "цена", "сколько",
        ]):
            flags["IS_PRICE_RELATED"] = True

        # Reservation
        if any(kw in lower for kw in [
            "booking", "reservation", "cancel", "modify", "change dates",
            "iptal", "değiştir", "отмена", "изменить",
        ]):
            flags["IS_RESERVATION_RELATED"] = True

        # Complaint
        if any(kw in lower for kw in [
            "complaint", "terrible", "awful", "unacceptable", "disappointed",
            "worst", "şikayet", "berbat", "жалоба", "ужасно",
        ]):
            flags["IS_COMPLAINT"] = True

        # Security
        if any(kw in lower for kw in [
            "broken lock", "suspicious", "unsafe", "theft", "stolen",
            "kilit", "şüpheli", "hırsız", "замок сломан", "кража",
        ]):
            flags["IS_SECURITY_ISSUE"] = True

        # Noise
        if any(kw in lower for kw in [
            "noise", "loud", "neighbor", "party", "gürültü", "шум",
        ]):
            flags["IS_NOISE_COMPLAINT"] = True
            flags["IS_COMPLAINT"] = True

        # Discount
        if any(kw in lower for kw in [
            "discount", "cheaper", "deal", "coupon", "indirim", "скидк",
        ]):
            flags["IS_DISCOUNT_REQUEST"] = True

        # Invoice
        if any(kw in lower for kw in [
            "invoice", "receipt", "billing", "fatura", "makbuz", "счёт", "квитанция",
        ]):
            flags["IS_INVOICE_REQUEST"] = True

        # Alternative property
        if any(kw in lower for kw in [
            "alternative", "other property", "different place", "another",
            "başka", "alternatif", "другой", "альтернатив",
        ]):
            flags["IS_ALTERNATIVE_PROPERTY_REQUESTED"] = True

        # Additional services
        if any(kw in lower for kw in [
            "extra", "additional", "service", "special", "transfer", "airport",
            "ekstra", "hizmet", "transfer", "доп", "услуга", "трансфер",
        ]):
            flags["IS_ADDITIONAL_SERVICES"] = True

        # Thanks only
        thanks_kw = [
            "thank", "thanks", "teşekkür", "sağol", "merci", "gracias",
            "спасибо", "danke", "obrigado",
        ]
        if any(kw in lower for kw in thanks_kw):
            # Only if no other flags are set
            other_active = any(
                v for k, v in flags.items() if k != "IS_THANKS_ONLY"
            )
            if not other_active:
                flags["IS_THANKS_ONLY"] = True

        urgency = "normal"
        if flags["IS_EMERGENCY"]:
            urgency = "critical"
        elif flags["IS_COMPLAINT"] or flags["IS_SECURITY_ISSUE"]:
            urgency = "high"

        return ClassificationResult(
            flags=flags,
            confidence=0.5,
            urgency=urgency,
            sentiment_score=2 if flags["IS_COMPLAINT"] else 3,
        )


# ── Prompt templates ────────────────────────────────────────────────── #

_CLASSIFICATION_SYSTEM = (
    "You are a message classification engine for a vacation rental "
    "property management platform. Classify guest messages into "
    "business flags accurately. Return valid JSON only."
)

_CLASSIFICATION_PROMPT = """Classify this guest message into business flags.

MESSAGE: {message}

CONTEXT:
{context}

Return JSON with exactly these fields:
{{
    "flags": {{
        "IS_EMERGENCY": false,
        "IS_PROPERTY_RELATED": false,
        "IS_AVAILABILITY_RELATED": false,
        "IS_RESERVATION_RELATED": false,
        "IS_PRICE_RELATED": false,
        "IS_CHECK_IN_OUT_RELATED": false,
        "IS_LOCATION_BASED": false,
        "IS_ALTERNATIVE_PROPERTY_REQUESTED": false,
        "IS_INVOICE_REQUEST": false,
        "IS_DISCOUNT_REQUEST": false,
        "IS_ADDITIONAL_SERVICES": false,
        "IS_THANKS_ONLY": false,
        "IS_COMPLAINT": false,
        "IS_CLEANING_ISSUE": false,
        "IS_MAINTENANCE_ISSUE": false,
        "IS_NOISE_COMPLAINT": false,
        "IS_SECURITY_ISSUE": false,
        "IS_NAVIGATION_QUERY": false
    }},
    "response_language": "en",
    "confidence": 0.95,
    "sentiment_score": 3,
    "urgency": "normal",
    "detected_issues": [],
    "suggested_category": "",
    "suggested_subcategory": "",
    "scenario_hint": "",
    "decision_type_hint": ""
}}

Flag rules (multiple can be true simultaneously):
- IS_EMERGENCY: ONLY life-threatening — fire, gas leak, health emergency, break-in, flood
- IS_PROPERTY_RELATED: amenities, WiFi, appliances, rules, policies, parking, keys
- IS_AVAILABILITY_RELATED: booking dates, calendar, when is available
- IS_RESERVATION_RELATED: existing booking details, modifications, cancellation
- IS_PRICE_RELATED: pricing questions, cost, how much, rate
- IS_CHECK_IN_OUT_RELATED: check-in/out instructions, timing, early/late requests
- IS_LOCATION_BASED: nearby restaurants, directions, local recommendations
- IS_ALTERNATIVE_PROPERTY_REQUESTED: asks for different/alternative property
- IS_INVOICE_REQUEST: receipt, billing document, invoice
- IS_DISCOUNT_REQUEST: price negotiation, coupon, deal request
- IS_ADDITIONAL_SERVICES: extra cleaning, packages, special services
- IS_THANKS_ONLY: ONLY gratitude/acknowledgment, no question asked
- IS_COMPLAINT: dissatisfaction, negative experience
- IS_CLEANING_ISSUE: dirty, stains, smell, hygiene problems
- IS_MAINTENANCE_ISSUE: broken, leaking, not working, repair needed
- IS_NOISE_COMPLAINT: loud neighbors, construction noise, party
- IS_SECURITY_ISSUE: lock broken, suspicious person, theft, unsafe
- IS_NAVIGATION_QUERY: can't find property, directions, entrance, address, Google Maps. Also set IS_CHECK_IN_OUT_RELATED=true when navigation detected.

Other fields:
- response_language: the ISO 639-1 two-letter code for the language of the MESSAGE above (the current guest turn) ONLY — the assistant replies in the guest's own language, whatever it is (e.g. "en", "tr", "de", "ru", "fr", "es", "it", "ar", "zh"). Decide it from the language of the MESSAGE only; IGNORE the language of the CONTEXT / earlier messages, which may be in a different language. Use "en" only when the message language is genuinely unclear (e.g. a bare "ok").
- sentiment_score: 1=very negative, 2=negative, 3=neutral, 4=positive, 5=very positive
- urgency: "low", "normal", "high", "critical"
- suggested_category: main ops category for routing
- detected_issues: list of specific problems mentioned
- scenario_hint: ONE of these exact lowercase snake_case values when applicable, else "":
    "access_code_release"        guest needs entry credentials / apartment number / floor / how to get in
    "early_checkin"              guest wants to arrive earlier than scheduled check-in
    "late_checkout"              guest wants to leave later than scheduled check-out (incl. key drop questions)
    "cancellation_request"       guest wants to cancel or get a refund
    "booking_extension"          guest wants to extend the stay by additional nights
    "guest_count_mismatch"       reservation guest count differs from the actual party size
    "discount_request"           guest negotiates price / asks for a deal
    "amenity_exception"          guest asks for amenity not in the listing (towels, coffee, etc.)
    "pet_policy_exception"       pet-related request
    "parking_request"            parking-spot request
    "extra_bed_request"          extra bed / crib / cot request
    "damage_report"              guest reports something broken / damaged
    "noise_complaint"            guest complaints about noise / neighbours / construction
    "lost_item"                  guest lost or forgot an item at the property
    "maintenance_request"        leak / broken appliance / malfunction
    "complaint_compensation"     general complaint that may need goodwill compensation
    "min_stay_exception"         guest wants to book fewer nights than min_stay
    "price_negotiation"          guest pushes back on the quoted price
    "special_request"            other operational ask not covered above
  Use the most specific match.  This field replaces brittle keyword
  matching for non-English guests — choose the scenario by *intent*,
  not literal English keywords.  Leave empty if the message is just
  a thanks / acknowledgement / unrelated chat.
- decision_type_hint: ONE of these exact lowercase values when the
  *assistant response* commits to an action, else "":
    "approve"   PM agreed / granted the request
    "deny"      PM refused / declined
    "defer"     PM postponed / asked guest to wait / no immediate action
    "inform"    PM provided information without an action commitment
    "quote"     PM quoted a price / fee
    "offer"     PM proposed an alternative
    "ask"       PM asked the guest for more information
  Choose by *intent* of the response text, not English keywords."""
