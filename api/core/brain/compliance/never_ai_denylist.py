"""Structurally-enforced never-AI denylist (Moat #5).

A small, hand-curated set of action / decision categories Brain
Engine refuses to perform autonomously *under any owner-policy
override*.  Unlike the per-style :class:`PlannerStyleSpec`
denylist (which the owner-policy DSL of Moat #2 can extend), the
never-AI denylist is a structural floor: the runtime middleware
checks it *before* any other gate, and a hit raises a hard
:class:`StructuralDenyError`.

The four built-in categories cover the EU AI Act + GDPR + civil-
rights baseline that no STR-PM tenant should be allowed to opt
out of:

- :attr:`NeverAICategory.SCREEN_BY_PROTECTED_CLASS` — any
  decision that filters guests by a protected class (race,
  religion, disability, etc.).  Discrimination floor.
- :attr:`NeverAICategory.GDPR_ART22_AUTONOMOUS_DENY` — fully
  autonomous adverse decision against a natural person without
  HITL.  GDPR Art. 22 (CJEU SCHUFA Dec 2023).
- :attr:`NeverAICategory.LEGAL_RESPONSE_NO_HUMAN` — autonomous
  legal advice or response to a legal claim.
- :attr:`NeverAICategory.MEDICAL_ADVICE` — autonomous medical
  triage or advice.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

__all__ = [
    "NEVER_AI_REASONS",
    "NeverAICategory",
    "StructuralDenyError",
    "is_never_ai",
    "reason_for",
]


class NeverAICategory(StrEnum):
    """Structurally-blocked decision categories."""

    SCREEN_BY_PROTECTED_CLASS = "screen_by_protected_class"
    GDPR_ART22_AUTONOMOUS_DENY = "gdpr_art22_autonomous_deny"
    LEGAL_RESPONSE_NO_HUMAN = "legal_response_no_human"
    MEDICAL_ADVICE = "medical_advice"


NEVER_AI_REASONS: Final[Mapping[NeverAICategory, str]] = {
    NeverAICategory.SCREEN_BY_PROTECTED_CLASS: (
        "Filtering guests by a protected class (race, religion, "
        "disability, etc.) is forbidden under EU + national civil-"
        "rights law and never executed autonomously."
    ),
    NeverAICategory.GDPR_ART22_AUTONOMOUS_DENY: (
        "Fully autonomous adverse decisions against a natural "
        "person are forbidden under GDPR Art. 22 (CJEU SCHUFA, "
        "Dec 2023).  Such decisions require a human-in-the-loop."
    ),
    NeverAICategory.LEGAL_RESPONSE_NO_HUMAN: (
        "Autonomous responses to legal claims, dispute filings, "
        "or compliance enforcement notices are forbidden — every "
        "such response is escalated to a qualified human."
    ),
    NeverAICategory.MEDICAL_ADVICE: (
        "Brain Engine never gives autonomous medical advice or "
        "triage; medical concerns are escalated to qualified "
        "humans."
    ),
}


class StructuralDenyError(RuntimeError):
    """Raised when a structural-denylist category is requested.

    The error name and message are deliberately descriptive so the
    audit log records the exact reason for the deny.
    """

    def __init__(self, category: NeverAICategory) -> None:
        message = f"structural deny: {category.value} — {reason_for(category)}"
        super().__init__(message)
        self.category = category


def is_never_ai(category: NeverAICategory | str) -> bool:
    """Return ``True`` when ``category`` is a structural-deny."""
    if isinstance(category, NeverAICategory):
        return True
    try:
        NeverAICategory(category)
    except ValueError:
        return False
    return True


def reason_for(category: NeverAICategory) -> str:
    """Return the audit-log rationale for ``category``."""
    return NEVER_AI_REASONS[category]
