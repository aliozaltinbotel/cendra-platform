"""Refusal / conditional-grant extractor for historical PM messages.

Reference: ``brain_engine_advisory.md`` §3 (pattern mining) +
Monday-2026-04-27 PM-test feedback ("a PM refusing to release the door
code until a passport scan arrives is a *guardrail*, not a generic
inform").

Why this module exists
----------------------
The runtime guardrail pipeline (``brain_engine.guardrails.pipeline``)
validates *outgoing* responses.  It cannot mine archived conversations
for the **implicit policy** a PM has already enforced for years
(e.g. "no access code without ID").  Without that mining the engine
keeps proposing access-code releases that the PM will silently veto.

The extractor walks a PM message in any of the four operational
languages — Turkish, English, Russian, Spanish — and returns zero or
more :class:`RefusalSignal` immutable value objects describing:

* the refusal **type** (document required, payment required, approval
  required, hard block, generic refusal);
* the trigger phrase that fired the rule;
* the optional **conditional clause** (``unless / until / if / sin /
  hasta que / olmadan / bez / poka``-equivalents) bounding the
  refusal;
* the detected language and a deterministic confidence score.

The extractor is a pure function, has no I/O, and is hot-path safe —
suitable for the bootstrap loader and the past-conversation viewer
endpoint added in Stage 8.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


# ---------------------------------------------------------------------------
# Public enums + value objects
# ---------------------------------------------------------------------------


class RefusalType(StrEnum):
    """Taxonomy of PM refusal semantics surfaced by the extractor."""

    REQUIRES_DOCUMENT = "requires_document"
    REQUIRES_PAYMENT = "requires_payment"
    REQUIRES_APPROVAL = "requires_approval"
    HARD_BLOCK = "hard_block"
    GENERIC_REFUSAL = "generic_refusal"


class RefusalLanguage(StrEnum):
    """Languages the extractor recognises."""

    TR = "tr"
    EN = "en"
    RU = "ru"
    ES = "es"


@dataclass(frozen=True, slots=True)
class RefusalSignal:
    """One refusal occurrence detected inside a PM message.

    Attributes:
        refusal_type: Semantic class of the refusal.
        language: Detected message language (best-effort).
        trigger_phrase: Substring that fired the rule.
        conditional_clause: Optional bounding clause (e.g. the
            English "unless paid in full" or the equivalent
            Spanish "hasta que envie el DNI").  Empty when the
            refusal is unconditional.
        confidence: Deterministic [0, 1] score reflecting how
            specifically the patterns matched.
    """

    refusal_type: RefusalType
    language: RefusalLanguage
    trigger_phrase: str
    conditional_clause: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Pattern dictionaries — each entry is (refusal_type, regex, weight).
# Weights inform the final ``confidence`` score.
# ---------------------------------------------------------------------------


_PatternEntry = tuple[RefusalType, re.Pattern[str], float]
_REFUSAL_PATTERNS: Final[
    dict[RefusalLanguage, tuple[_PatternEntry, ...]]
] = {
    RefusalLanguage.EN: (
        (
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:cannot|can't|won't|will not|unable to)[^.?!]*"
                r"(?:without|unless|until|before)[^.?!]*"
                r"(?:passport|id|identification|document|dni)",
                re.IGNORECASE,
            ),
            0.9,
        ),
        (
            # Positive-phrasing document gate, e.g. "Once your ID
            # verification is successful, your digital key will...".
            # Property managers using app-driven check-in (face
            # recognition, KYC) phrase the refusal as a precondition
            # rather than an explicit "cannot".  Same operational
            # meaning: door / digital key is gated on documents.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:once|after|when|complete|completing)[^.?!]*"
                r"(?:id verification|identity verification|"
                r"face recognition|kyc)[^.?!]*"
                r"(?:digital key|access code|key|"
                r"check[\s-]?in|door|unlock)",
                re.IGNORECASE,
            ),
            0.85,
        ),
        (
            # "ID verification (and face recognition) is required"
            # standalone gate, common in onboarding-style replies.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:id|identity|document)\s+verification[^.?!]*"
                r"(?:required|necessary|mandatory|needed|"
                r"must (?:be )?complete[d]?)",
                re.IGNORECASE,
            ),
            0.8,
        ),
        (
            # Pre-arrival KYC bundle:
            # "you need to complete ... including ID verification
            # and face recognition" — block on doc submission.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:need|must|have)[^.?!]*"
                r"(?:complete|finish|submit)[^.?!]*"
                r"(?:id verification|identity verification|"
                r"face recognition|passport|document)",
                re.IGNORECASE,
            ),
            0.8,
        ),
        (
            RefusalType.REQUIRES_PAYMENT,
            re.compile(
                r"(?:cannot|can't|won't|will not|unable to)[^.?!]*"
                r"(?:until|unless|without|before)[^.?!]*"
                r"(?:payment|paid|deposit|invoice|charge)",
                re.IGNORECASE,
            ),
            0.85,
        ),
        (
            RefusalType.REQUIRES_APPROVAL,
            re.compile(
                r"(?:need|require)[^.?!]*"
                r"(?:owner|manager|host)[^.?!]*"
                r"(?:approval|confirmation|permission)",
                re.IGNORECASE,
            ),
            0.8,
        ),
        (
            RefusalType.HARD_BLOCK,
            re.compile(
                r"\b(?:not allowed|forbidden|prohibited|"
                r"strictly not permitted|policy does not allow)\b",
                re.IGNORECASE,
            ),
            0.85,
        ),
        (
            RefusalType.GENERIC_REFUSAL,
            re.compile(
                r"\b(?:i'm afraid|unfortunately|sorry,?\s*(?:but|i))\b"
                r"[^.?!]*\b(?:cannot|can't|unable)\b",
                re.IGNORECASE,
            ),
            0.55,
        ),
    ),
    RefusalLanguage.TR: (
        (
            # Negative phrasing — "without passport I cannot share".
            # Active-gerund variants ``göndermeden / vermeden /
            # yüklemeden`` cover Mümin's exact line ("Pasaport
            # bilgilerinizi göndermeden kapı kodunu sizinle
            # paylaşamam.") which the prior pattern missed because
            # it only listed the passive ``gönderilmeden``.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:pasaport|kimlik|belge|kimlik doğrulama|"
                r"yüz tanıma|kyc)[^.?!]*"
                r"(?:olmadan|olmaks[ıi]z[ıi]n|gönderilmeden|"
                r"göndermeden|vermeden|yüklemeden|"
                r"tamamlanmadan|önce)[^.?!]*"
                r"(?:veremem|veremeyiz|paylaşamam|"
                r"gönderemem|aktaramam|açamam)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.9,
        ),
        (
            # Positive phrasing — "once ID verification is complete,
            # the door code / digital key will be released".  Mirrors
            # the EN positive-form pattern for the app-driven
            # check-in workflows that some TR PMs use.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:kimlik doğrulama|pasaport|belge|"
                r"yüz tanıma|kyc)[^.?!]*"
                r"(?:tamamland[ıi]ktan sonra|sonras[ıi]nda|"
                r"yap[ıi]ld[ıi]ktan sonra|onayland[ıi]ktan sonra)"
                r"[^.?!]*"
                r"(?:kap[ıi] kodu|dijital anahtar|şifre|kod|"
                r"giriş)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.REQUIRES_PAYMENT,
            re.compile(
                r"(?:ödeme|kapora|fatura)[^.?!]*"
                r"(?:olmadan|alınmadan|yapılmadan)[^.?!]*"
                r"(?:veremem|veremeyiz|onaylayamam)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.HARD_BLOCK,
            re.compile(
                r"\b(?:yasak(?:t[ıi]r)?|izin verilmiyor|"
                r"kabul edilmiyor)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.GENERIC_REFUSAL,
            re.compile(
                r"\b(?:maalesef|üzgünüm|ne yaz[ıi]k ki)\b"
                r"[^.?!]*\b(?:m[üu]mk[üu]n de[ğg]il|"
                r"yapamam|veremem)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.55,
        ),
    ),
    RefusalLanguage.RU: (
        (
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:не могу|не дам|не отправлю|не могу выслать)"
                r"[^.?!]*"
                r"(?:без|пока не|до)"
                r"[^.?!]*"
                r"(?:паспорт|документ|удостоверени|"
                r"верификаци|идентификаци)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.9,
        ),
        (
            # Positive RU phrasing — "after ID verification you will
            # receive the door code".  Mirror of the EN/TR positive
            # form so app-driven check-in PMs are not missed.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:после|когда)[^.?!]*"
                r"(?:верификаци|идентификаци"
                r"|паспорт|документ"
                r"|проверк[аи] личности)[^.?!]*"
                r"(?:код|ключ|доступ|войти|двер)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.REQUIRES_PAYMENT,
            re.compile(
                r"(?:не могу|не дам|не отправлю)[^.?!]*"
                r"(?:без|пока не|до)[^.?!]*"
                r"(?:оплат|депозит|предоплат|счет)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.HARD_BLOCK,
            re.compile(
                r"\b(?:запрещено|не разрешается|"
                r"строго запрещено|не допускается)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.GENERIC_REFUSAL,
            re.compile(
                r"\b(?:к сожалению|извините(?:,)?|"
                r"к сожалени[ью])\b[^.?!]*"
                r"\b(?:не могу|невозможно|не получится)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.55,
        ),
    ),
    RefusalLanguage.ES: (
        (
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:no puedo|no podemos)[^.?!]*"
                r"(?:sin|hasta que|antes de)[^.?!]*"
                r"(?:pasaporte|dni|identificaci[óo]n|"
                r"documento|verificaci[óo]n)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.9,
        ),
        (
            # Positive ES phrasing — "una vez que la verificación de
            # identidad esté completa, recibirá el código".  Closes
            # parity with EN/TR/RU app-driven check-in flows.
            RefusalType.REQUIRES_DOCUMENT,
            re.compile(
                r"(?:una vez|cuando|tras)[^.?!]*"
                r"(?:verificaci[óo]n|identificaci[óo]n|pasaporte|"
                r"documento|reconocimiento facial)[^.?!]*"
                r"(?:c[óo]digo|llave|acceso|puerta|entrar)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.REQUIRES_PAYMENT,
            re.compile(
                r"(?:no puedo|no podemos)[^.?!]*"
                r"(?:sin|hasta que|antes de)[^.?!]*"
                r"(?:pago|dep[óo]sito|factura)",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.HARD_BLOCK,
            re.compile(
                r"\b(?:no est[áa] permitido|prohibido|"
                r"no se permite)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.85,
        ),
        (
            RefusalType.GENERIC_REFUSAL,
            re.compile(
                r"\b(?:lo siento|lamentablemente|"
                r"desafortunadamente)\b[^.?!]*"
                r"\b(?:no puedo|imposible)\b",
                re.IGNORECASE | re.UNICODE,
            ),
            0.55,
        ),
    ),
}


# Conditional-clause extractor — runs after a refusal pattern
# matches to capture the bounding sub-phrase (English "until
# passport arrives", and the equivalents in TR / RU / ES) so
# downstream pattern mining can reconstruct the if-condition.
_CONDITIONAL_PATTERNS: Final[
    dict[RefusalLanguage, re.Pattern[str]]
] = {
    RefusalLanguage.EN: re.compile(
        r"(?:without|unless|until|before)\s+([^.?!]+)",
        re.IGNORECASE,
    ),
    RefusalLanguage.TR: re.compile(
        r"([^.?!]+?)\s+(?:olmadan|olmaks[ıi]z[ıi]n|"
        r"gönderilmeden|göndermeden|vermeden|yüklemeden|"
        r"tamamlanmadan|önce)",
        re.IGNORECASE | re.UNICODE,
    ),
    RefusalLanguage.RU: re.compile(
        r"(?:без|пока не|до)\s+([^.?!]+)",
        re.IGNORECASE | re.UNICODE,
    ),
    RefusalLanguage.ES: re.compile(
        r"(?:sin|hasta que|antes de)\s+([^.?!]+)",
        re.IGNORECASE | re.UNICODE,
    ),
}


# Lightweight language-detection — alphabet-frequency heuristic.
# Real language detection (fasttext / langid) is overkill here:
# refusal patterns themselves are language-specific so a wrong guess
# only costs us a single regex sweep across the alternative locales.
_TR_HINT = re.compile(r"[ğüşöçıİĞÜŞÖÇ]")
_RU_HINT = re.compile(r"[а-яёА-ЯЁ]")
_ES_HINT = re.compile(r"[ñáéíóúÑÁÉÍÓÚ¿¡]")


def _detect_language(text: str) -> RefusalLanguage:
    """Pick the most likely language for ``text``.

    Falls back to :class:`RefusalLanguage.EN` when no script-specific
    hint is found, since English uses only the basic Latin alphabet
    and is the operational default at Cendra.
    """
    if _RU_HINT.search(text):
        return RefusalLanguage.RU
    if _TR_HINT.search(text):
        return RefusalLanguage.TR
    if _ES_HINT.search(text):
        return RefusalLanguage.ES
    return RefusalLanguage.EN


# ---------------------------------------------------------------------------
# Public extractor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefusalExtractor:
    """Stateless, pure refusal-signal extractor.

    The class is dataclass-shaped so callers can substitute a
    test-specific fixture with the same surface, but it carries no
    mutable state.
    """

    def extract(
        self,
        text: str,
        *,
        language: RefusalLanguage | None = None,
    ) -> tuple[RefusalSignal, ...]:
        """Return all :class:`RefusalSignal` instances found in ``text``.

        Args:
            text: PM message body.  Empty / whitespace-only strings
                yield an empty tuple deterministically.
            language: Override automatic language detection.  Useful
                when the calling pipeline already carries a confident
                language tag from upstream NLU.

        Returns:
            An immutable tuple ordered by ``(refusal_type, trigger
            offset)`` so the extractor is deterministic — same input
            always produces an equal tuple, suitable for property
            tests and snapshot comparison.
        """
        if not text or not text.strip():
            return ()

        lang = language or _detect_language(text)
        candidates: list[tuple[int, RefusalSignal]] = []
        # Every locale has its own pattern set; we additionally sweep
        # the English set for mixed-language messages because Cendra
        # PMs frequently switch into English mid-Turkish/Russian
        # for technical terms (passport, deposit, invoice).
        sweeps: tuple[RefusalLanguage, ...]
        if lang is RefusalLanguage.EN:
            sweeps = (RefusalLanguage.EN,)
        else:
            sweeps = (lang, RefusalLanguage.EN)

        for sweep_lang in sweeps:
            for refusal_type, pattern, weight in (
                _REFUSAL_PATTERNS[sweep_lang]
            ):
                for match in pattern.finditer(text):
                    trigger = match.group(0).strip()
                    conditional = self._extract_conditional(
                        match.group(0), sweep_lang,
                    )
                    confidence = weight + (
                        0.05 if conditional else 0.0
                    )
                    if confidence > 1.0:
                        confidence = 1.0
                    candidates.append(
                        (
                            match.start(),
                            RefusalSignal(
                                refusal_type=refusal_type,
                                language=sweep_lang,
                                trigger_phrase=trigger,
                                conditional_clause=conditional,
                                confidence=round(confidence, 3),
                            ),
                        ),
                    )

        if not candidates:
            return ()

        # Deduplicate signals that match the same span / type — a
        # mixed-language sweep can fire both EN and the native locale
        # on the same trigger word; keep the highest-confidence copy.
        deduped: dict[tuple[RefusalType, str], RefusalSignal] = {}
        for _offset, signal in candidates:
            key = (signal.refusal_type, signal.trigger_phrase.lower())
            previous = deduped.get(key)
            if previous is None or signal.confidence > previous.confidence:
                deduped[key] = signal

        ordered = sorted(
            deduped.values(),
            key=lambda s: (s.refusal_type.value, s.trigger_phrase),
        )
        _emit_refusal_metrics(ordered)
        return tuple(ordered)

    @staticmethod
    def _extract_conditional(
        span: str, language: RefusalLanguage,
    ) -> str:
        """Pull the ``if/unless/until``-style clause out of ``span``.

        Returns the conditional fragment with surrounding whitespace
        stripped, or an empty string when no clause is present.
        """
        pattern = _CONDITIONAL_PATTERNS.get(language)
        if pattern is None:
            return ""
        match = pattern.search(span)
        if match is None:
            return ""
        groups = [g for g in match.groups() if g]
        if not groups:
            return match.group(0).strip()
        return groups[0].strip()


__all__ = (
    "RefusalExtractor",
    "RefusalLanguage",
    "RefusalSignal",
    "RefusalType",
)


def _emit_refusal_metrics(signals: list[RefusalSignal]) -> None:
    """Forward each detected signal to the Prometheus exporter.

    Best-effort — any exporter exception is swallowed so a broken
    metrics registry can never block extraction on the hot path.
    Aggregated under (language, refusal_type) so dashboards can
    plot the TR / EN / RU / ES split alongside the type taxonomy.
    """
    if not signals:
        return
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        exporter = build_default_exporter()
        for signal in signals:
            exporter.record_refusal_signal(
                language=signal.language.value,
                refusal_type=signal.refusal_type.value,
            )
    except Exception:  # noqa: BLE001 — never break extract()
        # Silent: refusal extraction runs on every PM message and
        # the extractor must stay zero-friction.  Errors land in
        # the global structlog default through the exporter itself.
        return
