"""Status-aware redaction of sensitive lines in property knowledge.

Closes Sandbox UI test C1 (2026-05-19): with reservation status
``Inquiry``, the agent shared the WiFi password (``1234``) that
a property manager had previously entered through the PM
correction path.  The correction wrote to ``pm_facts`` as
"MANAGER-CONFIRMED KNOWLEDGE (authoritative — prefer over generic
defaults)" and appeared in the system prompt **before** the
``Operational Policies`` block that forbade WiFi disclosure in
pre-booking.  LLM picked the first authoritative signal and
leaked the password.

Strengthening the policy text alone is fragile — the LLM can
always rationalise around it.  The structural fix is to remove
the sensitive lines from ``property_knowledge`` outright when
the reservation status is pre-booking.  The agent cannot leak
what it does not see.

This module exposes a single pure function:

    redact_sensitive_for_status(text, status) -> str

The function:

* Returns ``text`` unchanged when ``status`` is post-booking
  (``confirmed``, ``in_house``, ``currently_hosting`` …) or
  empty — the model is free to quote WiFi / door code on those
  paths.
* Splits ``text`` into lines and replaces each line that matches
  a sensitive-keyword pattern with
  ``<Label>: [REDACTED — share only after booking confirmation]``.
* Returns the joined result so the caller can drop it back into
  the prompt assembly verbatim.

The pattern list is intentionally narrow.  False positives (a
line incidentally containing ``"wifi password"`` in a generic
sentence) are worse than the rare leak slipping through, so we
only redact lines that look like a fact emission — a label
followed by a colon or equals sign.
"""

from __future__ import annotations

import re
from re import Pattern

__all__ = [
    "PRE_BOOKING_STATUSES",
    "REDACTION_MARKER",
    "SENSITIVE_LINE_PATTERNS",
    "redact_sensitive_for_status",
]


# Statuses where the SECURITY clause applies.  Sourced from
# ``operational_policies.py`` "Status: inquiry preapproved
# (channel exceptions)" plus plain ``"inquiry"`` added in R1.
# Matching is case-insensitive so the raw PMS label
# ("Inquiry", "InquiryPreapproved") works without lowercasing.
PRE_BOOKING_STATUSES: frozenset[str] = frozenset(
    {
        "inquiry",
        "follow_up",
        "inquirypreapproved",
        "inquirynotpossible",
        # ``expired`` joins the set for the same SECURITY rationale:
        # the booking is no longer active so sensitive details
        # (WiFi password / door / lock / GPS) must not surface in
        # the prompt.  A separate prompt block (R12) handles the
        # "do not offer modifications" instruction; this entry
        # just keeps the sensitive-fields layer consistent.
        "expired",
    }
)


# What the LLM sees in place of a redacted value.  Includes a
# short justification so the model knows to defer rather than
# treat the line as a missing field.
REDACTION_MARKER: str = (
    "[REDACTED — share only after booking confirmation]"
)


# Patterns matched against each line (case-insensitive).  Each
# tuple is (regex, human label used in the replacement).  Order
# matters: more specific patterns first so a line carrying both
# "wifi password" and "door code" gets the WiFi label.
SENSITIVE_LINE_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    (re.compile(r"wi-?fi\s+password", re.IGNORECASE), "WiFi password"),
    (re.compile(r"wi-?fi\s+(şifre|sifre)", re.IGNORECASE), "WiFi password"),
    (re.compile(r"lock\s*box\s+code", re.IGNORECASE), "Lockbox code"),
    (
        re.compile(
            r"building\s+(entry|entrance|door)\s+code",
            re.IGNORECASE,
        ),
        "Building entry code",
    ),
    (re.compile(r"door\s+code", re.IGNORECASE), "Door code"),
    (re.compile(r"safe\s+code", re.IGNORECASE), "Safe code"),
    (re.compile(r"gps\s+coord", re.IGNORECASE), "GPS coordinates"),
    (
        re.compile(r"\bexact\s+address\b", re.IGNORECASE),
        "Exact address",
    ),
)


def _is_fact_line(line: str) -> bool:
    """Return ``True`` when ``line`` looks like a key/value fact.

    Used to skip lines that mention a sensitive keyword in a
    generic sentence (e.g. "we will share the WiFi password
    after booking") rather than a concrete value emission
    ("WiFi password: 1234").  The fact shapes the property
    profile / PM facts pipeline emits are:

    * ``"- WiFi password: 1234"`` (PM facts bullet)
    * ``"WiFi password: 1234"`` (raw line)
    * ``"WiFi password = 1234"``
    * ``"WiFi password is 1234"``
    """
    stripped = line.strip()
    if not stripped:
        return False
    # Bullet form
    if stripped.startswith(("-", "•", "*")):
        stripped = stripped[1:].strip()
    return (
        ":" in stripped
        or "=" in stripped
        or re.search(r"\b(is|=>|→)\b", stripped, re.IGNORECASE) is not None
    )


def _normalize_status(status: str) -> str:
    """Lowercase + strip the PMS label so the lookup is forgiving."""
    return (status or "").strip().lower()


def is_pre_booking_status(status: str) -> bool:
    """Whether ``status`` is in the pre-booking set.

    Public helper exposed for downstream code that wants to gate
    other status-driven behaviour off the same set.
    """
    return _normalize_status(status) in PRE_BOOKING_STATUSES


def redact_sensitive_for_status(text: str, status: str) -> str:
    """Redact sensitive-value lines from ``text`` when in pre-booking.

    Args:
        text: Property-knowledge block built by
            ``_format_profile_knowledge`` + ``_append_pm_facts``.
            Multiline string; may be empty.
        status: Reservation status from the request.  Raw PMS
            label is fine — normalised internally.

    Returns:
        ``text`` unchanged when the status is post-booking or
        empty.  Otherwise the same text with sensitive fact lines
        replaced by ``<Label>: <REDACTION_MARKER>``.  Other lines
        are left untouched so the LLM still sees the rest of the
        property knowledge.
    """
    if not text or not is_pre_booking_status(status):
        return text

    lines: list[str] = text.split("\n")
    out: list[str] = []
    for line in lines:
        if not _is_fact_line(line):
            out.append(line)
            continue
        replaced: str | None = None
        for pattern, label in SENSITIVE_LINE_PATTERNS:
            if pattern.search(line):
                # Preserve the original leading whitespace + bullet
                # prefix so the surrounding Markdown layout stays
                # stable and the redacted line still parses as part
                # of the same list.
                prefix_match = re.match(r"^(\s*[-•*]?\s*)", line)
                prefix = prefix_match.group(1) if prefix_match else ""
                replaced = f"{prefix}{label}: {REDACTION_MARKER}"
                break
        out.append(replaced if replaced is not None else line)
    return "\n".join(out)
