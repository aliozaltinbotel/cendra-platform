"""Canonical action vocabulary for decision-card prepared actions.

The :class:`PreparedAction` value object accepts a free-form
``action_type`` string so the engine can grow without churn — but
the V2 UI relies on a stable vocabulary to render the right Confirm
button copy, the right Undo affordance, and to route the action
through the correct downstream pipeline.

This module declares the sixteen canonical action kinds drawn from
the wireframes and the AI Pattern doc, plus a default reversibility
hint per kind so the builder can pick a sensible Undo tier when no
explicit value is provided.

The reversibility hint mirrors :class:`ReversibilityTier`:

- ``GREEN``: fully reversible within 60 s (e.g. logging only,
  outbound message before guest read).
- ``AMBER``: reversible via compensating action within 10 min
  (e.g. hold release, vendor reschedule).
- ``RED``: effectively irreversible; audit log only (e.g. refund
  issued, deposit captured, code rotated).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from brain_engine.cards.models import ReversibilityTier


__all__ = [
    "ACTION_KIND_DESCRIPTIONS",
    "ACTION_KIND_REVERSIBILITY",
    "CardActionKind",
    "default_reversibility",
    "describe_action_kind",
]


class CardActionKind(StrEnum):
    """Sixteen canonical action types a decision card may carry."""

    # ── Communication ─────────────────────────────────────────── #
    SEND_MESSAGE = "send_message"
    REQUEST_DOCUMENT = "request_document"

    # ── Booking lifecycle ─────────────────────────────────────── #
    CONFIRM_BOOKING = "confirm_booking"
    CANCEL_BOOKING = "cancel_booking"
    HOLD_FOR_REVIEW = "hold_for_review"
    BLOCK_DATE = "block_date"

    # ── Pricing / negotiation ─────────────────────────────────── #
    APPLY_DISCOUNT = "apply_discount"
    COUNTER_OFFER = "counter_offer"

    # ── Financial ─────────────────────────────────────────────── #
    CHARGE_FEE = "charge_fee"
    ISSUE_REFUND = "issue_refund"

    # ── Operations ────────────────────────────────────────────── #
    DISPATCH_VENDOR = "dispatch_vendor"
    RELEASE_CODE = "release_code"

    # ── Coordination ──────────────────────────────────────────── #
    ESCALATE = "escalate"
    HANDOFF_TO_TEAMMATE = "handoff_to_teammate"

    # ── Bookkeeping ───────────────────────────────────────────── #
    MARK_RESOLVED = "mark_resolved"
    LOG_DECISION = "log_decision"


ACTION_KIND_DESCRIPTIONS: Final[dict[CardActionKind, str]] = {
    CardActionKind.SEND_MESSAGE: (
        "Send a message to the guest, owner, or vendor through the "
        "channel already attached to the conversation."
    ),
    CardActionKind.REQUEST_DOCUMENT: (
        "Ask the counterparty for a missing document (passport, "
        "lease, vendor invoice, deposit confirmation)."
    ),
    CardActionKind.CONFIRM_BOOKING: (
        "Move a reservation from pending review to confirmed and "
        "release the standard pre-arrival sequence."
    ),
    CardActionKind.CANCEL_BOOKING: (
        "Cancel a reservation and trigger the configured refund / "
        "calendar-release behaviour."
    ),
    CardActionKind.HOLD_FOR_REVIEW: (
        "Park the workflow and require explicit PM action before "
        "anything downstream proceeds."
    ),
    CardActionKind.BLOCK_DATE: (
        "Add a calendar block (owner stay, maintenance window) so "
        "future inquiries skip the conflicting nights."
    ),
    CardActionKind.APPLY_DISCOUNT: (
        "Quote a discount within the configured floor; the payload "
        "carries the percent or absolute amount."
    ),
    CardActionKind.COUNTER_OFFER: (
        "Send a counter-offer in a price negotiation, optionally "
        "with conditions (longer stay, cleaning fee waived)."
    ),
    CardActionKind.CHARGE_FEE: (
        "Charge an extra-person, late-checkout, or damage fee via "
        "the configured payment provider."
    ),
    CardActionKind.ISSUE_REFUND: (
        "Issue a partial or full refund through the booking "
        "channel's refund API."
    ),
    CardActionKind.DISPATCH_VENDOR: (
        "Open a vendor task (cleaning, plumbing, locksmith) and "
        "track it through the ops queue."
    ),
    CardActionKind.RELEASE_CODE: (
        "Send the door / lockbox / Wi-Fi code to the guest now "
        "that preconditions are satisfied."
    ),
    CardActionKind.ESCALATE: (
        "Route the situation up the 24/7 escalation tier; the "
        "payload carries the target tier."
    ),
    CardActionKind.HANDOFF_TO_TEAMMATE: (
        "Transfer the conversation to a named teammate and notify "
        "them with the current context."
    ),
    CardActionKind.MARK_RESOLVED: (
        "Close out the decision card without an external action — "
        "used when the situation resolved itself."
    ),
    CardActionKind.LOG_DECISION: (
        "Audit-only entry; the engine records the decision but "
        "performs no outbound action."
    ),
}


ACTION_KIND_REVERSIBILITY: Final[dict[CardActionKind, ReversibilityTier]] = {
    CardActionKind.SEND_MESSAGE: ReversibilityTier.AMBER,
    CardActionKind.REQUEST_DOCUMENT: ReversibilityTier.GREEN,
    CardActionKind.CONFIRM_BOOKING: ReversibilityTier.AMBER,
    CardActionKind.CANCEL_BOOKING: ReversibilityTier.RED,
    CardActionKind.HOLD_FOR_REVIEW: ReversibilityTier.GREEN,
    CardActionKind.BLOCK_DATE: ReversibilityTier.GREEN,
    CardActionKind.APPLY_DISCOUNT: ReversibilityTier.AMBER,
    CardActionKind.COUNTER_OFFER: ReversibilityTier.AMBER,
    CardActionKind.CHARGE_FEE: ReversibilityTier.RED,
    CardActionKind.ISSUE_REFUND: ReversibilityTier.RED,
    CardActionKind.DISPATCH_VENDOR: ReversibilityTier.AMBER,
    CardActionKind.RELEASE_CODE: ReversibilityTier.RED,
    CardActionKind.ESCALATE: ReversibilityTier.AMBER,
    CardActionKind.HANDOFF_TO_TEAMMATE: ReversibilityTier.GREEN,
    CardActionKind.MARK_RESOLVED: ReversibilityTier.GREEN,
    CardActionKind.LOG_DECISION: ReversibilityTier.GREEN,
}


def describe_action_kind(kind: CardActionKind) -> str:
    """Return the canonical one-line description for ``kind``."""
    return ACTION_KIND_DESCRIPTIONS[kind]


def default_reversibility(kind: CardActionKind) -> ReversibilityTier:
    """Return the recommended :class:`ReversibilityTier` for ``kind``."""
    return ACTION_KIND_REVERSIBILITY[kind]
