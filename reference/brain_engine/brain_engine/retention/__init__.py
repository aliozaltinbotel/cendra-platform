"""Retention policy — what Brain Engine must never store long-term.

Public surface:

- :class:`MaskKind` — how a redacted span should be rendered.
- :class:`RetainRule` — a single pattern + mask entry.
- :class:`RetainBlacklist` — ordered rule set with lookup helpers.
- :class:`Redactor` — runtime component that applies the blacklist
  to free text and returns both the masked result and the list of
  spans that were masked.
- :class:`RedactionHit` / :class:`RedactionResult` — report types.
- :data:`DEFAULT_RETAIN_BLACKLIST` — common PII / secret patterns
  the engine should always strip (credit cards, IBANs, phone
  numbers, email addresses, Turkish national ID, generic secrets).
"""

from __future__ import annotations

from brain_engine.retention.blacklist import (
    DEFAULT_RETAIN_BLACKLIST,
    MaskKind,
    RetainBlacklist,
    RetainRule,
)
from brain_engine.retention.redactor import (
    RedactionHit,
    RedactionResult,
    Redactor,
)

__all__ = [
    "DEFAULT_RETAIN_BLACKLIST",
    "MaskKind",
    "RedactionHit",
    "RedactionResult",
    "Redactor",
    "RetainBlacklist",
    "RetainRule",
]
