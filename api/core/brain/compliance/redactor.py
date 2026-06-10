"""Redaction strategies for PII spans.

Three strategies cover the production needs:

* ``MASK`` — replace the value with the same-length string of ``*``
  while preserving the leading and trailing character (so logs stay
  human-readable for debugging — ``"a***@b.com"``).
* ``HASH`` — replace the value with a stable blake2b digest, useful
  when downstream needs equality semantics ("same email twice")
  without storing the plaintext.
* ``DROP`` — remove the value entirely.  Used when even the shape
  of the identifier is sensitive (e.g. passport length leaks
  nationality).

The ``redact`` function is pure: same input → same output, no I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from enum import StrEnum

from core.brain.compliance.pii_detector import PIIMatch


class RedactionStrategy(StrEnum):
    """How to replace a PII span in the output text."""

    MASK = "mask"
    HASH = "hash"
    DROP = "drop"


def redact(
    text: str,
    matches: Iterable[PIIMatch],
    strategy: RedactionStrategy = RedactionStrategy.MASK,
    *,
    hash_secret: bytes = b"",
) -> str:
    """Return ``text`` with each match in ``matches`` replaced.

    Matches must be non-overlapping; the detector guarantees this via
    its own dedupe pass.  We still iterate in reverse-start order so
    the offsets remain valid after each substitution.

    ``hash_secret`` is mixed into the blake2b key when
    ``strategy == HASH`` — pass a per-tenant secret to prevent
    rainbow-table attacks across tenants.
    """
    ordered = sorted(matches, key=lambda m: m.start, reverse=True)
    out = text
    for match in ordered:
        replacement = _replacement_for(match, strategy, hash_secret)
        out = out[: match.start] + replacement + out[match.end :]
    return out


def _replacement_for(
    match: PIIMatch,
    strategy: RedactionStrategy,
    hash_secret: bytes,
) -> str:
    if strategy is RedactionStrategy.DROP:
        return ""
    if strategy is RedactionStrategy.HASH:
        digest = hashlib.blake2b(
            match.value.encode("utf-8"),
            key=hash_secret,
            digest_size=8,
        ).hexdigest()
        return f"<{match.pii_type.value}:{digest}>"
    # MASK — preserve length, keep the first and last char.
    length = max(match.end - match.start, 1)
    if length <= 2:
        return "*" * length
    return f"{match.value[0]}{'*' * (length - 2)}{match.value[-1]}"
