"""Built-in compliance checks for :class:`ComplianceMonitor`.

Each function follows the :class:`ComplianceCheck` Protocol —
takes a :class:`ComplianceContext`, returns either ``None`` (rule
passes) or a populated :class:`ComplianceViolation`.

Five rules ship by default:

- :func:`registration_id_required` — Reg 2024/1028: every booking
  affecting action must reference the unit's authority-issued
  registration_id.
- :func:`hitl_required_for_high_risk` — EU AI Act Art. 14:
  REFUND / CANCEL / RELEASE_CODE / ESCALATE ask for explicit
  human approval.
- :func:`gdpr_art22_consent` — GDPR Art. 22: adverse decisions on
  a natural person require recorded human consent.
- :func:`jurisdiction_min_nights` — Barcelona / Amsterdam /
  Lisbon / NYC LL18: minimum-night caps prevent autonomous
  bookings shorter than the local floor.
- :func:`never_ai_action` — wraps the structurally-banned action
  set from :mod:`core.brain.compliance.never_ai_denylist`.

Tenants extend the registry via PR — every rule has a stable
``rule_id`` so the regulator can reproduce the same evaluation
on their side.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from core.brain.compliance.monitor import (
    ComplianceContext,
    ComplianceSeverity,
    ComplianceViolation,
)
from core.brain.compliance.never_ai_denylist import (
    NeverAICategory,
    is_never_ai,
    reason_for,
)

__all__ = [
    "DEFAULT_BUILTIN_CHECKS",
    "REG_2024_1028_BOOKING_KINDS",
    "gdpr_art22_consent",
    "hitl_required_for_high_risk",
    "jurisdiction_min_nights",
    "never_ai_action",
    "registration_id_required",
]


REG_2024_1028_BOOKING_KINDS: Final[frozenset[str]] = frozenset(
    {
        "confirm_booking",
        "cancel_booking",
        "block_date",
        "release_code",
    }
)


_HITL_REQUIRED_KINDS: Final[frozenset[str]] = frozenset(
    {
        "issue_refund",
        "cancel_booking",
        "release_code",
        "escalate",
    }
)


_JURISDICTION_MIN_NIGHTS: Final[Mapping[str, int]] = {
    "BCN": 31,
    "AMS": 30,
    "AMS_CENTER": 15,
    "NYC": 30,
    "LIS": 30,
}


def registration_id_required(
    context: ComplianceContext,
) -> ComplianceViolation | None:
    """Reg 2024/1028: booking actions require a registration_id."""
    if context.action_kind not in REG_2024_1028_BOOKING_KINDS:
        return None
    if context.registration_id:
        return None
    return ComplianceViolation(
        rule_id="reg_2024_1028.registration_id_required",
        severity=ComplianceSeverity.BLOCK,
        reason=(
            "Reg (EU) 2024/1028 requires a per-unit "
            "registration_id on every booking-affecting action; "
            "context carries none."
        ),
        evidence={
            "action_kind": context.action_kind,
            "property_id": context.property_id,
        },
    )


def hitl_required_for_high_risk(
    context: ComplianceContext,
) -> ComplianceViolation | None:
    """EU AI Act Art. 14: high-impact actions require HITL approval."""
    if context.action_kind not in _HITL_REQUIRED_KINDS:
        return None
    if context.has_human_consent:
        return None
    return ComplianceViolation(
        rule_id="eu_ai_act.art14.hitl_required",
        severity=ComplianceSeverity.REVIEW,
        reason=("EU AI Act Art. 14 requires explicit human oversight before this action class is executed."),
        evidence={"action_kind": context.action_kind},
    )


def gdpr_art22_consent(
    context: ComplianceContext,
) -> ComplianceViolation | None:
    """GDPR Art. 22: adverse decisions on natural persons need consent."""
    if not context.is_natural_person_decision:
        return None
    if context.has_human_consent:
        return None
    return ComplianceViolation(
        rule_id="gdpr.art22.adverse_decision_consent",
        severity=ComplianceSeverity.BLOCK,
        reason=(
            "GDPR Art. 22 forbids fully autonomous adverse "
            "decisions against a natural person without recorded "
            "human consent."
        ),
        evidence={
            "action_kind": context.action_kind,
            "owner_id": context.owner_id,
        },
    )


def jurisdiction_min_nights(
    context: ComplianceContext,
) -> ComplianceViolation | None:
    """Per-jurisdiction minimum-night caps."""
    if context.action_kind != "confirm_booking":
        return None
    juris = (context.jurisdiction or "").upper()
    floor = _JURISDICTION_MIN_NIGHTS.get(juris)
    if floor is None:
        return None
    nights = len(context.booking_dates)
    if nights >= floor:
        return None
    return ComplianceViolation(
        rule_id=f"jurisdiction.{juris.lower()}.min_nights",
        severity=ComplianceSeverity.BLOCK,
        reason=(f"jurisdiction {juris} requires at least {floor} nights; booking covers {nights}."),
        evidence={
            "jurisdiction": juris,
            "floor": str(floor),
            "nights": str(nights),
        },
    )


def never_ai_action(
    context: ComplianceContext,
) -> ComplianceViolation | None:
    """Refuse any structurally-banned action category."""
    requested = context.extra.get("never_ai_category")
    if requested is None:
        return None
    if not is_never_ai(requested):
        return None
    category = NeverAICategory(requested)
    return ComplianceViolation(
        rule_id=f"never_ai.{category.value}",
        severity=ComplianceSeverity.BLOCK,
        reason=reason_for(category),
        evidence={"category": category.value},
    )


DEFAULT_BUILTIN_CHECKS: Final[tuple] = (
    registration_id_required,
    hitl_required_for_high_risk,
    gdpr_art22_consent,
    jurisdiction_min_nights,
    never_ai_action,
)
