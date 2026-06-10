"""Emotion-aware tone classification and rewriting.

The advisory (§12 narrative) flags the current renderer as
emotion-blind: a frustrated guest gets the same template as a
happy one.  This module adds a deterministic, lexicon-based
classifier so the renderer can pick a tone-appropriate prefix
without involving an LLM.

Two pieces ship together:

* :func:`classify_emotion` — score the input text against four
  primitives (positive / concerned / frustrated / sad) and
  return the dominant :class:`EmotionalTone` plus a confidence
  score.  The classifier is intentionally simple — stem-prefix
  matching against per-language lexicons — because the goal is
  *signal at the boundary*, not opinionated NLP.
* :class:`ToneRewriter` — given a target tone and a base reply,
  prepend / append a short template phrase that nudges the
  reply toward the requested tone.  The rewriter never edits
  the body itself (that stays the renderer's job).

Both surfaces are pure compute, deterministic, and stdlib-only
so they can run inside any worker tier without dependency creep.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "DEFAULT_LEXICONS",
    "EmotionScore",
    "EmotionalTone",
    "ToneRewriter",
    "classify_emotion",
]


class EmotionalTone(StrEnum):
    """Coarse tone bucket the renderer can react to."""

    NEUTRAL = "neutral"
    POSITIVE = "positive"
    CONCERNED = "concerned"
    FRUSTRATED = "frustrated"
    EMPATHETIC = "empathetic"


# Stem-prefix lexicons per language.  Stems are deliberately
# short — `wonder` matches "wonderful", "wonders", "wondered".
# Mapped to the pre-tone bucket; ``EMPATHETIC`` is reserved for
# the rewriter and is never produced directly by the classifier.
_EN_LEXICON: Final[Mapping[str, EmotionalTone]] = MappingProxyType(
    {
        "wonder": EmotionalTone.POSITIVE,
        "great": EmotionalTone.POSITIVE,
        "love": EmotionalTone.POSITIVE,
        "happy": EmotionalTone.POSITIVE,
        "thank": EmotionalTone.POSITIVE,
        "please": EmotionalTone.POSITIVE,
        "concern": EmotionalTone.CONCERNED,
        "worri": EmotionalTone.CONCERNED,
        "anxious": EmotionalTone.CONCERNED,
        "afraid": EmotionalTone.CONCERNED,
        "uncertain": EmotionalTone.CONCERNED,
        "angry": EmotionalTone.FRUSTRATED,
        "furious": EmotionalTone.FRUSTRATED,
        "frustrat": EmotionalTone.FRUSTRATED,
        "terrible": EmotionalTone.FRUSTRATED,
        "awful": EmotionalTone.FRUSTRATED,
        "ridiculous": EmotionalTone.FRUSTRATED,
        "unaccept": EmotionalTone.FRUSTRATED,
        "scam": EmotionalTone.FRUSTRATED,
    },
)

_RU_LEXICON: Final[Mapping[str, EmotionalTone]] = MappingProxyType(
    {
        "спасиб": EmotionalTone.POSITIVE,
        "благодар": EmotionalTone.POSITIVE,
        "отличн": EmotionalTone.POSITIVE,
        "прекрасн": EmotionalTone.POSITIVE,
        "люблю": EmotionalTone.POSITIVE,
        "беспоко": EmotionalTone.CONCERNED,
        "волну": EmotionalTone.CONCERNED,
        "тревож": EmotionalTone.CONCERNED,
        "ужасн": EmotionalTone.FRUSTRATED,
        "кошмар": EmotionalTone.FRUSTRATED,
        "недопустим": EmotionalTone.FRUSTRATED,
        "обма": EmotionalTone.FRUSTRATED,
        "возмут": EmotionalTone.FRUSTRATED,
    },
)

_TR_LEXICON: Final[Mapping[str, EmotionalTone]] = MappingProxyType(
    {
        "teşekkür": EmotionalTone.POSITIVE,
        "harika": EmotionalTone.POSITIVE,
        "mükemmel": EmotionalTone.POSITIVE,
        "memnun": EmotionalTone.POSITIVE,
        "endiş": EmotionalTone.CONCERNED,
        "kayg": EmotionalTone.CONCERNED,
        "berbat": EmotionalTone.FRUSTRATED,
        "rezalet": EmotionalTone.FRUSTRATED,
        "kabul edilemez": EmotionalTone.FRUSTRATED,
        "kızgın": EmotionalTone.FRUSTRATED,
    },
)


DEFAULT_LEXICONS: Final[Mapping[str, Mapping[str, EmotionalTone]]] = (
    MappingProxyType(
        {
            "en": _EN_LEXICON,
            "ru": _RU_LEXICON,
            "tr": _TR_LEXICON,
        },
    )
)


@dataclass(frozen=True, slots=True)
class EmotionScore:
    """Per-tone counters plus the picked dominant tone.

    Attributes:
        tone: Dominant :class:`EmotionalTone` — ``NEUTRAL`` when
            no lexical hit fires.
        confidence: Share of total hits that voted for ``tone``.
            ``0.0`` for the neutral case.
        hits: Per-tone hit counts.  Always lists every tone (zero
            when absent).
    """

    tone: EmotionalTone
    confidence: float
    hits: Mapping[EmotionalTone, int]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "confidence must lie in [0.0, 1.0]",
            )
        object.__setattr__(
            self,
            "hits",
            MappingProxyType(dict(self.hits)),
        )


def classify_emotion(
    text: str,
    *,
    language: str = "en",
    lexicons: Mapping[str, Mapping[str, EmotionalTone]] = (
        DEFAULT_LEXICONS
    ),
) -> EmotionScore:
    """Classify ``text`` against the per-language lexicon.

    Empty input returns ``NEUTRAL`` with zero confidence.
    Unknown languages fall through to ``NEUTRAL`` rather than
    raising — the caller is expected to handle multi-language
    routing separately.
    """
    base = {tone: 0 for tone in EmotionalTone}
    if not text:
        return EmotionScore(
            tone=EmotionalTone.NEUTRAL,
            confidence=0.0,
            hits=base,
        )
    table = lexicons.get(language)
    if table is None:
        return EmotionScore(
            tone=EmotionalTone.NEUTRAL,
            confidence=0.0,
            hits=base,
        )
    lowered = text.lower()
    hits = dict(base)
    for stem, tone in table.items():
        if stem in lowered:
            hits[tone] += 1
    total = sum(hits.values())
    if total == 0:
        return EmotionScore(
            tone=EmotionalTone.NEUTRAL,
            confidence=0.0,
            hits=hits,
        )
    # Pick the dominant tone with deterministic tie-break by
    # tone name (alphabetical) so equal counts always resolve
    # the same way.
    dominant = max(
        hits.items(),
        key=lambda kv: (kv[1], kv[0].value),
    )
    return EmotionScore(
        tone=dominant[0],
        confidence=dominant[1] / total,
        hits=hits,
    )


_REWRITE_PREFIXES: Final[Mapping[
    EmotionalTone, Mapping[str, str]
]] = MappingProxyType(
    {
        EmotionalTone.EMPATHETIC: MappingProxyType(
            {
                "en": "I hear how upsetting this is. ",
                "ru": "Понимаю, насколько это тяжело. ",
                "tr": "Bunun ne kadar üzücü olduğunu anlıyorum. ",
            },
        ),
        EmotionalTone.CONCERNED: MappingProxyType(
            {
                "en": "I want to make sure we get this right. ",
                "ru": "Хочу убедиться, что мы решим это правильно. ",
                "tr": "Doğru çözdüğümüzden emin olmak istiyorum. ",
            },
        ),
        EmotionalTone.POSITIVE: MappingProxyType(
            {
                "en": "Glad to help — ",
                "ru": "Рад помочь — ",
                "tr": "Yardımcı olmaktan memnuniyet duyarım — ",
            },
        ),
        EmotionalTone.FRUSTRATED: MappingProxyType(
            {
                "en": "Let's resolve this right now. ",
                "ru": "Давайте сейчас же это уладим. ",
                "tr": "Bunu hemen çözelim. ",
            },
        ),
    },
)


class ToneRewriter:
    """Prepend a tone-appropriate phrase to a reply body.

    The rewriter is intentionally minimal — the renderer keeps
    full ownership of the reply body.  This class only adds a
    short tone-tag at the front so downstream channel-specific
    adapters (WhatsApp, email, etc.) see a coherent voice.

    Args:
        prefixes: Per-tone, per-language prefix table.  Defaults
            to a small embedded set covering EN/RU/TR for the
            non-neutral tones.
    """

    def __init__(
        self,
        prefixes: Mapping[
            EmotionalTone, Mapping[str, str]
        ] = _REWRITE_PREFIXES,
    ) -> None:
        self._prefixes = prefixes

    def rewrite(
        self,
        body: str,
        *,
        target: EmotionalTone,
        language: str = "en",
    ) -> str:
        """Return ``body`` with the tone prefix prepended.

        ``NEUTRAL`` and unknown ``language`` short-circuit and
        return ``body`` unchanged.
        """
        if not body:
            raise ValueError("body must not be empty")
        if target is EmotionalTone.NEUTRAL:
            return body
        per_lang = self._prefixes.get(target)
        if per_lang is None:
            return body
        prefix = per_lang.get(language)
        if prefix is None:
            return body
        return f"{prefix}{body}"

    def supported_targets(self) -> Sequence[EmotionalTone]:
        """Targets the rewriter has prefixes for, sorted."""
        return tuple(
            sorted(self._prefixes.keys(), key=lambda t: t.value),
        )
