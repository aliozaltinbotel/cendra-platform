"""Loader for the deferral-phrase substring matcher.

``missing_info_extractor.response_has_deferral`` used to carry the
phrases inline as a 49-entry hardcoded tuple covering English,
Turkish and Russian.  This module moves the data into
``deferral_phrases.yaml`` next to the loader so:

* an operator can add / remove a phrase without a code release
  (matches the project-wide rule against hardcoded language
  whitelists — see ``feedback_no_hardcode``);
* duplicates and language-section drift surface in review of one
  small data file rather than a Python tuple inside a 296-line
  module;
* the substring matcher's cost path (no LLM call when no deferral
  detected) is preserved verbatim — only the *source* of the
  phrases moves.

The loader is intentionally narrow: read the YAML, flatten every
language section's list into one tuple, lower-case + strip every
entry, and surface operator errors loudly.  Missing file degrades
to an empty tuple plus a WARN log — the worst case is "every
deferral now reaches the LLM" (correctness preserved, cost only
loss), which is safer than crashing the conversation pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

__all__ = ["DEFAULT_PHRASES_PATH", "load_deferral_phrases"]


logger = logging.getLogger(__name__)


DEFAULT_PHRASES_PATH: Path = Path(__file__).with_name("deferral_phrases.yaml")


def load_deferral_phrases(
    path: Path | str | None = None,
) -> tuple[str, ...]:
    """Read the YAML config and return a flat tuple of phrases.

    Args:
        path: Optional override path.  Defaults to
            ``DEFAULT_PHRASES_PATH`` (the sibling YAML file).  Tests
            pass a temp-dir path to exercise loader behaviour without
            touching the production data file.

    Returns:
        Tuple of lower-cased, whitespace-stripped phrases ready to
        feed straight into the substring matcher.  Empty tuple when
        the file is missing — the live pipeline degrades to
        "every AI response triggers the LLM extractor" rather than
        crashing.

    Raises:
        ValueError: when the YAML parses but does not match the
            expected ``{language: [phrase, ...]}`` shape, or when an
            entry is neither a string nor coercible to one.  Loud
            failure is intentional — a typo in the config must
            surface in deploy review, not silently shrink the
            matcher.
    """
    target = Path(path) if path is not None else DEFAULT_PHRASES_PATH

    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "deferral_phrases.yaml not found at %s — substring "
            "matcher degraded to empty tuple, every deferral reaches "
            "the LLM extractor.",
            target,
        )
        return ()

    document: Any = yaml.safe_load(raw) or {}
    if not isinstance(document, dict):
        raise ValueError(
            "deferral_phrases.yaml must be a mapping of "
            "{language: [phrase, ...]}, got "
            f"{type(document).__name__}",
        )

    phrases: list[str] = []
    seen: set[str] = set()
    for language, entries in document.items():
        if not isinstance(entries, list):
            raise ValueError(
                f"deferral_phrases.yaml[{language!r}] must be a "
                f"list of strings, got {type(entries).__name__}",
            )
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError(
                    f"deferral_phrases.yaml[{language!r}] entries "
                    "must be strings, got "
                    f"{type(entry).__name__}: {entry!r}",
                )
            normalised = entry.strip().lower()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            phrases.append(normalised)

    return tuple(phrases)
