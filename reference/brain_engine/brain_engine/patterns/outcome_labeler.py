"""Per-scenario outcome labelling for :class:`DecisionCase`.

The pre-P4 codebase emitted a generic :class:`CaseOutcome` per case
(``patterns/models.py``).  ali.md §8 makes the case that "successful"
is not a generic property — every scenario has its own success and
failure criteria, plus its own outcome window.  Without that
structure the pattern miner cannot tell apart a one-off goodwill
discount that doomed the ADR from a converted booking that held
margin, and the resulting rules drift into noise.

This module closes that gap with a small, pluggable framework:

* :class:`OutcomeObservation` carries every signal we might collect
  during the outcome window (replies, complaints, overrides, ops
  fulfilment, vendor state, revenue impact, payment, blockers,
  security incidents, …).  Every field is optional — observers
  populate only what they have access to and the labelling rules
  treat ``None`` as "unknown".
* :data:`OUTCOME_WINDOWS` materialises the five per-scenario windows
  spelled out in ali.md §8 (discount_request → 7 days or until
  booking decision; early_checkin → end of check-in day; …).
* :data:`_RULES_BY_SCENARIO` registers four concrete labelling rules
  for the scenarios that ali.md §8 explicitly covers
  (``amenity_exception``, ``discount_request``,
  ``guest_count_mismatch``, ``access_code_release``).  Other
  scenarios fall through to a generic labeller that mirrors the
  pre-P4 behaviour, so this module never narrows what the
  ecosystem can label.
* :class:`OutcomeLabeler` ties it together: ``label(case, obs)``
  routes to the registered rule and emits a fully-populated
  :class:`CaseOutcome` ready for storage.

The labelling rules are deliberately literal: they encode the
ali.md §8 success / failure clauses verbatim.  When the
observation is contradictory (both success and failure clauses
fire) the case is recorded as a failure — the failure clause
typically captures harder evidence (PM override, security
incident) and we do not want to silently upgrade a contested
outcome to "success".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

import structlog

from brain_engine.patterns.models import (
    CaseOutcome,
    DecisionCase,
    ResolutionType,
    Scenario,
)

__all__ = [
    "OUTCOME_WINDOWS",
    "OutcomeLabeler",
    "OutcomeObservation",
    "OutcomeRule",
    "OutcomeWindow",
    "register_rule",
    "rule_for",
    "window_for",
]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# OutcomeObservation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    """Bag of optional signals collected during the outcome window.

    Every field defaults to a "no signal" value.  Boolean fields
    that carry a tri-state semantic (success / failure / unknown)
    use ``bool | None`` and default to ``None``.  Plain ``bool``
    fields default to ``False`` and are interpreted as "did not
    happen as far as we observed".

    The observation is intentionally generic — concrete labelling
    rules pick out only the signals they need, and missing signals
    degrade the rule to ``successful=None`` rather than fabricate
    a verdict.
    """

    # Universal signals
    guest_replied: bool = False
    guest_complained: bool = False
    pm_overrode: bool = False
    pm_modified_decision: bool = False
    pm_reversed_decision: bool = False
    approval_required: bool = False
    approved: bool | None = None
    revenue_impact: float | None = None

    # Operations / amenity_exception
    ops_fulfilled: bool | None = None
    vendor_unavailable: bool = False
    cost_overrun: bool = False

    # Discount / discount_request, price_negotiation
    booking_converted: bool | None = None
    adr_acceptable: bool | None = None
    cancelled_after_decision: bool = False
    pm_gave_different_discount: bool = False

    # Guest count mismatch
    guest_confirmed_count: bool | None = None
    fee_collected: bool | None = None
    reservation_updated: bool = False
    arrival_issue: bool = False
    extra_unpaid_guests: bool = False
    blockers_cleared: bool | None = None

    # Access code release
    code_correct_and_live: bool | None = None
    guest_locked_out: bool = False
    sent_before_id_check: bool = False
    security_incident: bool = False

    # Maintenance / damage / claim
    issue_resolved: bool | None = None
    repeat_complaint: bool = False
    claim_accepted: bool | None = None


# ---------------------------------------------------------------------------
# OutcomeWindow
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutcomeWindow:
    """Per-scenario outcome window from ali.md §8.

    A window has either an absolute duration in days, an event-
    driven close (named lifecycle event) or both — many ali.md
    rules combine "until event X *or* N days, whichever first".

    Attributes:
        scenario: Scenario the window is registered for.
        duration_days: Absolute upper bound on window length, in
            days; ``None`` when the window is purely event-driven.
        closes_at_event: Symbolic name of the event that closes
            the window early when it fires (e.g.
            ``"booking_decision"``, ``"successful_checkin"``).
            Concrete consumers map the symbol to their lifecycle
            telemetry; this module does not interpret it.
    """

    scenario: Scenario
    duration_days: float | None = None
    closes_at_event: str | None = None


OUTCOME_WINDOWS: Final[Mapping[Scenario, OutcomeWindow]] = {
    Scenario.DISCOUNT_REQUEST: OutcomeWindow(
        scenario=Scenario.DISCOUNT_REQUEST,
        duration_days=7.0,
        closes_at_event="booking_decision",
    ),
    Scenario.PRICE_NEGOTIATION: OutcomeWindow(
        scenario=Scenario.PRICE_NEGOTIATION,
        duration_days=7.0,
        closes_at_event="booking_decision",
    ),
    Scenario.EARLY_CHECKIN: OutcomeWindow(
        scenario=Scenario.EARLY_CHECKIN,
        closes_at_event="check_in_day_end",
    ),
    Scenario.ACCESS_CODE_RELEASE: OutcomeWindow(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        closes_at_event="successful_checkin",
    ),
    Scenario.MAINTENANCE_REQUEST: OutcomeWindow(
        scenario=Scenario.MAINTENANCE_REQUEST,
        closes_at_event="issue_resolved_no_repeat",
    ),
    Scenario.DAMAGE_REPORT: OutcomeWindow(
        scenario=Scenario.DAMAGE_REPORT,
        closes_at_event="claim_accepted_or_denied",
    ),
}


def window_for(scenario: Scenario) -> OutcomeWindow | None:
    """Return the outcome window for ``scenario`` or ``None``."""
    return OUTCOME_WINDOWS.get(scenario)


# ---------------------------------------------------------------------------
# OutcomeRule registry
# ---------------------------------------------------------------------------


OutcomeRule = Callable[[DecisionCase, OutcomeObservation], CaseOutcome]


def _resolution_from_signals(
    *,
    pm_overrode: bool,
    pm_modified_decision: bool,
    pm_reversed_decision: bool,
    successful: bool | None,
    guest_replied: bool,
    booking_converted: bool | None = None,
) -> ResolutionType | None:
    """Map override / success signals onto a :class:`ResolutionType`.

    Resolution precedence (most specific wins):

    1. PM modification → ``PM_MODIFIED``
    2. PM reversal of a previously-accepted decision → ``PM_DENIED``
    3. Generic PM override → ``PM_DENIED``
    4. Successful + booking converted → ``GUEST_ACCEPTED``
    5. Successful auto-handled → ``AUTO_RESOLVED``
    6. Failure with no guest reply → ``TIMEOUT``
    7. Failure with explicit refusal → ``GUEST_REJECTED``
    8. Otherwise → ``None`` (unknown).
    """
    if pm_modified_decision:
        return ResolutionType.PM_MODIFIED
    if pm_reversed_decision or pm_overrode:
        return ResolutionType.PM_DENIED
    if successful is True:
        if booking_converted is True:
            return ResolutionType.GUEST_ACCEPTED
        return ResolutionType.AUTO_RESOLVED
    if successful is False:
        if not guest_replied:
            return ResolutionType.TIMEOUT
        return ResolutionType.GUEST_REJECTED
    return None


def _materialise(
    obs: OutcomeObservation,
    *,
    successful: bool | None,
    booking_converted: bool | None = None,
) -> CaseOutcome:
    """Project an :class:`OutcomeObservation` onto a :class:`CaseOutcome`.

    Centralises the field copy so per-scenario rules only own the
    success / failure clause.  ``successful`` is the per-scenario
    verdict (``True`` / ``False`` / ``None``); the other fields
    follow mechanically.
    """
    resolution = _resolution_from_signals(
        pm_overrode=obs.pm_overrode,
        pm_modified_decision=obs.pm_modified_decision,
        pm_reversed_decision=obs.pm_reversed_decision,
        successful=successful,
        guest_replied=obs.guest_replied,
        booking_converted=booking_converted,
    )
    return CaseOutcome(
        guest_replied=obs.guest_replied,
        human_overrode=obs.pm_overrode
        or obs.pm_modified_decision
        or obs.pm_reversed_decision,
        approval_required=obs.approval_required,
        approved=obs.approved,
        successful=successful,
        resolution_type=resolution,
        revenue_impact=obs.revenue_impact,
    )


# ---------------------------------------------------------------------------
# Per-scenario rules
# ---------------------------------------------------------------------------


def _label_amenity_exception(
    case: DecisionCase, obs: OutcomeObservation
) -> CaseOutcome:
    """Label an amenity-exception case per ali.md §8.

    Success: guest accepted (replied without complaint), ops
    fulfilled, no PM override, no cost overrun.

    Failure: vendor unavailable, guest complained, PM reversed
    the decision, or extra cost was incurred without approval.
    """
    del case
    success = (
        obs.ops_fulfilled is True
        and obs.guest_replied
        and not obs.guest_complained
        and not obs.pm_overrode
        and not obs.pm_reversed_decision
        and not obs.cost_overrun
    )
    failure = (
        obs.vendor_unavailable
        or obs.guest_complained
        or obs.pm_reversed_decision
        or obs.cost_overrun
    )
    successful: bool | None
    if failure:
        successful = False
    elif success:
        successful = True
    else:
        successful = None
    return _materialise(obs, successful=successful)


def _label_discount_request(
    case: DecisionCase, obs: OutcomeObservation
) -> CaseOutcome:
    """Label a discount-negotiation case per ali.md §8.

    Success: booking converted, ADR acceptable, no later
    cancellation, no PM override or different-discount handover.

    Failure: guest disappeared, PM gave a different discount, or
    booking converted at an unacceptable ADR (margin breach).
    """
    del case
    success = (
        obs.booking_converted is True
        and obs.adr_acceptable is True
        and not obs.cancelled_after_decision
        and not obs.pm_overrode
        and not obs.pm_gave_different_discount
    )
    margin_breach = (
        obs.booking_converted is True and obs.adr_acceptable is False
    )
    failure = (
        margin_breach
        or obs.pm_gave_different_discount
        or obs.cancelled_after_decision
        or (obs.booking_converted is False and not obs.guest_replied)
    )
    successful: bool | None
    if failure:
        successful = False
    elif success:
        successful = True
    else:
        successful = None
    return _materialise(
        obs,
        successful=successful,
        booking_converted=obs.booking_converted,
    )


def _label_guest_count_mismatch(
    case: DecisionCase, obs: OutcomeObservation
) -> CaseOutcome:
    """Label a guest-count-mismatch case per ali.md §8.

    Success: guest confirmed actual count, fee collected when
    applicable, reservation updated, blockers cleared before
    access-code release, no arrival issue.

    Failure: guest never confirmed, PM bypassed manually, access
    code sent despite an active blocker, or guest arrived with
    extra unpaid guests.
    """
    del case
    fee_ok = obs.fee_collected is not False
    success = (
        obs.guest_confirmed_count is True
        and obs.reservation_updated
        and fee_ok
        and obs.blockers_cleared is not False
        and not obs.arrival_issue
        and not obs.pm_overrode
    )
    failure = (
        obs.guest_confirmed_count is False
        or obs.pm_overrode
        or obs.arrival_issue
        or obs.extra_unpaid_guests
        or obs.blockers_cleared is False
    )
    successful: bool | None
    if failure:
        successful = False
    elif success:
        successful = True
    else:
        successful = None
    return _materialise(obs, successful=successful)


def _label_access_code_release(
    case: DecisionCase, obs: OutcomeObservation
) -> CaseOutcome:
    """Label an access-code-release case per ali.md §8.

    Success: guest checked in without access issue, the code was
    correct and live, required blockers cleared before the send.

    Failure: wrong or stale code, guest locked out, code sent
    before ID / payment / guest-count confirmation, or any
    security incident.
    """
    del case
    success = (
        obs.code_correct_and_live is True
        and obs.blockers_cleared is True
        and not obs.guest_locked_out
        and not obs.security_incident
        and not obs.sent_before_id_check
    )
    failure = (
        obs.code_correct_and_live is False
        or obs.guest_locked_out
        or obs.sent_before_id_check
        or obs.security_incident
    )
    successful: bool | None
    if failure:
        successful = False
    elif success:
        successful = True
    else:
        successful = None
    return _materialise(obs, successful=successful)


def _label_generic(case: DecisionCase, obs: OutcomeObservation) -> CaseOutcome:
    """Generic fallback for scenarios without a dedicated rule.

    Mirrors the pre-P4 behaviour: ``successful`` is whatever the
    observation explicitly states (positive when the guest replied
    and there was no override, negative when PM reversed, otherwise
    unknown).  This keeps the labeller usable for the 22 scenarios
    ali.md §8 does not enumerate.
    """
    del case
    successful: bool | None
    if obs.pm_reversed_decision or obs.guest_complained:
        successful = False
    elif obs.guest_replied and not obs.pm_overrode:
        successful = True
    else:
        successful = None
    return _materialise(obs, successful=successful)


_RULES_BY_SCENARIO: dict[Scenario, OutcomeRule] = {
    Scenario.AMENITY_EXCEPTION: _label_amenity_exception,
    Scenario.DISCOUNT_REQUEST: _label_discount_request,
    Scenario.PRICE_NEGOTIATION: _label_discount_request,
    Scenario.GUEST_COUNT_MISMATCH: _label_guest_count_mismatch,
    Scenario.ACCESS_CODE_RELEASE: _label_access_code_release,
}


def rule_for(scenario: Scenario) -> OutcomeRule:
    """Return the registered rule for ``scenario`` or the generic fallback."""
    return _RULES_BY_SCENARIO.get(scenario, _label_generic)


def register_rule(scenario: Scenario, rule: OutcomeRule) -> None:
    """Register a custom labelling rule for ``scenario``.

    Existing registrations are overwritten; downstream consumers
    can extend coverage to new scenarios (or override an existing
    rule for a scope-specific tweak) without monkey-patching the
    module.  Callers that want to roll back should keep a handle
    to the previous rule via :func:`rule_for` before registering.
    """
    _RULES_BY_SCENARIO[scenario] = rule


# ---------------------------------------------------------------------------
# OutcomeLabeler
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutcomeLabeler:
    """Routes decision cases to the registered per-scenario rule.

    The labeller is a thin orchestration shell — the real work
    lives in the ``_label_*`` functions registered in
    :data:`_RULES_BY_SCENARIO`.  Keeping the labeller stateless
    means a single instance is safe to share across threads /
    async tasks; concrete callers typically build one at process
    boot and reuse it.

    Attributes:
        log_unmatched: When ``True`` (default), a warning is
            emitted whenever a case falls through to the generic
            fallback rule.  Set to ``False`` for batch backfills
            where the warnings would just be noise.
    """

    log_unmatched: bool = True
    _log: structlog.stdlib.BoundLogger = field(
        default_factory=lambda: logger.bind(component="outcome_labeler"),
        init=False,
        repr=False,
        compare=False,
    )

    def label(
        self,
        case: DecisionCase,
        observation: OutcomeObservation,
    ) -> CaseOutcome:
        """Label ``case`` using the rule registered for its scenario."""
        rule = _RULES_BY_SCENARIO.get(case.scenario)
        if rule is None:
            if self.log_unmatched:
                self._log.debug(
                    "outcome_rule_fallback",
                    case_id=case.case_id[:8],
                    scenario=case.scenario.value,
                )
            return _label_generic(case, observation)
        return rule(case, observation)
