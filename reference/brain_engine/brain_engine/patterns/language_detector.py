"""Layer 1 of the intelligent classifier — message language detection.

Brain Engine's pre-existing keyword-classification chain carried
hand-curated multilingual phrase lists (EN / TR / RU / ES / DE / SK)
that grew unbounded as Mümin's archive surfaced new languages.
This module is the first step in retiring those lists: an
offline, sub-10ms language detector that lets the downstream
intelligent classifier specialise (e.g. send a TR message
through a TR-aware embedding model or LLM prompt).

Design constraints
------------------

* **Offline.**  No external API call.  ``lingua-language-detector``
  ships pretrained n-gram + lookup tables (~50 MB) that load once
  per process.
* **Deterministic.**  Same input → same output across pod
  restarts.  The detector has no learnable weights modified at
  runtime.
* **ISO 639-1 codes.**  The public surface returns two-letter
  codes (``"en"`` / ``"tr"`` / …) so downstream callers do not
  depend on the third-party enum.
* **Confidence-gated fallback.**  When the detector cannot rank
  a single language above the configured threshold (short
  messages, mixed scripts) the function returns the configured
  ``default_language`` rather than guessing.  ``"en"`` is the
  pragmatic default since the Brain Engine prompt template and
  the canonical scenario registry are authored in English.

References
----------
* lingua-language-detector — Peter M. Stahl, Apache 2.0
  https://github.com/pemistahl/lingua-py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

__all__ = [
    "DEFAULT_LANGUAGE",
    "DEFAULT_LANGUAGES",
    "DEFAULT_MIN_CONFIDENCE",
    "DetectionResult",
    "LanguageDetectorService",
    "get_shared_language_detector",
]


DEFAULT_LANGUAGES: Final[tuple[Language, ...]] = (
    Language.ENGLISH,
    Language.TURKISH,
    Language.RUSSIAN,
    Language.SPANISH,
    Language.GERMAN,
    Language.SLOVAK,
    Language.FRENCH,
    Language.ITALIAN,
    Language.PORTUGUESE,
    Language.DUTCH,
    Language.POLISH,
    Language.CZECH,
)

DEFAULT_LANGUAGE: Final[str] = "en"
DEFAULT_MIN_CONFIDENCE: Final[float] = 0.5

# Lingua → ISO 639-1 mapping.  Only languages we expose to callers
# need to be present; missing entries fall back to ``DEFAULT_LANGUAGE``
# via :meth:`LanguageDetectorService._iso_code`.
_LINGUA_TO_ISO: Final[dict[Language, str]] = {
    Language.ENGLISH: "en",
    Language.TURKISH: "tr",
    Language.RUSSIAN: "ru",
    Language.SPANISH: "es",
    Language.GERMAN: "de",
    Language.SLOVAK: "sk",
    Language.FRENCH: "fr",
    Language.ITALIAN: "it",
    Language.PORTUGUESE: "pt",
    Language.DUTCH: "nl",
    Language.POLISH: "pl",
    Language.CZECH: "cs",
}


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of one :meth:`LanguageDetectorService.detect` call.

    Attributes:
        language: ISO 639-1 code; ``DEFAULT_LANGUAGE`` when the
            confidence floor was not met.
        confidence: ``[0.0, 1.0]`` — the detector's reported
            confidence in ``language``.  ``0.0`` when the input
            was empty.
        is_fallback: ``True`` when the detector did not produce a
            confident answer and the result reflects the
            configured default.  Audit-friendly so downstream
            consumers can mark uncertain cases.
    """

    language: str
    confidence: float
    is_fallback: bool

    def __post_init__(self) -> None:
        if not self.language:
            raise ValueError("language code required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "confidence must be in [0.0, 1.0]"
            )


class LanguageDetectorService:
    """Thin wrapper over ``lingua`` with a stable public surface.

    Heavy initialisation (n-gram tables, ~50 MB) happens once on
    construction; subsequent :meth:`detect` calls are sub-ms after
    JIT warm-up.

    The detector is *thread-safe* by virtue of the underlying
    ``lingua`` library; share a single instance per process.
    """

    def __init__(
        self,
        *,
        languages: tuple[Language, ...] = DEFAULT_LANGUAGES,
        default_language: str = DEFAULT_LANGUAGE,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        if not languages:
            raise ValueError("languages must be non-empty")
        if not default_language:
            raise ValueError("default_language required")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                "min_confidence must be in [0.0, 1.0]"
            )
        self._default = default_language
        self._min_confidence = min_confidence
        self._detector: LanguageDetector = (
            LanguageDetectorBuilder.from_languages(*languages).build()
        )

    def detect(self, text: str) -> DetectionResult:
        """Return the language code best matching ``text``.

        Empty / whitespace-only inputs short-circuit to the
        configured default with ``confidence=0.0``.  Inputs the
        detector cannot rank confidently fall back to the same
        default with the highest reported confidence carried
        through, so audit logs preserve the original signal.
        """
        if not text or not text.strip():
            return DetectionResult(
                language=self._default,
                confidence=0.0,
                is_fallback=True,
            )
        ranked = self._detector.compute_language_confidence_values(
            text,
        )
        if not ranked:
            return DetectionResult(
                language=self._default,
                confidence=0.0,
                is_fallback=True,
            )
        top = ranked[0]
        confidence = float(top.value)
        if confidence < self._min_confidence:
            return DetectionResult(
                language=self._default,
                confidence=confidence,
                is_fallback=True,
            )
        return DetectionResult(
            language=self._iso_code(top.language),
            confidence=confidence,
            is_fallback=False,
        )

    def _iso_code(self, language: Language) -> str:
        """Return the ISO 639-1 code for ``language`` or the default."""
        return _LINGUA_TO_ISO.get(language, self._default)


_SHARED_DETECTOR: LanguageDetectorService | None = None


def get_shared_language_detector() -> LanguageDetectorService:
    """Return the process-wide :class:`LanguageDetectorService`.

    The detector loads ~50 MB of n-gram tables on construction, so
    every caller that only needs default-configuration detection
    (the intelligent classifier, the missing-info escalation
    language anchor, …) must share one instance rather than paying
    that cost per consumer.  The underlying ``lingua`` detector is
    thread-safe, so a single shared instance is safe to reuse across
    the async pipeline.

    Lazily constructed on first call so import of this module stays
    cheap and test code can monkeypatch the module global before the
    first detection.
    """
    global _SHARED_DETECTOR
    if _SHARED_DETECTOR is None:
        _SHARED_DETECTOR = LanguageDetectorService()
    return _SHARED_DETECTOR
