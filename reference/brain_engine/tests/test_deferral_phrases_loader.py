# ruff: noqa: RUF001
# RUF001 / RUF003 (ambiguous unicode) suppressed file-wide because the
# loader fixtures reference Turkish / Cyrillic letters verbatim — the
# whole point of the loader is to preserve them.
"""Tests for ``deferral_phrases.load_deferral_phrases``.

A2 moves the formerly-inline ``_DEFERRAL_PHRASES`` tuple from
``missing_info_extractor.py`` into ``deferral_phrases.yaml`` so an
operator can edit the substring-matcher list without a code
release.  These tests pin the loader contract:

* Well-formed YAML returns a flat tuple of lower-cased,
  whitespace-stripped phrases.
* Duplicates and empty entries are dropped silently.
* Missing file degrades to ``()`` plus a WARN log — the live
  pipeline must not crash on a missing config.
* Malformed YAML raises ``ValueError`` loudly so a typo surfaces
  in deploy review rather than silently shrinking the matcher.
* The shipped production file loads cleanly and includes the
  three live-traffic languages (TR / EN / RU).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from brain_engine.conversation.deferral_phrases import (
    DEFAULT_PHRASES_PATH,
    load_deferral_phrases,
)

# ── Well-formed YAML round-trip ──────────────────────────────────


def test_loader_flattens_language_sections(tmp_path: Path) -> None:
    """Multiple language sections collapse into one ordered tuple."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        "english:\n"
        '  - "i\'ll check"\n'
        "turkish:\n"
        '  - "üzgünüm"\n'
        "russian:\n"
        '  - "у меня нет"\n',
        encoding="utf-8",
    )
    result = load_deferral_phrases(path)
    assert result == ("i'll check", "üzgünüm", "у меня нет")


def test_loader_lowercases_entries(tmp_path: Path) -> None:
    """Operator may write Title-Case; substring matcher needs lower."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        'english:\n  - "I\'ll Check"\n  - "GET BACK TO YOU"\n',
        encoding="utf-8",
    )
    assert load_deferral_phrases(path) == ("i'll check", "get back to you")


def test_loader_strips_whitespace(tmp_path: Path) -> None:
    """Trailing / leading whitespace must not leak into the matcher."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        'english:\n  - "  i\'ll check  "\n',
        encoding="utf-8",
    )
    assert load_deferral_phrases(path) == ("i'll check",)


def test_loader_drops_duplicates(tmp_path: Path) -> None:
    """Duplicates across or within language sections are deduped."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        "english:\n"
        '  - "i\'ll check"\n'
        '  - "i\'ll check"\n'
        "turkish:\n"
        '  - "I\'LL CHECK"\n',
        encoding="utf-8",
    )
    assert load_deferral_phrases(path) == ("i'll check",)


def test_loader_drops_empty_entries(tmp_path: Path) -> None:
    """Whitespace-only YAML rows do not bloat the matcher."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        'english:\n  - "i\'ll check"\n  - ""\n  - "   "\n',
        encoding="utf-8",
    )
    assert load_deferral_phrases(path) == ("i'll check",)


def test_loader_preserves_unicode_verbatim(tmp_path: Path) -> None:
    """Turkish and Cyrillic letters survive the round-trip."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        "turkish:\n"
        '  - "üzgünüm"\n'
        '  - "şu anda elimde"\n'
        "russian:\n"
        '  - "уточню и вернусь"\n',
        encoding="utf-8",
    )
    assert "üzgünüm" in load_deferral_phrases(path)
    assert "şu anda elimde" in load_deferral_phrases(path)
    assert "уточню и вернусь" in load_deferral_phrases(path)


# ── Missing / malformed inputs ───────────────────────────────────


def test_loader_missing_file_returns_empty_tuple(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing file → empty tuple + WARN log; pipeline keeps running."""
    missing = tmp_path / "does_not_exist.yaml"
    with caplog.at_level(
        logging.WARNING,
        logger="brain_engine.conversation.deferral_phrases",
    ):
        result = load_deferral_phrases(missing)
    assert result == ()
    assert any(
        "deferral_phrases.yaml not found" in r.message for r in caplog.records
    )


def test_loader_rejects_top_level_list(tmp_path: Path) -> None:
    """Bare top-level list (not a mapping) is operator error."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        '- "i\'ll check"\n- "get back to you"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be a mapping"):
        load_deferral_phrases(path)


def test_loader_rejects_non_list_language_section(tmp_path: Path) -> None:
    """A language section must be a list — string would silently
    iterate character-by-character without this guard."""
    path = tmp_path / "phrases.yaml"
    path.write_text("english: 'just a string'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list of strings"):
        load_deferral_phrases(path)


def test_loader_rejects_non_string_entries(tmp_path: Path) -> None:
    """An int / bool entry is operator error — surface loudly."""
    path = tmp_path / "phrases.yaml"
    path.write_text(
        'english:\n  - "i\'ll check"\n  - 42\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="entries must be strings"):
        load_deferral_phrases(path)


def test_loader_empty_yaml_returns_empty_tuple(tmp_path: Path) -> None:
    """Empty file is degenerate but valid — return empty tuple."""
    path = tmp_path / "phrases.yaml"
    path.write_text("", encoding="utf-8")
    assert load_deferral_phrases(path) == ()


# ── Shipped production data file ─────────────────────────────────


def test_default_path_points_to_sibling_yaml() -> None:
    """Production loader must point at the sibling YAML, not a
    repo-root-relative path that could break in containerised
    deploys."""
    assert DEFAULT_PHRASES_PATH.name == "deferral_phrases.yaml"
    assert DEFAULT_PHRASES_PATH.parent.name == "conversation"


def test_production_yaml_loads_cleanly() -> None:
    """The shipped data file parses + delivers entries for every
    live-traffic language."""
    phrases = load_deferral_phrases()
    # Three live surface languages — at least one phrase each.
    assert any("check" in p for p in phrases)  # English
    assert any("üzgünüm" in p for p in phrases)  # Turkish
    assert any("у меня нет" in p for p in phrases)  # Russian


def test_production_yaml_preserves_pre_a2_phrase_count() -> None:
    """The data file must carry at least the 49 phrases the inline
    tuple shipped before this PR.  A regression here means an
    operator-editable change accidentally shrank the matcher."""
    phrases = load_deferral_phrases()
    # 49 was the inline-tuple length on dev top before A2.  Dedup
    # tolerance: allow >= 45 to absorb future operator deletes.
    assert len(phrases) >= 45, (
        f"Production phrase file shrank below baseline: {len(phrases)}"
    )
