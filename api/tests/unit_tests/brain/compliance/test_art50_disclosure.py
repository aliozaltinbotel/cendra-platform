"""Behaviour of :func:`disclosure_for` Art. 50 helper."""

from __future__ import annotations

import pytest

from core.brain.compliance.art50_disclosure import (
    DEFAULT_DISCLOSURES,
    DisclosureLocale,
    disclosure_for,
)


@pytest.mark.parametrize(
    "locale",
    [lc.value for lc in DisclosureLocale],
    ids=lambda v: f"locale_{v}",
)
def test_known_locale_returns_native_text(locale: str) -> None:
    """Each locale returns its own pre-translated text."""
    disclosure = disclosure_for(locale)
    assert disclosure.locale.value == locale
    assert disclosure.text == DEFAULT_DISCLOSURES[disclosure.locale]


def test_uppercase_locale_normalised() -> None:
    """Locale lookup is case-insensitive."""
    assert disclosure_for("DE").locale is DisclosureLocale.DE


def test_unknown_locale_falls_back_to_english() -> None:
    """Unknown locale codes return the English disclosure."""
    disclosure = disclosure_for("xx")
    assert disclosure.locale is DisclosureLocale.EN


def test_custom_fallback_honoured() -> None:
    """Caller-supplied fallback overrides the English default."""
    disclosure = disclosure_for("xx", fallback=DisclosureLocale.ES)
    assert disclosure.locale is DisclosureLocale.ES


def test_disclosure_is_immutable() -> None:
    """Returned object is a frozen dataclass."""
    disclosure = disclosure_for("en")
    with pytest.raises((AttributeError, TypeError)):
        disclosure.text = "x"  # type: ignore[misc]
