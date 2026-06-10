"""Foundation scenario parse is memoised by content digest.

The shipped foundation markdown (470+ scenarios) is regex-parsed, and
several components load it independently at startup. ``load_foundation_
scenarios`` caches the parse keyed by the SHA-256 of the document text,
so repeated loads of the same bytes are served from cache while a
genuine content change still triggers a re-parse (the cache key is the
content, not the path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import brain_engine.patterns.foundation_registry as fr


def _counting_parser(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace ``parse_foundation_document`` with a call-counting wrapper
    that still returns the real parse output."""
    calls = {"n": 0}
    real = fr.parse_foundation_document

    def _spy(text: str):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(fr, "parse_foundation_document", _spy)
    return calls


def test_same_content_is_parsed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two loads of an unchanged file parse once; the second is a cache
    hit returning the identical cached tuple."""
    fr._SCENARIO_PARSE_CACHE.clear()
    calls = _counting_parser(monkeypatch)
    doc = tmp_path / "foundation.md"
    doc.write_text(
        "# Foundation\n\n## s1_1 Greeting\n### Trigger\nhello\n",
        encoding="utf-8",
    )

    first = fr.load_foundation_scenarios(doc)
    second = fr.load_foundation_scenarios(doc)

    assert calls["n"] == 1
    assert second is first


def test_changed_content_is_reparsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewriting the SAME path with new bytes re-parses — the cache keys
    on content, so callers (and tests) never get stale data."""
    fr._SCENARIO_PARSE_CACHE.clear()
    calls = _counting_parser(monkeypatch)
    doc = tmp_path / "foundation.md"

    doc.write_text("# Foundation\n\n## s1_1 A\n### Trigger\naaa\n", "utf-8")
    fr.load_foundation_scenarios(doc)
    doc.write_text("# Foundation\n\n## s1_2 B\n### Trigger\nbbb\n", "utf-8")
    fr.load_foundation_scenarios(doc)

    assert calls["n"] == 2


def test_missing_file_returns_empty_without_caching(
    tmp_path: Path,
) -> None:
    """A missing file still degrades to an empty tuple (no crash, no
    cache entry)."""
    fr._SCENARIO_PARSE_CACHE.clear()
    result = fr.load_foundation_scenarios(tmp_path / "nope.md")
    assert result == ()
    assert len(fr._SCENARIO_PARSE_CACHE) == 0
