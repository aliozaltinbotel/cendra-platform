"""Canonical workflow taxonomy for per-workflow autonomy gating.

The eleven decision-card types in the V2 wireframes (MISMATCH, CODE,
ORPHAN, LATE, DAMAGE, VENDOR, UPSELL, DISCOUNT, PATTERN, AUTOPILOT,
BRIEFING) and the per-booking-stage learning checklist from the CEO V2
directive (2026-04-20) collapse into the twelve atomic workflows
defined here.  Each value is the wire string persisted in
:class:`brain_engine.autonomy.WorkflowAutonomy.workflow` and exposed to
the UI's Trust Meter, so renames are breaking.

The default ``event_type`` -> :class:`WorkflowKind` resolver is a best
effort across the
:class:`brain_engine.continual_learning.interaction_recorder.BrainEngineInteraction`
vocabulary; callers that produce richer metadata can supply a custom
:class:`WorkflowResolver`.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Final, TypeAlias

__all__ = [
    "DEFAULT_WORKFLOW_RESOLVER",
    "WorkflowKind",
    "WorkflowResolver",
    "default_workflow_for_event",
]


class WorkflowKind(StrEnum):
    """Atomic units of per-workflow autonomy.

    The taxonomy is derived from the V2 wireframe card vocabulary and
    the booking-stage learning checklist.  Values are stable wire
    strings — never rename without a migration of
    ``WorkflowAutonomy.workflow``.
    """

    MISMATCH_EXTRA_FEE = "mismatch_extra_fee"
    CODE_RELEASE = "code_release"
    ORPHAN_NIGHT = "orphan_night"
    EARLY_CHECKIN = "early_checkin"
    LATE_CHECKOUT = "late_checkout"
    DAMAGE_CLAIM = "damage_claim"
    VENDOR_DISPATCH = "vendor_dispatch"
    UPSELL_OFFER = "upsell_offer"
    DISCOUNT_REQUEST = "discount_request"
    PATTERN_PROMOTION = "pattern_promotion"
    DAILY_BRIEFING = "daily_briefing"
    INQUIRY_REPLY = "inquiry_reply"


WorkflowResolver: TypeAlias = Callable[[Any], "WorkflowKind | None"]


# ---------------------------------------------------------------------------
# Event-type fallback mapping
# ---------------------------------------------------------------------------
#
# Keys are case-insensitive (we lower the lookup key before a hit).  The
# table is intentionally narrow: anything not listed here resolves to
# ``None``, which the collector treats as "skip this interaction" rather
# than bucketing it into an arbitrary workflow.

_EVENT_TYPE_MAP: Final[dict[str, WorkflowKind]] = {
    # Pre-arrival mismatch / extra-person fees
    "guest_count_mismatch": WorkflowKind.MISMATCH_EXTRA_FEE,
    "extra_person_fee": WorkflowKind.MISMATCH_EXTRA_FEE,
    "extra_fee": WorkflowKind.MISMATCH_EXTRA_FEE,
    # Door codes / smart locks
    "code_release": WorkflowKind.CODE_RELEASE,
    "send_access_code": WorkflowKind.CODE_RELEASE,
    "access_code_request": WorkflowKind.CODE_RELEASE,
    # Calendar gaps
    "orphan_night": WorkflowKind.ORPHAN_NIGHT,
    "min_stay_exception": WorkflowKind.ORPHAN_NIGHT,
    # Stay-time flexibility
    "early_checkin": WorkflowKind.EARLY_CHECKIN,
    "early_checkin_request": WorkflowKind.EARLY_CHECKIN,
    "late_checkout": WorkflowKind.LATE_CHECKOUT,
    "late_checkout_request": WorkflowKind.LATE_CHECKOUT,
    # Post-stay claims
    "damage_report": WorkflowKind.DAMAGE_CLAIM,
    "damage_claim": WorkflowKind.DAMAGE_CLAIM,
    # Operations dispatch
    "vendor_dispatched": WorkflowKind.VENDOR_DISPATCH,
    "cleaner_dispatched": WorkflowKind.VENDOR_DISPATCH,
    # Upsell / discount
    "upsell": WorkflowKind.UPSELL_OFFER,
    "upsell_accepted": WorkflowKind.UPSELL_OFFER,
    "transfer_offer": WorkflowKind.UPSELL_OFFER,
    "discount_request": WorkflowKind.DISCOUNT_REQUEST,
    "discount_handling": WorkflowKind.DISCOUNT_REQUEST,
    # Meta — pattern lifecycle and daily summary
    "pattern_promote": WorkflowKind.PATTERN_PROMOTION,
    "pattern_demote": WorkflowKind.PATTERN_PROMOTION,
    "daily_briefing": WorkflowKind.DAILY_BRIEFING,
    # Inquiry-stage reply
    "inquiry": WorkflowKind.INQUIRY_REPLY,
    "inquiry_reply": WorkflowKind.INQUIRY_REPLY,
}


def default_workflow_for_event(event_type: str) -> WorkflowKind | None:
    """Map an interaction's ``event_type`` to its canonical workflow.

    Returns ``None`` for empty or unrecognised event types so the
    caller can skip the interaction without forcing an arbitrary
    bucket.

    Args:
        event_type: Raw event type string from the interaction record.

    Returns:
        The matching :class:`WorkflowKind`, or ``None`` when no rule
        applies.
    """
    if not event_type:
        return None
    return _EVENT_TYPE_MAP.get(event_type.lower())


def _resolve_from_interaction(ix: Any) -> WorkflowKind | None:
    """Default resolver shaped to the recorder's interaction object.

    Honours an explicit ``ix.workflow`` attribute when present (allows
    upstream code to bypass the event-type heuristic), otherwise falls
    back to :func:`default_workflow_for_event`.
    """
    explicit = getattr(ix, "workflow", "") or ""
    if explicit:
        try:
            return WorkflowKind(str(explicit))
        except ValueError:
            return None
    return default_workflow_for_event(
        str(getattr(ix, "event_type", "")),
    )


DEFAULT_WORKFLOW_RESOLVER: Final[WorkflowResolver] = (
    _resolve_from_interaction
)
