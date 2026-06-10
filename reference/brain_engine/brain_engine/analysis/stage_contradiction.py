"""Foundation Layer stage contradiction gate (Q5-C, Variant A).

When a guest message lands on Brain Engine, the FL-16
:class:`~brain_engine.analysis.orchestrator.FoundationAnalysisOrchestrator`
runs the trigger text through :class:`ScenarioMatcher` and picks
the dominant foundation scenario from the 469-row catalog.  The
match is decided **only** by trigger embedding cosine similarity
— calendar dates carried alongside the message do not influence
which scenario wins.

The result: a guest typing "I'm at the door" on an event whose
calendar says the guest already checked out 5 days ago matches
``arrival_event``.  Brain produces a cheerful arrival reply for
an impossible scenario.  Mümin's 2026-05-18 Sandbox UI
adversarial tests exploit this exact gap — he deliberately
manipulates the Reservation Details panel to make calendar
dates contradict the guest message, then reports Brain's
context-blind response as a bug.

This module closes the **observability** half of the gap (Q5-C
Variant A).  The orchestrator step that uses it
(:meth:`~brain_engine.analysis.orchestrator.FoundationAnalysisOrchestrator._detect_stage_contradiction`)
derives a booking stage from the event's calendar snapshot,
maps the dominant catalog entry's stage to the same enum, and
reports a mismatch on :pyattr:`AnalysisResult.stage_mismatch`
when the two disagree.

Variant A is intentionally **observation only**: it never
gates the guardrail / mining / routing steps.  A follow-up
Q5-C Variant B PR will add a mining gate once production
observation data tells us how often legitimate mismatches
occur (e.g. guest sends a checkout question one hour before
the calendar's checkout time — adjacent stages are usually
fine, hard mismatches are not).

The module is pure compute: no I/O, no LLM calls.  All inputs
are either parsed ISO timestamps or the typed catalog entry —
the orchestrator owns the I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from brain_engine.patterns.models import BookingStage

if TYPE_CHECKING:
    from brain_engine.patterns.foundation_registry import (
        FoundationScenario,
    )

__all__ = [
    "compatible_stages",
    "derive_stage_from_calendar",
    "detect_stage_mismatch",
    "scenario_stage_from_catalog",
]


# ── Window boundaries used by ``derive_stage_from_calendar`` ── #
#
# Same rationale as the existing ``_compute_temporal_axes``
# helper in conversation/service.py:1163 — windows are wide
# enough to absorb timezone slop and human pre-arrival prep
# (guests typing "I'm at the door" 30 min before the official
# 14:00 check-in time should not trip a contradiction).
_LONG_LEAD_DAYS: Final[int] = 7
_CHECKIN_WINDOW_HOURS: Final[int] = 24
_CHECKOUT_WINDOW_HOURS: Final[int] = 24


# Scenario stage_number → BookingStage.  The catalog uses a
# 9-stage taxonomy; this table maps each to the closest
# BookingStage enum value the rest of Brain Engine speaks.
# Stages with no clean enum match collapse to ``None`` and the
# mismatch check skips them (treated as stage-agnostic).
_CATALOG_STAGE_TO_BOOKING_STAGE: Final[Mapping[int, BookingStage | None]] = {
    1: BookingStage.PRE_BOOKING,
    2: BookingStage.BOOKING_REVIEW,
    3: BookingStage.PRE_ARRIVAL,
    4: BookingStage.CHECKIN,
    5: BookingStage.IN_STAY,
    # Stage 6 — Modification / mid-stay services / upsell.
    # No exact match in BookingStage; falls under MODIFICATION
    # which the rest of Brain treats as a sibling of IN_STAY.
    6: BookingStage.MODIFICATION,
    7: BookingStage.CHECKOUT,
    8: BookingStage.POST_CHECKOUT,
    # Stage 9 — Internal / Owner / Vendor / Integration.
    # These are stage-agnostic from the guest perspective; do
    # not flag a mismatch when stage 9 lands on any calendar
    # stage.
    9: None,
}


# Pairs of stages that count as "compatible" even when not
# exactly equal.  Adjacent transitions are the common case:
# a guest typing "I'm at the door" during PRE_ARRIVAL is the
# normal arrival pattern; flagging this as a contradiction
# would be a false positive.  Pairs are symmetric — checked in
# both directions.
_COMPATIBLE_PAIRS: Final[frozenset[frozenset[BookingStage]]] = frozenset(
    {
        frozenset({BookingStage.PRE_ARRIVAL, BookingStage.CHECKIN}),
        frozenset({BookingStage.CHECKIN, BookingStage.IN_STAY}),
        frozenset({BookingStage.IN_STAY, BookingStage.MODIFICATION}),
        frozenset({BookingStage.IN_STAY, BookingStage.CHECKOUT}),
        frozenset(
            {BookingStage.CHECKOUT, BookingStage.POST_CHECKOUT},
        ),
        frozenset(
            {BookingStage.PRE_BOOKING, BookingStage.BOOKING_REVIEW},
        ),
        frozenset(
            {BookingStage.BOOKING_REVIEW, BookingStage.PRE_ARRIVAL},
        ),
    },
)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string into a UTC datetime.

    Returns ``None`` when the input is empty or unparsable.  A
    bare date (``"2026-05-15"``) is accepted and treated as
    midnight UTC.  Timezone-naive timestamps are assumed to be
    UTC — Brain stores everything in UTC and the live path's
    ``current_time`` is documented as ISO-with-Z.
    """
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if "T" not in raw and " " not in raw:
            # Date-only form ("2026-05-15") → midnight UTC.
            parsed = datetime.fromisoformat(raw)
        else:
            normalised = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalised)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def derive_stage_from_calendar(
    check_in: str | None,
    check_out: str | None,
    current_time: str | None = None,
) -> BookingStage | None:
    """Return the :class:`BookingStage` the calendar implies.

    Args:
        check_in: ISO 8601 string for the reservation's check-in
            date/datetime, or ``None``.
        check_out: ISO 8601 string for check-out, or ``None``.
        current_time: ISO 8601 string for the message timestamp.
            When omitted, :func:`datetime.now` (UTC) is used.

    Returns:
        The derived :class:`BookingStage`, or ``None`` when
        ``check_in`` or ``check_out`` cannot be parsed.  Never
        raises — Q5-C must not break the orchestrator just
        because the calendar context is incomplete.

    The six stage ranges (relative to ``current_time``):

    * ``current < check_in - 7d``     → :pyattr:`BookingStage.PRE_BOOKING`
    * ``check_in - 7d ≤ current < check_in - 1d``
                                       → :pyattr:`BookingStage.PRE_ARRIVAL`
    * ``check_in - 24h ≤ current < check_in + 24h``
                                       → :pyattr:`BookingStage.CHECKIN`
    * ``check_in + 24h ≤ current < check_out - 24h``
                                       → :pyattr:`BookingStage.IN_STAY`
    * ``check_out - 24h ≤ current ≤ check_out + 24h``
                                       → :pyattr:`BookingStage.CHECKOUT`
    * ``current > check_out + 24h``    → :pyattr:`BookingStage.POST_CHECKOUT`

    Windows around check-in / check-out are wide (24 h each)
    on purpose: a guest typing "I'm at the door" 30 minutes
    before the official 14:00 check-in is the normal arrival
    pattern, not a contradiction.
    """
    ci = _parse_iso(check_in)
    co = _parse_iso(check_out)
    if ci is None or co is None:
        return None
    now = _parse_iso(current_time) or datetime.now(UTC)

    long_lead = timedelta(days=_LONG_LEAD_DAYS)
    short_lead = timedelta(hours=_CHECKIN_WINDOW_HOURS)
    short_tail = timedelta(hours=_CHECKOUT_WINDOW_HOURS)

    if now < ci - long_lead:
        return BookingStage.PRE_BOOKING
    if now < ci - short_lead:
        return BookingStage.PRE_ARRIVAL
    if now < ci + short_lead:
        return BookingStage.CHECKIN
    if now < co - short_tail:
        return BookingStage.IN_STAY
    if now <= co + short_tail:
        return BookingStage.CHECKOUT
    return BookingStage.POST_CHECKOUT


def scenario_stage_from_catalog(
    scenario: FoundationScenario | None,
) -> BookingStage | None:
    """Return the :class:`BookingStage` for a foundation entry.

    Reads :pyattr:`FoundationScenario.stage_number` (1-9) and
    maps it via :data:`_CATALOG_STAGE_TO_BOOKING_STAGE`.  Stage 9
    (Internal / Owner / Ops) maps to ``None`` because those
    scenarios are stage-agnostic from the guest's calendar
    perspective and would produce false-positive contradictions
    on every event.

    Args:
        scenario: Dominant catalog entry from the matcher, or
            ``None`` when the matcher returned nothing / Q5-A
            cleared the entry.

    Returns:
        Mapped :class:`BookingStage`, or ``None`` when the
        scenario is missing or maps to stage-agnostic.
    """
    if scenario is None:
        return None
    stage_number = getattr(scenario, "stage_number", None)
    if stage_number is None:
        return None
    return _CATALOG_STAGE_TO_BOOKING_STAGE.get(int(stage_number))


def compatible_stages(
    calendar_stage: BookingStage,
    scenario_stage: BookingStage,
) -> bool:
    """Return whether two stages count as "no contradiction".

    Exact equality is compatible.  Adjacent transition pairs
    (e.g. :pyattr:`PRE_ARRIVAL` ↔ :pyattr:`CHECKIN`) are also
    compatible — they are documented in
    :data:`_COMPATIBLE_PAIRS`.  Everything else is a hard
    mismatch.
    """
    if calendar_stage == scenario_stage:
        return True
    pair = frozenset({calendar_stage, scenario_stage})
    return pair in _COMPATIBLE_PAIRS


def detect_stage_mismatch(
    calendar_stage: BookingStage | None,
    scenario_stage: BookingStage | None,
) -> str | None:
    """Return a human-readable mismatch detail, or ``None``.

    Args:
        calendar_stage: Output of
            :func:`derive_stage_from_calendar`, or ``None`` when
            calendar data was missing.
        scenario_stage: Output of
            :func:`scenario_stage_from_catalog`, or ``None`` when
            the scenario is missing / stage-agnostic.

    Returns:
        ``None`` when nothing to flag (one input missing, exact
        match, or compatible-pair).  A short string identifying
        the mismatch otherwise — formatted for log lines and
        the :pyattr:`AnalysisResult.stage_mismatch_detail`
        field.  Format is stable for log-grep:
        ``"calendar=<calendar_stage> scenario=<scenario_stage>"``.

    Q5-C Variant A: this is **observation only**.  The caller
    must not gate any downstream decision on the return value
    — record it on :class:`AnalysisResult`, log it, expose it
    in API responses for Mümin's adversarial tests.  A later
    Q5-C Variant B PR may consume the field to skip pattern
    mining.
    """
    if calendar_stage is None or scenario_stage is None:
        return None
    if compatible_stages(calendar_stage, scenario_stage):
        return None
    return f"calendar={calendar_stage.value} scenario={scenario_stage.value}"
