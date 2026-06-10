"""Redactor — applies a :class:`RetainBlacklist` to free text.

Produces the masked output plus an ordered list of :class:`RedactionHit`
entries so callers can log *what* was redacted without persisting the
original content.  The redactor is pure-Python and stateless apart
from its injected blacklist; call sites may cache a single instance
per process.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from brain_engine.retention.blacklist import (
    DEFAULT_RETAIN_BLACKLIST,
    MaskKind,
    RetainBlacklist,
    RetainRule,
)


__all__ = [
    "RedactionHit",
    "RedactionResult",
    "Redactor",
]


_FULL_MASK: Final[str] = "[REDACTED]"
_HASH_PREFIX: Final[str] = "[HASH:"
_HASH_SUFFIX: Final[str] = "]"
_HASH_LENGTH: Final[int] = 8


@dataclass(frozen=True, slots=True)
class RedactionHit:
    """One match replaced during redaction.

    Attributes:
        label: The matching rule's label.
        start: Start offset in the *original* text.
        end: End offset in the *original* text.
        mask: How the span was rendered in the output.
    """

    label: str
    start: int
    end: int
    mask: MaskKind


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Output of :meth:`Redactor.redact`.

    Attributes:
        original_length: Character count of the input (for telemetry).
        redacted: The masked text safe to persist long-term.
        hits: Ordered list of :class:`RedactionHit` records; empty
            tuple when the text was clean.
    """

    original_length: int
    redacted: str
    hits: tuple[RedactionHit, ...] = ()

    @property
    def had_redactions(self) -> bool:
        """Whether at least one rule matched."""
        return bool(self.hits)


class Redactor:
    """Apply a :class:`RetainBlacklist` to free text.

    The redactor walks each rule in blacklist order.  Overlapping
    matches are resolved in rule order: the first match wins and
    later rules operate on the already-masked text, so a credit
    card number cannot slip through as a "phone" match.
    """

    def __init__(
        self,
        blacklist: RetainBlacklist = DEFAULT_RETAIN_BLACKLIST,
    ) -> None:
        self._blacklist = blacklist

    def redact(self, text: str) -> RedactionResult:
        """Return the masked text + report for ``text``."""
        if not text:
            return RedactionResult(
                original_length=0,
                redacted=text,
                hits=(),
            )
        working = text
        hits: list[RedactionHit] = []
        for rule in self._blacklist.iter_rules():
            working, rule_hits = self._apply_rule(working, rule)
            hits.extend(rule_hits)
        return RedactionResult(
            original_length=len(text),
            redacted=working,
            hits=tuple(hits),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_rule(
        self,
        text: str,
        rule: RetainRule,
    ) -> tuple[str, list[RedactionHit]]:
        """Apply a single rule and return (new_text, hits)."""
        hits: list[RedactionHit] = []
        pieces: list[str] = []
        cursor = 0
        for match in rule.pattern.finditer(text):
            start, end = match.span()
            pieces.append(text[cursor:start])
            replacement = _render_mask(match.group(0), rule)
            pieces.append(replacement)
            hits.append(
                RedactionHit(
                    label=rule.label,
                    start=start,
                    end=end,
                    mask=rule.mask,
                ),
            )
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces), hits


def _render_mask(value: str, rule: RetainRule) -> str:
    """Render the replacement string for a matched span."""
    if rule.mask is MaskKind.FULL:
        return _FULL_MASK
    if rule.mask is MaskKind.HASH:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"{_HASH_PREFIX}{digest[:_HASH_LENGTH]}{_HASH_SUFFIX}"
    # PARTIAL — keep the last ``keep_trailing`` characters.
    keep = max(0, rule.keep_trailing)
    if keep <= 0 or len(value) <= keep:
        return _FULL_MASK
    return f"{_FULL_MASK[:-1]}:{value[-keep:]}]"
