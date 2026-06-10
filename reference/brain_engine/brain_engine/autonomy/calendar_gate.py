"""Calendar-aware autonomy gate.

The :class:`brain_engine.autonomy.AutonomyEngine` answers "what
autonomy state has this (property, workflow) earned?".  For
calendar-dependent workflows that is *necessary but not sufficient*:
even a workflow on AUTOPILOT must be re-validated against current
calendar reality before acting, because the calendar is the most
volatile piece of operational context (a single new booking can
invalidate yesterday's safe decision).

This gate sits in front of the dispatcher:

1. Pull the engine state for the workflow.
2. For calendar-gated workflows (``ORPHAN_NIGHT``, ``EARLY_CHECKIN``,
   ``LATE_CHECKOUT``) consult :class:`CalendarEvaluator` against the
   freshly-fetched calendar.
3. Emit a :class:`CalendarGateDecision`:
    - ``ALLOW`` — calendar context supports the engine state.
    - ``DOWNGRADE`` — calendar context is uncertain; act one rung
      lower (AUTOPILOT → SEMI_AUTO → OBSERVE).
    - ``BLOCK`` — calendar conflicts with the action; defer to PM.

For workflows that are not calendar-gated the gate is a transparent
``ALLOW`` so callers can route every decision through it uniformly.

Pure component — no I/O, no global state.  Callers fetch the calendar
themselves (the gate makes no claim about how stale the data is and
will refuse to act on an empty payload for calendar-gated workflows).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

import structlog

from brain_engine.autonomy.engine import AutonomyEngine
from brain_engine.autonomy.models import AutonomyState, state_rank
from brain_engine.autonomy.workflow_kinds import WorkflowKind
from brain_engine.calendar.evaluator import (
    CalendarEvaluator,
    FeasibilityResult,
    GapInfo,
)


__all__ = [
    "CalendarAutonomyGate",
    "CalendarGateDecision",
    "CalendarSignal",
    "CalendarVerdict",
]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Workflows whose decisions depend on current calendar state.  Anything
# outside this set is allowed through with no calendar inspection.
_CALENDAR_GATED: Final[frozenset[WorkflowKind]] = frozenset({
    WorkflowKind.ORPHAN_NIGHT,
    WorkflowKind.EARLY_CHECKIN,
    WorkflowKind.LATE_CHECKOUT,
})

# A feasibility check that returns less than this many buffer hours is
# treated as "tight" — feasible but not safe enough for autopilot.
_TIGHT_BUFFER_HOURS: Final[float] = 6.0

# A gap with sellability above this threshold is considered "likely to
# sell"; a min-stay exception there is not a clear-cut win.
_HIGH_SELLABILITY: Final[float] = 0.7


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class CalendarVerdict(StrEnum):
    """How the calendar gate modulates the engine state.

    Stable wire-strings — surfaced in audit logs and the Trust Meter UI.
    """

    ALLOW = "allow"
    DOWNGRADE = "downgrade"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class CalendarSignal:
    """Why the gate reached its verdict.

    Attributes:
        code: Stable machine-readable reason; the UI may switch on it.
        detail: Human-readable explanation for audit logs and tooltips.
        conflict_reservation_id: Reservation that caused the conflict
            (when applicable).
        buffer_hours: Available buffer time, when the verdict came from
            an early/late timing check.
    """

    code: str
    detail: str
    conflict_reservation_id: str | None = None
    buffer_hours: float | None = None


@dataclass(frozen=True, slots=True)
class CalendarGateDecision:
    """Final autonomy decision after the calendar gate has run.

    Attributes:
        workflow: The workflow under evaluation.
        base_state: State the :class:`AutonomyEngine` returned.
        effective_state: State the gate authorises right now.  Equal to
            ``base_state`` when the verdict is :pyattr:`CalendarVerdict.ALLOW`,
            one rank lower under ``DOWNGRADE``, and ``OBSERVE`` under
            ``BLOCK`` — the dispatcher must not act without PM input.
        verdict: Modulation kind.
        signal: Reason payload.
    """

    workflow: WorkflowKind
    base_state: AutonomyState
    effective_state: AutonomyState
    verdict: CalendarVerdict
    signal: CalendarSignal

    @property
    def is_blocked(self) -> bool:
        """Whether the dispatcher must defer to PM."""
        return self.verdict is CalendarVerdict.BLOCK

    @property
    def is_downgraded(self) -> bool:
        """Whether the effective state is below the engine state."""
        return self.verdict is CalendarVerdict.DOWNGRADE

    @property
    def allows_immediate_execution(self) -> bool:
        """Whether the gate authorises autopilot execution right now."""
        return (
            self.verdict is CalendarVerdict.ALLOW
            and self.effective_state is AutonomyState.AUTOPILOT
        )


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class CalendarAutonomyGate:
    """Re-validate engine autonomy against current calendar reality.

    The gate owns no state of its own; it composes the autonomy engine
    with a :class:`CalendarEvaluator` and returns a single decision per
    call.  Construction is cheap — instantiate per request when handy.
    """

    def __init__(
        self,
        *,
        engine: AutonomyEngine,
        evaluator: CalendarEvaluator | None = None,
    ) -> None:
        self._engine = engine
        self._evaluator = evaluator or CalendarEvaluator()
        self._log = logger.bind(component="calendar_autonomy_gate")

    async def decide(
        self,
        *,
        property_id: str,
        workflow: WorkflowKind,
        calendar_data: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> CalendarGateDecision:
        """Decide whether the dispatcher may act on ``workflow``.

        Args:
            property_id: Property the action targets.
            workflow: Workflow under evaluation.
            calendar_data: Freshly fetched calendar payload.  Required
                for calendar-gated workflows; ignored for the rest.
            context: Workflow-specific inputs.  Each handler documents
                the keys it consumes.

        Returns:
            A :class:`CalendarGateDecision` carrying the modulated
            state and the reason.
        """
        base_state = await self._engine.state_for(
            property_id=property_id, workflow=workflow.value,
        )
        if workflow not in _CALENDAR_GATED:
            return _allow_through(workflow, base_state, "workflow_calendar_independent")

        if not calendar_data:
            decision = _block(
                workflow=workflow,
                base_state=base_state,
                signal=CalendarSignal(
                    code="calendar_missing",
                    detail=(
                        "Calendar data is empty; calendar-gated workflows "
                        "cannot run without current availability context."
                    ),
                ),
            )
            self._emit_audit(property_id, decision)
            return decision

        ctx = context or {}
        if workflow is WorkflowKind.LATE_CHECKOUT:
            decision = self._decide_late_checkout(
                base_state=base_state,
                calendar_data=calendar_data,
                property_id=property_id,
                context=ctx,
            )
        elif workflow is WorkflowKind.EARLY_CHECKIN:
            decision = self._decide_early_checkin(
                base_state=base_state,
                calendar_data=calendar_data,
                property_id=property_id,
                context=ctx,
            )
        else:
            decision = self._decide_orphan_night(
                base_state=base_state,
                calendar_data=calendar_data,
                property_id=property_id,
                context=ctx,
            )
        self._emit_audit(property_id, decision)
        return decision

    # ── Per-workflow handlers ─────────────────────────────── #

    def _decide_late_checkout(
        self,
        *,
        base_state: AutonomyState,
        calendar_data: Mapping[str, Any],
        property_id: str,
        context: Mapping[str, Any],
    ) -> CalendarGateDecision:
        """Inputs: ``checkout_date`` (ISO str), ``requested_hour`` (int)."""
        checkout_date = _str(context.get("checkout_date"))
        requested_hour = _int(context.get("requested_hour"), default=14)
        if not checkout_date:
            return _block(
                workflow=WorkflowKind.LATE_CHECKOUT,
                base_state=base_state,
                signal=CalendarSignal(
                    code="missing_checkout_date",
                    detail="Late-checkout request needs a checkout date.",
                ),
            )
        feasibility = self._evaluator.check_late_checkout_feasibility(
            calendar_data=dict(calendar_data),
            property_id=property_id,
            checkout_date_str=checkout_date,
            requested_hour=requested_hour,
        )
        return _from_feasibility(
            workflow=WorkflowKind.LATE_CHECKOUT,
            base_state=base_state,
            feasibility=feasibility,
            block_code="late_checkout_conflict",
            tight_code="late_checkout_tight_buffer",
            ok_code="late_checkout_safe",
        )

    def _decide_early_checkin(
        self,
        *,
        base_state: AutonomyState,
        calendar_data: Mapping[str, Any],
        property_id: str,
        context: Mapping[str, Any],
    ) -> CalendarGateDecision:
        """Inputs: ``checkin_date`` (ISO str), ``requested_hour`` (int)."""
        checkin_date = _str(context.get("checkin_date"))
        requested_hour = _int(context.get("requested_hour"), default=12)
        if not checkin_date:
            return _block(
                workflow=WorkflowKind.EARLY_CHECKIN,
                base_state=base_state,
                signal=CalendarSignal(
                    code="missing_checkin_date",
                    detail="Early-checkin request needs a checkin date.",
                ),
            )
        feasibility = self._evaluator.check_early_checkin_feasibility(
            calendar_data=dict(calendar_data),
            property_id=property_id,
            checkin_date_str=checkin_date,
            requested_hour=requested_hour,
        )
        return _from_feasibility(
            workflow=WorkflowKind.EARLY_CHECKIN,
            base_state=base_state,
            feasibility=feasibility,
            block_code="early_checkin_conflict",
            tight_code="early_checkin_tight_buffer",
            ok_code="early_checkin_safe",
        )

    def _decide_orphan_night(
        self,
        *,
        base_state: AutonomyState,
        calendar_data: Mapping[str, Any],
        property_id: str,
        context: Mapping[str, Any],
    ) -> CalendarGateDecision:
        """Inputs: ``requested_nights`` (int), ``min_stay`` (int),
        ``gap_start_date`` (ISO str pointing at the gap's first night).
        """
        requested_nights = _int(context.get("requested_nights"))
        min_stay = _int(context.get("min_stay"), default=1)
        gap_start_str = _str(context.get("gap_start_date"))
        if requested_nights <= 0 or not gap_start_str:
            return _block(
                workflow=WorkflowKind.ORPHAN_NIGHT,
                base_state=base_state,
                signal=CalendarSignal(
                    code="missing_orphan_inputs",
                    detail=(
                        "Orphan-night gate requires requested_nights and "
                        "gap_start_date in the context payload."
                    ),
                ),
            )
        gap_start = _parse_iso_date(gap_start_str)
        if gap_start is None:
            return _block(
                workflow=WorkflowKind.ORPHAN_NIGHT,
                base_state=base_state,
                signal=CalendarSignal(
                    code="invalid_gap_start_date",
                    detail=f"gap_start_date {gap_start_str!r} is not ISO.",
                ),
            )
        gap_info = self._locate_gap(
            calendar_data=calendar_data,
            property_id=property_id,
            min_stay=min_stay,
            gap_start=gap_start,
        )
        feasibility = self._evaluator.check_min_stay_exception(
            requested_nights=requested_nights,
            min_stay=min_stay,
            gap_info=gap_info,
        )
        if not feasibility.feasible:
            return _block(
                workflow=WorkflowKind.ORPHAN_NIGHT,
                base_state=base_state,
                signal=CalendarSignal(
                    code="orphan_exception_unjustified",
                    detail=feasibility.reason,
                ),
            )
        if gap_info is not None and gap_info.sellability_score >= _HIGH_SELLABILITY:
            return _downgrade(
                workflow=WorkflowKind.ORPHAN_NIGHT,
                base_state=base_state,
                signal=CalendarSignal(
                    code="orphan_high_sellability",
                    detail=(
                        f"Gap sellability {gap_info.sellability_score:.2f} is "
                        "high — defer autopilot to semi-auto for PM review."
                    ),
                ),
            )
        return _allow(
            workflow=WorkflowKind.ORPHAN_NIGHT,
            base_state=base_state,
            signal=CalendarSignal(
                code="orphan_exception_supported",
                detail=feasibility.reason,
            ),
        )

    # ── Helpers ──────────────────────────────────────────── #

    def _locate_gap(
        self,
        *,
        calendar_data: Mapping[str, Any],
        property_id: str,
        min_stay: int,
        gap_start: date,
    ) -> GapInfo | None:
        """Return the gap whose first night equals ``gap_start``."""
        gaps = self._evaluator.analyze_gaps(
            calendar_data=dict(calendar_data),
            property_id=property_id,
            min_stay=min_stay,
        )
        for gap in gaps:
            if gap.gap_start == gap_start:
                return gap
        return None

    def _emit_audit(
        self,
        property_id: str,
        decision: CalendarGateDecision,
    ) -> None:
        # BLOCK is loud (warning), DOWNGRADE is informative, ALLOW stays
        # debug to keep the hot path quiet.
        if decision.is_blocked:
            self._log.warning(
                "calendar_gate.block",
                property_id=property_id,
                workflow=decision.workflow.value,
                base_state=decision.base_state.value,
                code=decision.signal.code,
            )
            return
        if decision.is_downgraded:
            self._log.info(
                "calendar_gate.downgrade",
                property_id=property_id,
                workflow=decision.workflow.value,
                base_state=decision.base_state.value,
                effective_state=decision.effective_state.value,
                code=decision.signal.code,
            )
            return
        self._log.debug(
            "calendar_gate.allow",
            property_id=property_id,
            workflow=decision.workflow.value,
            state=decision.effective_state.value,
            code=decision.signal.code,
        )


# ---------------------------------------------------------------------------
# Module-level helpers — keep handlers terse and uniform.
# ---------------------------------------------------------------------------


def _allow_through(
    workflow: WorkflowKind,
    base_state: AutonomyState,
    code: str,
) -> CalendarGateDecision:
    """Pass the engine state through untouched (non-calendar workflow)."""
    return CalendarGateDecision(
        workflow=workflow,
        base_state=base_state,
        effective_state=base_state,
        verdict=CalendarVerdict.ALLOW,
        signal=CalendarSignal(
            code=code,
            detail="Workflow does not depend on calendar state.",
        ),
    )


def _allow(
    *,
    workflow: WorkflowKind,
    base_state: AutonomyState,
    signal: CalendarSignal,
) -> CalendarGateDecision:
    return CalendarGateDecision(
        workflow=workflow,
        base_state=base_state,
        effective_state=base_state,
        verdict=CalendarVerdict.ALLOW,
        signal=signal,
    )


def _downgrade(
    *,
    workflow: WorkflowKind,
    base_state: AutonomyState,
    signal: CalendarSignal,
) -> CalendarGateDecision:
    return CalendarGateDecision(
        workflow=workflow,
        base_state=base_state,
        effective_state=_one_rung_lower(base_state),
        verdict=CalendarVerdict.DOWNGRADE,
        signal=signal,
    )


def _block(
    *,
    workflow: WorkflowKind,
    base_state: AutonomyState,
    signal: CalendarSignal,
) -> CalendarGateDecision:
    return CalendarGateDecision(
        workflow=workflow,
        base_state=base_state,
        effective_state=AutonomyState.OBSERVE,
        verdict=CalendarVerdict.BLOCK,
        signal=signal,
    )


def _from_feasibility(
    *,
    workflow: WorkflowKind,
    base_state: AutonomyState,
    feasibility: FeasibilityResult,
    block_code: str,
    tight_code: str,
    ok_code: str,
) -> CalendarGateDecision:
    """Translate a :class:`FeasibilityResult` into a gate decision.

    No conflict on the adjacent date → ``ALLOW`` regardless of buffer
    (buffer here is just "hours before the standard check time" which
    is an ops concern, not a calendar one).  When the calendar shows a
    same-day adjacent reservation but the buffer still satisfies the
    cleaning window, autopilot is downgraded to semi-auto so the PM
    can sanity-check the squeeze.
    """
    if not feasibility.feasible:
        return _block(
            workflow=workflow,
            base_state=base_state,
            signal=CalendarSignal(
                code=block_code,
                detail=feasibility.reason,
                conflict_reservation_id=feasibility.conflict_reservation_id,
                buffer_hours=feasibility.buffer_hours,
            ),
        )
    if feasibility.conflict_reservation_id is None:
        return _allow(
            workflow=workflow,
            base_state=base_state,
            signal=CalendarSignal(
                code=ok_code,
                detail=feasibility.reason,
                buffer_hours=feasibility.buffer_hours,
            ),
        )
    if feasibility.buffer_hours < _TIGHT_BUFFER_HOURS:
        return _downgrade(
            workflow=workflow,
            base_state=base_state,
            signal=CalendarSignal(
                code=tight_code,
                detail=feasibility.reason,
                conflict_reservation_id=feasibility.conflict_reservation_id,
                buffer_hours=feasibility.buffer_hours,
            ),
        )
    return _allow(
        workflow=workflow,
        base_state=base_state,
        signal=CalendarSignal(
            code=ok_code,
            detail=feasibility.reason,
            buffer_hours=feasibility.buffer_hours,
        ),
    )


def _one_rung_lower(state: AutonomyState) -> AutonomyState:
    """AUTOPILOT → SEMI_AUTO → OBSERVE → OBSERVE."""
    rank = state_rank(state)
    if rank <= 0:
        return AutonomyState.OBSERVE
    if rank == 1:
        return AutonomyState.OBSERVE
    return AutonomyState.SEMI_AUTO


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
