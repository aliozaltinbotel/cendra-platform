"""Retain blacklist — pattern catalogue for long-term redaction.

Each :class:`RetainRule` pairs a compiled regex with a mask kind
and a short label used in audit logs.  :class:`RetainBlacklist`
wraps an ordered tuple so callers can pick the default set or
compose tenant-specific overrides.

The default blacklist covers the classes of content the AI Pattern
doc and Cendra's privacy review flagged as "never commit to
long-term memory":

- Credit card numbers (Luhn-shaped digit runs)
- IBAN account numbers
- International phone numbers
- Email addresses
- Turkish national ID (11 digit) and passport-like codes
- Generic bearer / API secrets

Regexes are intentionally conservative — false negatives are
preferred to false positives that would strip the surrounding
context the engine needs to reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


__all__ = [
    "DEFAULT_RETAIN_BLACKLIST",
    "MaskKind",
    "RetainBlacklist",
    "RetainRule",
]


class MaskKind(StrEnum):
    """How a matched span is replaced in the masked output."""

    FULL = "full"
    PARTIAL = "partial"
    HASH = "hash"


@dataclass(frozen=True, slots=True)
class RetainRule:
    """A single blacklist entry.

    Attributes:
        label: Short human-readable tag written to the redaction
            report (e.g. ``"credit_card"``, ``"email"``).
        pattern: Compiled regular expression matched against the
            full message text; matches are replaced according to
            :attr:`mask`.
        mask: How the matched span is rendered in the output.
        keep_trailing: For :attr:`MaskKind.PARTIAL`, how many
            trailing characters of the original match are kept;
            ignored for ``FULL`` and ``HASH``.
    """

    label: str
    pattern: re.Pattern[str]
    mask: MaskKind = MaskKind.FULL
    keep_trailing: int = 0


@dataclass(frozen=True, slots=True)
class RetainBlacklist:
    """Ordered set of :class:`RetainRule` objects."""

    rules: tuple[RetainRule, ...] = ()

    def iter_rules(self) -> tuple[RetainRule, ...]:
        """Expose the internal tuple for iteration."""
        return self.rules


# ---------------------------------------------------------------------------
# Default pattern catalogue
# ---------------------------------------------------------------------------


_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ -]*?){13,19}\b",
)

_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
)

_PHONE_RE = re.compile(
    r"(?<!\w)\+?\d[\d\s\-().]{7,}\d(?!\w)",
)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)

# Turkish TCKN: 11 digits, first must be non-zero.  We avoid
# implementing the Luhn-style checksum so the rule stays cheap and
# predictable; the occasional false positive is acceptable for
# redaction.
_TCKN_RE = re.compile(
    r"(?<!\d)[1-9]\d{10}(?!\d)",
)

_PASSPORT_RE = re.compile(
    r"(?<![A-Z0-9])[A-Z]\d{7,9}(?![A-Z0-9])",
)

_GENERIC_SECRET_RE = re.compile(
    r"(?i)(bearer|api[_\- ]?key|secret|token)"
    r"[\"'=:\s]+"
    r"[A-Za-z0-9._\-]{12,}",
)


DEFAULT_RETAIN_BLACKLIST: Final[RetainBlacklist] = RetainBlacklist(
    rules=(
        RetainRule(
            label="credit_card",
            pattern=_CREDIT_CARD_RE,
            mask=MaskKind.PARTIAL,
            keep_trailing=4,
        ),
        RetainRule(
            label="iban",
            pattern=_IBAN_RE,
            mask=MaskKind.PARTIAL,
            keep_trailing=4,
        ),
        RetainRule(
            label="phone",
            pattern=_PHONE_RE,
            mask=MaskKind.PARTIAL,
            keep_trailing=2,
        ),
        RetainRule(
            label="email",
            pattern=_EMAIL_RE,
            mask=MaskKind.HASH,
        ),
        RetainRule(
            label="tckn",
            pattern=_TCKN_RE,
            mask=MaskKind.FULL,
        ),
        RetainRule(
            label="passport",
            pattern=_PASSPORT_RE,
            mask=MaskKind.FULL,
        ),
        RetainRule(
            label="generic_secret",
            pattern=_GENERIC_SECRET_RE,
            mask=MaskKind.FULL,
        ),
    ),
)
