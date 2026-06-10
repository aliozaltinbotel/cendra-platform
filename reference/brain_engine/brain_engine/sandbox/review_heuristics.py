"""Heuristic flags for sandbox example replies.

The bootstrap pipeline calls :func:`classify_review_need` on every
LLM-generated example reply before it is persisted as an
:class:`~brain_engine.sandbox.models.UnansweredThread`.  Replies that
trip any rule are tagged with a comma-separated reason string so the
sandbox UI can colour them red — drawing the PM's attention to the
candidates most at risk of carrying hallucinated specifics.

The rules are deliberately *narrow*: they are pattern matches against
text the LLM is forbidden from producing by the system prompt
(prices, time windows, access codes, …).  False positives are
acceptable — the PM still reviews every row, the flag only changes
the order of attention.  False negatives are tolerated for the same
reason.

Adding a new rule is intentionally a one-line edit: extend
:data:`_RULES` with ``("rule_name", compiled_pattern)``.  Keep the
rule set small — every rule increases noise, and the human-in-the-loop
remains the primary defence against hallucination.
"""

from __future__ import annotations

import re
from typing import Final, Pattern

__all__ = ["classify_review_need"]


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------


# Time-of-day windows (e.g. ``check-in is at 15:00``).  STR-domain
# replies frequently invent a check-in time when no fact supports one.
_RE_TIME: Final[Pattern[str]] = re.compile(r"\b\d{1,2}:\d{2}\b")

# Currency-tagged amounts.  Captures values like ``25 EUR``, ``€25``,
# ``$30``, ``₺250``.  We do not flag bare integers because guest
# names, room numbers and review counts produce too many false
# positives.
_RE_PRICE: Final[Pattern[str]] = re.compile(
    r"(?ix)"
    r"(?:                              "
    r"   \b\d+(?:[.,]\d+)?\s*"
    r"   (?:eur|usd|tl|try|gbp|rub)\b "
    r"   |                              "
    r"   [€$£₺₽]\s*\d+(?:[.,]\d+)?     "
    r")"
)

# Sensitive grants — anything that hints at a secret we should not be
# emitting.  ``\s?`` allows ``passcode`` / ``pass code``.
_RE_SECRET: Final[Pattern[str]] = re.compile(
    r"(?i)\b("
    r"wifi\s?password|"
    r"wi-?fi\s?password|"
    r"network\s?password|"
    r"door\s?code|"
    r"door\s?password|"
    r"lockbox\s?code|"
    r"key\s?code|"
    r"access\s?code|"
    r"pin\s?code|"
    r"pass\s?code"
    r")\b",
)

# Numbered street addresses in EN / TR / RU shapes.  ``\b`` keeps the
# pattern from matching ``room 12 floor`` etc.
_RE_ADDRESS: Final[Pattern[str]] = re.compile(
    r"(?i)\b\d+\s+"
    r"(?:street|st\.?|avenue|ave\.?|road|rd\.?|"
    r"boulevard|blvd\.?|"
    r"sokak|sk\.?|cadde|cd\.?|caddesi|"
    r"улиц[аы]?|ул\.?|переулок|пер\.?|проспект|пр\.?)\b",
)


_RULES: Final[tuple[tuple[str, Pattern[str]], ...]] = (
    ("contains_time", _RE_TIME),
    ("contains_price", _RE_PRICE),
    ("contains_secret", _RE_SECRET),
    ("contains_address", _RE_ADDRESS),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_review_need(reply_text: str) -> str:
    """Return a comma-separated list of rule names triggered.

    Empty string means "nothing suspicious" — the PM can review at
    leisure.  A non-empty string is a hint to the UI that this row
    deserves the first look.

    The order of names mirrors the rule declaration order so the
    string is stable across calls and easy to assert in tests.
    """
    if not reply_text:
        return ""
    triggered: list[str] = [
        name for name, pattern in _RULES if pattern.search(reply_text)
    ]
    return ",".join(triggered)
