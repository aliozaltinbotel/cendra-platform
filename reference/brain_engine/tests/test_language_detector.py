"""Tests for :class:`LanguageDetectorService` — Layer 1.

Pins the public contract:

* ISO 639-1 codes (``"en"`` / ``"tr"`` / …) — not the third-party
  enum.
* Empty / whitespace input ⇒ default language, ``is_fallback=True``.
* Below-threshold confidence ⇒ default language, ``is_fallback=True``.
* Known sentences across EN / TR / RU / ES / DE / SK map to the
  expected ISO codes — the languages Mümin's archive carries.
* Constructor rejects invalid knobs (empty language tuple, empty
  default, out-of-range confidence).
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.language_detector import (
    DEFAULT_LANGUAGE,
    DEFAULT_MIN_CONFIDENCE,
    DetectionResult,
    LanguageDetectorService,
)


@pytest.fixture(scope="module")
def detector() -> LanguageDetectorService:
    """Module-scoped to amortise the ~50 MB lingua model load."""
    return LanguageDetectorService()


@pytest.mark.parametrize(
    "text,expected",
    [
        # English — long enough to be unambiguous
        (
            "Could you please send me the apartment access code "
            "before we arrive tomorrow afternoon?",
            "en",
        ),
        # Turkish (Mümin's primary)
        (
            "Erken giriş yapmamız mümkün mü? Saat 13'te orada "
            "olacağız.",
            "tr",
        ),
        (
            "Üzgünüz, bu sefer yapamayız. Müsait değil maalesef.",
            "tr",
        ),
        # Russian
        (
            "К сожалению, на эту дату не получится разместить вас "
            "раньше.",
            "ru",
        ),
        # Spanish
        (
            "Lo sentimos mucho, no podemos atender esa solicitud "
            "ahora mismo.",
            "es",
        ),
        # German
        (
            "Leider ist ein früherer Check-in an diesem Datum "
            "nicht möglich.",
            "de",
        ),
        # Slovak
        (
            "Bohužiaľ, na tento dátum to nie je možné, "
            "potvrdiť skorší príchod nemôžeme.",
            "sk",
        ),
    ],
)
def test_known_sentences_map_to_iso_codes(
    detector: LanguageDetectorService,
    text: str,
    expected: str,
) -> None:
    """Each canonical sentence maps to its expected ISO code."""
    result = detector.detect(text)
    assert result.language == expected, (
        f"text {text!r} expected {expected!r} got {result!r}"
    )
    assert result.confidence > 0.0


def test_empty_text_returns_default(
    detector: LanguageDetectorService,
) -> None:
    """Empty input short-circuits to the configured default."""
    result = detector.detect("")
    assert result.language == DEFAULT_LANGUAGE
    assert result.confidence == 0.0
    assert result.is_fallback is True


def test_whitespace_only_text_returns_default(
    detector: LanguageDetectorService,
) -> None:
    """Whitespace-only input behaves identically to empty."""
    result = detector.detect("   \n\t  ")
    assert result.language == DEFAULT_LANGUAGE
    assert result.confidence == 0.0
    assert result.is_fallback is True


def test_below_threshold_marks_fallback_but_returns_top_language(
) -> None:
    """Uncertain inputs are flagged ``is_fallback=True`` without dropping the signal.

    The detector keeps returning the best-ranked language so a
    downstream caller can still specialise; the
    ``is_fallback=True`` flag tells the audit layer the answer
    was below confidence.  Tested with a single ASCII token that
    overlaps across many configured languages — the detector
    cannot rank confidently and the wrapper marks the result as
    a fallback.
    """
    strict = LanguageDetectorService(min_confidence=0.9999999)
    result = strict.detect("ok")
    assert result.is_fallback is True
    # Despite the fallback flag, the top language is reported.
    assert result.language
    assert 0.0 <= result.confidence <= 1.0


def test_custom_default_language() -> None:
    """``default_language`` knob propagates to empty-input fallback."""
    detector = LanguageDetectorService(default_language="tr")
    result = detector.detect("")
    assert result.language == "tr"


def test_constructor_rejects_empty_languages() -> None:
    """At least one language must be configured."""
    with pytest.raises(ValueError, match="languages"):
        LanguageDetectorService(languages=())


def test_constructor_rejects_empty_default() -> None:
    """``default_language`` cannot be empty."""
    with pytest.raises(ValueError, match="default_language"):
        LanguageDetectorService(default_language="")


def test_constructor_rejects_out_of_range_confidence() -> None:
    """``min_confidence`` outside ``[0, 1]`` is rejected."""
    with pytest.raises(ValueError, match="min_confidence"):
        LanguageDetectorService(min_confidence=-0.1)
    with pytest.raises(ValueError, match="min_confidence"):
        LanguageDetectorService(min_confidence=1.5)


def test_detection_result_rejects_invalid_confidence() -> None:
    """``DetectionResult`` validates ``confidence`` bounds."""
    with pytest.raises(ValueError, match="confidence"):
        DetectionResult(
            language="en",
            confidence=1.5,
            is_fallback=False,
        )


def test_detection_result_rejects_empty_language() -> None:
    """``DetectionResult`` requires a non-empty language code."""
    with pytest.raises(ValueError, match="language"):
        DetectionResult(
            language="",
            confidence=0.5,
            is_fallback=False,
        )


def test_short_message_falls_back_gracefully(
    detector: LanguageDetectorService,
) -> None:
    """Very short ambiguous strings do not raise; they fall back."""
    # ``"OK"`` is ambiguous across many languages.
    result = detector.detect("OK")
    # Either fallback to default OR resolves to one of the configured
    # languages; the contract is "never raises".
    assert result.language
    assert 0.0 <= result.confidence <= 1.0
