"""Canonical context-tag vocabulary for decision cards.

The V2 mobile wireframes (see ``v2 wireframe/``) tile the
property-feed with single-tag chips that summarise *why* a card was
surfaced.  This module turns those chip labels into a typed enum so
the engine can emit them programmatically and the UI can map them to
a stable colour/icon set.

Sixteen tags mirror the wireframe screens in source order; each tag
ships with a one-line description used by the V2 onboarding tour and
the OpenAPI examples.

Adding a tag is a backwards-compatible change *only* when the new
value is appended to the enum.  Renaming or removing a tag is a wire
break — bump the API version first.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


__all__ = [
    "CONTEXT_TAG_DESCRIPTIONS",
    "ContextTag",
    "describe_context_tag",
]


class ContextTag(StrEnum):
    """Sixteen canonical decision-card context labels.

    The values mirror the chip text in the V2 wireframe so the UI
    layer can keep a one-to-one stylesheet keyed by the enum value.
    """

    # ── Knowledge / memory state ──────────────────────────────── #
    MEMORY_CONFIRMED = "memory_confirmed"
    PATTERN_LEARNED = "pattern_learned"

    # ── Holding states ────────────────────────────────────────── #
    BLOCKER_HOLDING = "blocker_holding"
    DOCUMENT_MISSING = "document_missing"
    CONFIRMATION_AWAITED = "confirmation_awaited"

    # ── Risk / compliance ─────────────────────────────────────── #
    POLICY_VIOLATION = "policy_violation"
    COMPLIANCE_ALERT = "compliance_alert"
    GUEST_RISK_FLAG = "guest_risk_flag"

    # ── Calendar / financial signals ──────────────────────────── #
    CALENDAR_CONFLICT = "calendar_conflict"
    PAYMENT_PENDING = "payment_pending"

    # ── Coordination ──────────────────────────────────────────── #
    VENDOR_NEEDED = "vendor_needed"
    HANDOFF_REQUESTED = "handoff_requested"
    ESCALATION_REQUIRED = "escalation_required"

    # ── Opportunity / autonomy offers ─────────────────────────── #
    OPPORTUNITY_SPOTTED = "opportunity_spotted"
    COUNTER_OFFER = "counter_offer"
    AUTOPILOT_OFFER = "autopilot_offer"


CONTEXT_TAG_DESCRIPTIONS: Final[dict[ContextTag, str]] = {
    ContextTag.MEMORY_CONFIRMED: (
        "Engine matched a stored PM answer or learned pattern; the "
        "card just confirms the outcome."
    ),
    ContextTag.PATTERN_LEARNED: (
        "A new PatternRule was promoted from recent decision cases; "
        "the card reports what changed."
    ),
    ContextTag.BLOCKER_HOLDING: (
        "Action is held by an active blocker until preconditions "
        "are satisfied."
    ),
    ContextTag.DOCUMENT_MISSING: (
        "A required document (passport, lease, vendor invoice) has "
        "not arrived yet."
    ),
    ContextTag.CONFIRMATION_AWAITED: (
        "Awaiting an explicit acknowledgement from the PM before "
        "the engine proceeds."
    ),
    ContextTag.POLICY_VIOLATION: (
        "Recommended action conflicts with a documented PM policy."
    ),
    ContextTag.COMPLIANCE_ALERT: (
        "Legal, safety, or platform-policy concern that must be "
        "resolved before continuing."
    ),
    ContextTag.GUEST_RISK_FLAG: (
        "Guest profile triggered a trust/risk heuristic; review "
        "before extending privileges."
    ),
    ContextTag.CALENDAR_CONFLICT: (
        "Reservation overlaps an existing booking, hold, or owner "
        "block on the calendar."
    ),
    ContextTag.PAYMENT_PENDING: (
        "Financial action (deposit, refund, claim) is staged but "
        "not yet executed."
    ),
    ContextTag.VENDOR_NEEDED: (
        "Workflow requires dispatching an external party "
        "(cleaner, plumber, locksmith)."
    ),
    ContextTag.HANDOFF_REQUESTED: (
        "Another teammate has been mentioned to take over the "
        "thread."
    ),
    ContextTag.ESCALATION_REQUIRED: (
        "24/7 escalation tier triggered; the card surfaces the "
        "current responder."
    ),
    ContextTag.OPPORTUNITY_SPOTTED: (
        "Engine spotted a proactive opportunity (upsell, transfer, "
        "early checkout) the PM has not asked for."
    ),
    ContextTag.COUNTER_OFFER: (
        "Guest sent a counter-offer; the card recommends an "
        "accept/reject/counter response."
    ),
    ContextTag.AUTOPILOT_OFFER: (
        "Workflow has earned enough confidence to be promoted to "
        "autopilot; the card asks the PM to opt in."
    ),
}


def describe_context_tag(tag: ContextTag) -> str:
    """Return the canonical one-line description for ``tag``."""
    return CONTEXT_TAG_DESCRIPTIONS[tag]
