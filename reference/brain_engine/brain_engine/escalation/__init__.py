"""Cendra V2 24/7 escalation tiers.

Public surface:

- :class:`EscalationTier` — five canonical tier values used by the
  decision engine and the V2 UI escalation chip.
- :class:`EscalationLevel` — one tier definition (target role,
  response window, fallback tier).
- :class:`EscalationPolicy` — ordered tier list with lookup helpers.
- :class:`EscalationDecision` — dispatcher output (resolved tier +
  member chain).
- :class:`EscalationDispatcher` — picks the right tier given the
  situation severity and the current time.
- :data:`DEFAULT_ESCALATION_POLICY` — out-of-the-box policy that
  matches the wireframe defaults.
"""

from __future__ import annotations

from brain_engine.escalation.dispatcher import (
    EscalationDecision,
    EscalationDispatcher,
)
from brain_engine.escalation.models import (
    DEFAULT_ESCALATION_POLICY,
    EscalationLevel,
    EscalationPolicy,
    EscalationTier,
)

__all__ = [
    "DEFAULT_ESCALATION_POLICY",
    "EscalationDecision",
    "EscalationDispatcher",
    "EscalationLevel",
    "EscalationPolicy",
    "EscalationTier",
]
