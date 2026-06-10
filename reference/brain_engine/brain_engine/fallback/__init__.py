"""Fallback & Gap Detection — Handles missing data and escalation scenarios.

When the system lacks information (no cleaner configured, no vendor contact,
no manager phone), these components detect the gap, propose alternatives,
and escalate to the property manager or owner.

Key scenario: All cleaners busy → try third cleaner → call manager → escalate to owner.
"""

from brain_engine.fallback.config_validator import ConfigValidator, ValidationResult
from brain_engine.fallback.gap_resolver import GapResolver, GapType
from brain_engine.fallback.fallback_chain import FallbackChain, FallbackStep

__all__ = [
    "ConfigValidator",
    "ValidationResult",
    "GapResolver",
    "GapType",
    "FallbackChain",
    "FallbackStep",
]
