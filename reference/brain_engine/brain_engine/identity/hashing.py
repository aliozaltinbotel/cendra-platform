"""GDPR-safe normalisation + HMAC hashing of guest handles.

Cross-channel identity reconciliation must never persist raw PII on
the join surface — every match is keyed off the *hash* of a
normalised email or phone number, not the raw value.  This module
ships the two primitives:

- :func:`normalize_email` / :func:`normalize_phone` — canonical
  form so two spellings of the same handle hash to the same value.
- :func:`hmac_handle` — HMAC-SHA256 over the normalised form with
  a caller-supplied 32-byte secret key.

The HMAC key is the only secret the matching layer needs; rotating
it forces a full re-build of the identity graph (which is a
deliberate property — the regulator can ask for proof that the
keys were rotated and the join surface was rehydrated).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Final


__all__ = [
    "MIN_HMAC_KEY_BYTES",
    "hmac_handle",
    "normalize_email",
    "normalize_phone",
]


MIN_HMAC_KEY_BYTES: Final[int] = 32


_PHONE_NON_DIGIT = re.compile(r"[^\d+]")


def normalize_email(value: str) -> str:
    """Return the canonical form of an email address.

    Rules:
        - strip surrounding whitespace
        - lower-case
        - require exactly one ``@``

    Raises :class:`ValueError` when the input does not look like an
    email — fail-fast so callers don't accidentally hash garbage.
    """
    candidate = value.strip().lower()
    if candidate.count("@") != 1:
        raise ValueError(
            f"expected one '@' in email, got {value!r}"
        )
    local, domain = candidate.split("@", 1)
    if not local or not domain or "." not in domain:
        raise ValueError(
            f"expected non-empty local + domain, got {value!r}"
        )
    return f"{local}@{domain}"


def normalize_phone(value: str) -> str:
    """Return the canonical E.164-leaning form of a phone number.

    Strips every character except digits and a leading ``+``.
    Validates that at least seven digits remain — anything shorter
    is more likely a typo than a real number.
    """
    raw = value.strip()
    candidate = _PHONE_NON_DIGIT.sub("", raw)
    digits = candidate.replace("+", "")
    if len(digits) < 7:
        raise ValueError(
            f"expected at least 7 digits, got {value!r}"
        )
    return candidate


def hmac_handle(*, key: bytes, normalised: str) -> str:
    """Return the hex-encoded HMAC-SHA256 of ``normalised``."""
    if len(key) < MIN_HMAC_KEY_BYTES:
        raise ValueError(
            f"key must be at least {MIN_HMAC_KEY_BYTES} bytes; "
            f"got {len(key)}"
        )
    if not normalised:
        raise ValueError("normalised must be non-empty")
    return hmac.new(
        key, normalised.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
