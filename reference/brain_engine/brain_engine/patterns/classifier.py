"""Decision classification — infer (stage, scenario, decision_type).

The learning subsystem requires every logged :class:`DecisionCase` to
carry a non-``GENERAL`` scenario for :pyattr:`DecisionCase.is_learnable`
to return ``True``.  Earlier revisions hard-coded ``(IN_STAY, GENERAL,
INFORM)`` inside :class:`~brain_engine.conversation.service.ConversationService`,
which silently disqualified every case from pattern extraction.

:class:`DecisionClassifier` replaces that hard-code with a deterministic,
signal-driven classifier.  It consumes:

* 16 business flags produced by
  :class:`~brain_engine.reasoning.business_classifier.BusinessFlagClassifier`
  (strongest signal — already LLM-normalised);
* the cleaned guest message text for targeted keyword heuristics;
* the assistant response text for decision-type inference;
* the tool names invoked during ReAct execution (``send_access_code``,
  ``fetch_availability``, …);
* the optional ``reservation_id`` for a booking-stage prior.

No LLM calls are made — the classifier is a pure function suitable for
the hot path of every conversation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from brain_engine.patterns.models import (
    BookingStage,
    DecisionType,
    Scenario,
)

if TYPE_CHECKING:
    from brain_engine.conversation.models import BusinessFlags


# Stage-2 LLM hint feature flag.  Default ON — hint paths gracefully
# degrade to keyword chain when hint is empty / invalid / disabled,
# so flipping this off via env never breaks the classifier; it
# simply restores pre-Stage-2 behaviour on the next pod restart.
_LLM_HINTS_ENV: Final[str] = "BRAIN_LLM_HINTS_ENABLED"
_FALSY_ENV_VALUES: Final[frozenset[str]] = frozenset(
    {"false", "0", "no", "off", ""},
)


def _llm_hints_enabled() -> bool:
    """Return ``True`` unless the operator opted out via env."""
    raw = os.environ.get(_LLM_HINTS_ENV, "true").strip().lower()
    return raw not in _FALSY_ENV_VALUES


def _record_classifier_metric(method: str, **labels: str) -> None:
    """Best-effort emit one classifier-hint Prometheus counter.

    Wraps the exporter behind a try/except so a broken metrics
    registry can never block classification on the hot path.
    ``method`` is the recorder name on
    :class:`PrometheusExporter` (e.g. ``"record_classifier_hint_used"``).
    """
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        exporter = build_default_exporter()
        getattr(exporter, method)(**labels)
    except Exception:
        return


# Time-window thresholds (hours) for stage refinement.  Aligned with the
# ``BookingFeatures.is_within_24h_window`` /
# ``is_within_4h_window`` flags emitted by
# :class:`~brain_engine.patterns.feature_builder.FeatureBuilder`.
_PRE_ARRIVAL_WINDOW_H: Final[float] = 24.0
_CHECKIN_WINDOW_H: Final[float] = 4.0
_CHECKOUT_WINDOW_H: Final[float] = 4.0


# ---------------------------------------------------------------------------
# ISO-8601 helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    """Best-effort parse of an ISO-8601 timestamp into aware UTC.

    The classifier accepts free-form strings from upstream callers
    (sandbox UI, GraphQL, PMS API).  Whenever parsing fails we
    return ``None`` so the caller can fall back to a coarse
    lexicographic comparison.

    Args:
        value: Candidate ISO-8601 timestamp.

    Returns:
        Timezone-aware :class:`datetime.datetime` in UTC, or
        ``None`` when the input cannot be interpreted.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed


# ---------------------------------------------------------------------------
# Public stage-window helper
# ---------------------------------------------------------------------------


def classify_stage_by_window(
    *,
    message_sent_at: str,
    arrival_date: str,
    departure_date: str,
) -> BookingStage | None:
    """Map message timestamp + stay window to a :class:`BookingStage`.

    Pure function — no side effects, no mutable state.  Implements the
    same proximity-to-arrival ladder used by the production classifier
    (4 h CHECKIN window, 24 h PRE_ARRIVAL window, 4 h CHECKOUT window),
    so the sandbox preview surface produces identical predictions to
    the live conversation pipeline.

    Args:
        message_sent_at: ISO-8601 timestamp at which the guest message
            was (or will be) sent.  ``"2026-04-30T18:00:00Z"`` style
            inputs are accepted; trailing ``Z`` is normalised to UTC.
        arrival_date: Reservation check-in (ISO-8601, date or full
            timestamp).  Date-only inputs are interpreted as midnight
            UTC, which matches how the production ``_classify_stage``
            short-circuit treats them.
        departure_date: Reservation check-out (ISO-8601).

    Returns:
        The matching :class:`BookingStage` when all three timestamps
        parse, otherwise ``None`` so the caller can fall back to
        keyword heuristics.

    Raises:
        Nothing — malformed input simply returns ``None``.
    """
    now = _parse_iso(message_sent_at)
    ci = _parse_iso(arrival_date)
    co = _parse_iso(departure_date)
    if now is None or ci is None or co is None:
        return None
    if now < ci:
        delta_h = (ci - now).total_seconds() / 3600.0
        if delta_h <= _CHECKIN_WINDOW_H:
            return BookingStage.CHECKIN
        if delta_h <= _PRE_ARRIVAL_WINDOW_H:
            return BookingStage.PRE_ARRIVAL
        return BookingStage.PRE_BOOKING
    if now <= co:
        in_h = (now - ci).total_seconds() / 3600.0
        out_h = (co - now).total_seconds() / 3600.0
        if in_h <= _CHECKIN_WINDOW_H:
            return BookingStage.CHECKIN
        if out_h <= _CHECKOUT_WINDOW_H:
            return BookingStage.CHECKOUT
        return BookingStage.IN_STAY
    return BookingStage.POST_CHECKOUT


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionClassification:
    """Output triple of :meth:`DecisionClassifier.classify`.

    Attributes:
        stage: Inferred booking lifecycle stage.
        scenario: Inferred operational scenario.
        decision_type: Inferred decision action taxonomy value.
    """

    stage: BookingStage
    scenario: Scenario
    decision_type: DecisionType


# ---------------------------------------------------------------------------
# Scenario / decision-type classification — flag-driven + LLM hint.
#
# The legacy keyword tables (English fast-path + multilingual TR /
# RU / ES / DE / SK / NL / DA / CZ entries) were retired by the
# intelligent-classifier migration:
#
#   * ``BusinessFlags`` populated by
#     :class:`brain_engine.reasoning.business_classifier.
#     BusinessFlagClassifier` (LLM) drives the high-level scenario
#     bucket (noise / maintenance / complaint / discount / price /
#     additional-services / availability / invoice / navigation).
#   * ``BusinessFlags.scenario_hint`` and
#     ``BusinessFlags.decision_type_hint`` carry the LLM's
#     fine-grained pick for scenarios + decision_types that lack a
#     dedicated structured flag (access_code_release,
#     early_checkin, late_checkout, cancellation_request, etc.).
#   * :class:`brain_engine.patterns.intelligent_classifier.
#     IntelligentClassifier` composes lingua language detection +
#     fastembed multilingual MiniLM retrieval over the 469-scenario
#     foundation registry + an LLM final pick.  It populates the
#     hint fields above so the same machinery wins multilingual
#     coverage without a single hardcoded phrase.
#
# Tool-name fragments below remain because they classify by
# *operational identifier* (function names invoked during ReAct
# execution), not by guest-message language.  Adding new tool
# names is a routing concern, not a multilingual one.
# ---------------------------------------------------------------------------


# Lead-time threshold for the "too early" inquiry pattern.  Mümin's
# example uses 5 days; we round to 96 hours to absorb timezone
# variance at the 4-day boundary without leaking into the 24-hour /
# 4-hour PRE_ARRIVAL/CHECKIN cohorts already covered by Stage 8.1.
_EARLY_INQUIRY_THRESHOLD_HOURS: Final[float] = 96.0

# Scenarios eligible for promotion to ``EARLY_INQUIRY_IGNORED``.
# Limited to check-in / access flows because the Mümin pattern is
# about *timing* of access-related questions, not about generic
# discount or amenity asks.  Adding scenarios here is the safe
# extension point if the PM behaviour generalises.
_EARLY_INQUIRY_SCENARIOS: Final[frozenset[Scenario]] = frozenset(
    {
        Scenario.EARLY_CHECKIN,
        Scenario.ACCESS_CODE_RELEASE,
    }
)


# Tool-name fragments interpreted as decision-type signals.
_TOOL_RELEASE_HINTS: Final[tuple[str, ...]] = (
    "send_access_code",
    "release_access_code",
    "share_code",
)
_TOOL_DISPATCH_HINTS: Final[tuple[str, ...]] = (
    "dispatch",
    "create_task",
    "open_task",
    "schedule_cleaning",
    "send_technician",
)
_TOOL_FETCH_HINTS: Final[tuple[str, ...]] = (
    "fetch_",
    "get_",
    "lookup_",
    "pms_",
    "calendar_",
)
_TOOL_CHARGE_HINTS: Final[tuple[str, ...]] = (
    "charge_",
    "invoice_",
    "capture_payment",
)


# ---------------------------------------------------------------------------
# DecisionClassifier
# ---------------------------------------------------------------------------


class DecisionClassifier:
    """Deterministic (stage, scenario, decision_type) classifier.

    The classifier has no mutable state and performs no I/O, so a single
    instance can be safely shared across requests.  Callers construct the
    inputs from the active :class:`~brain_engine.conversation.models.PipelineState`.
    """

    def classify(
        self,
        *,
        business_flags: BusinessFlags,
        message_text: str,
        response_text: str = "",
        tools_used: tuple[str, ...] = (),
        reservation_id: str | None = None,
        check_in: str = "",
        check_out: str = "",
        current_time: str = "",
    ) -> DecisionClassification:
        """Classify one conversation turn.

        Args:
            business_flags: Flags produced by the business classifier.
            message_text: Cleaned guest message (may be empty).
            response_text: Assistant response text (may be empty).
            tools_used: Tool names invoked during ReAct execution.
            reservation_id: PMS reservation identifier, when present.
            check_in: Reservation check-in date (ISO 8601 yyyy-mm-dd /
                full timestamp).  Optional — empty falls back to
                keyword-based stage inference.
            check_out: Reservation check-out date (ISO 8601).
            current_time: Wall-clock at which the guest message was
                sent (ISO 8601).  When all three timestamps are present
                the stage is decided by date math instead of keywords:
                "question asked at May 14 with stay Apr 26-28" maps to
                POST_CHECKOUT no matter what the message says.

        Returns:
            A :class:`DecisionClassification` triple suitable for
            :class:`~brain_engine.patterns.case_builder.CaseBuilder`.
        """
        message = message_text.lower()
        response = response_text.lower()
        tool_names = tuple(t.lower() for t in tools_used)

        keyword_scenario = self._classify_scenario(business_flags, message)
        scenario = self._resolve_scenario_with_hint(
            business_flags, keyword_scenario,
        )
        stage = self._classify_stage(
            scenario=scenario,
            message=message,
            reservation_id=reservation_id,
            check_in=check_in,
            check_out=check_out,
            current_time=current_time,
        )
        keyword_decision = self._classify_decision_type(
            business_flags=business_flags,
            tools_used=tool_names,
            response=response,
        )
        decision_type = self._resolve_decision_type_with_hint(
            business_flags, keyword_decision,
        )
        # Aybüke Q3 telemetry: when both the LLM hint and the
        # keyword chain returned INFORM, this turn fell through to
        # the default action.  Emit a per-scenario counter so a
        # sustained spike per scenario surfaces the "non-EN message
        # family bypassing keyword tables" failure mode early.
        if (
            decision_type is DecisionType.INFORM
            and keyword_decision is DecisionType.INFORM
        ):
            _record_classifier_metric(
                "record_classifier_fallback_inform",
                scenario=scenario.value,
            )
        scenario = self._upgrade_to_early_ignored(
            scenario=scenario,
            decision_type=decision_type,
            check_in=check_in,
            current_time=current_time,
        )
        return DecisionClassification(
            stage=stage,
            scenario=scenario,
            decision_type=decision_type,
        )

    def classify_all(
        self,
        *,
        business_flags: BusinessFlags,
        message_text: str,
        response_text: str = "",
        tools_used: tuple[str, ...] = (),
        reservation_id: str | None = None,
        check_in: str = "",
        check_out: str = "",
        current_time: str = "",
    ) -> tuple[DecisionClassification, ...]:
        """Emit one classification per scenario detected in the thread.

        ali.md §3 spells out that one conversation thread can carry
        multiple operational decisions — the canonical example fans
        out to ``amenity_exception``, ``guest_count_mismatch`` and
        ``access_code_release`` from a single PM exchange.  Pre-P7
        the pipeline collapsed those into a single :class:`DecisionCase`
        because :meth:`classify` returns the *winning* scenario only,
        so the pattern miner saw two of three signals erased at the
        boundary.

        ``classify_all`` walks the same priority chain as
        :meth:`classify` but collects every additive scenario instead
        of returning at the first hit.  Mutually exclusive top-tier
        scenarios (DAMAGE / NOISE / LOST / MAINTENANCE / COMPLAINT)
        still emit at most one entry; lifecycle, access-window and
        additional-services scenarios stack.  When no specific
        scenario matches we fall back to a single-element tuple
        carrying the legacy :meth:`classify` result so callers always
        receive at least one classification.

        Args:
            business_flags: As :meth:`classify`.
            message_text: As :meth:`classify`.
            response_text: As :meth:`classify`.
            tools_used: As :meth:`classify`.
            reservation_id: As :meth:`classify`.
            check_in: As :meth:`classify`.
            check_out: As :meth:`classify`.
            current_time: As :meth:`classify`.

        Returns:
            A non-empty tuple of :class:`DecisionClassification`
            preserving the priority order used by :meth:`classify`.
            Single-scenario messages return a 1-tuple, matching the
            pre-P7 behaviour for callers that only inspect the first
            element.
        """
        message = message_text.lower()
        scenarios = self._detect_all_scenarios(
            flags=business_flags, message=message,
        )
        if not scenarios:
            # Fan-out came back empty — fall back to single-scenario
            # path so the LLM scenario_hint still gets a chance to
            # rescue the case from Scenario.GENERAL.
            return (
                self.classify(
                    business_flags=business_flags,
                    message_text=message_text,
                    response_text=response_text,
                    tools_used=tools_used,
                    reservation_id=reservation_id,
                    check_in=check_in,
                    check_out=check_out,
                    current_time=current_time,
                ),
            )

        response = response_text.lower()
        tool_names = tuple(t.lower() for t in tools_used)
        keyword_decision = self._classify_decision_type(
            business_flags=business_flags,
            tools_used=tool_names,
            response=response,
        )
        decision_type = self._resolve_decision_type_with_hint(
            business_flags, keyword_decision,
        )
        classifications: list[DecisionClassification] = []
        for scenario in scenarios:
            stage = self._classify_stage(
                scenario=scenario,
                message=message,
                reservation_id=reservation_id,
                check_in=check_in,
                check_out=check_out,
                current_time=current_time,
            )
            refined = self._upgrade_to_early_ignored(
                scenario=scenario,
                decision_type=decision_type,
                check_in=check_in,
                current_time=current_time,
            )
            classifications.append(
                DecisionClassification(
                    stage=stage,
                    scenario=refined,
                    decision_type=decision_type,
                ),
            )
        return tuple(classifications)

    # ------------------------------------------------------------------
    # Scenario inference
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_scenario(
        flags: BusinessFlags,
        message: str,
    ) -> Scenario:
        """Map :class:`BusinessFlags` to the most specific :class:`Scenario`.

        Scenario detection is now flag-driven + LLM-hint-driven:

        * Structured ``BusinessFlags`` (set by the upstream
          ``BusinessFlagClassifier`` LLM call) drive the top-tier
          buckets (noise / maintenance / complaint / discount / price
          / additional-services / availability).
        * Scenarios that lack a dedicated structured flag (
          ``ACCESS_CODE_RELEASE`` / ``EARLY_CHECKIN`` /
          ``LATE_CHECKOUT`` / ``CANCELLATION_REQUEST`` /
          ``BOOKING_EXTENSION`` / ``DAMAGE_REPORT`` / ``LOST_ITEM``
          / ``GUEST_COUNT_MISMATCH`` / ``MIN_STAY_EXCEPTION`` /
          ``PET_POLICY_EXCEPTION`` / ``PARKING_REQUEST`` /
          ``EXTRA_BED_REQUEST`` / ``AMENITY_EXCEPTION``) fall to
          :attr:`Scenario.GENERAL` here and rely on
          :meth:`_resolve_scenario_with_hint` to apply the LLM
          ``scenario_hint`` or
          :class:`~brain_engine.patterns.intelligent_classifier.
          IntelligentClassifier` output downstream.

        The ``message`` parameter is preserved for signature
        compatibility with downstream callers but is not consulted
        here — no string scanning.
        """
        if flags.is_noise_complaint:
            return Scenario.NOISE_COMPLAINT
        if (
            flags.is_security_issue
            or flags.is_maintenance_issue
            or flags.is_cleaning_issue
            or flags.is_emergency
        ):
            return Scenario.MAINTENANCE_REQUEST
        if flags.is_complaint:
            return Scenario.COMPLAINT_COMPENSATION
        if flags.is_discount_request:
            return Scenario.DISCOUNT_REQUEST
        if flags.is_price_related:
            return Scenario.PRICE_NEGOTIATION
        if flags.is_additional_services:
            return Scenario.SPECIAL_REQUEST
        if (
            flags.is_availability_related
            or flags.is_alternative_property_requested
            or flags.is_invoice_request
            or flags.is_navigation_query
        ):
            return Scenario.SPECIAL_REQUEST
        return Scenario.GENERAL

    @staticmethod
    def _resolve_scenario_with_hint(
        flags: BusinessFlags,
        keyword_scenario: Scenario,
    ) -> Scenario:
        """Apply LLM ``scenario_hint`` over the keyword chain result.

        Stage-2 LLM hint pathway with three defensive layers:

        1. **Feature-flag gate** — when
           ``BRAIN_LLM_HINTS_ENABLED=false``, return
           ``keyword_scenario`` immediately so the env var is a
           one-restart kill switch.
        2. **Enum validation** — invalid hints fall through to
           ``keyword_scenario`` and emit
           ``brain_decision_classifier_hint_invalid_total`` so the
           prompt drift is visible in Grafana.
        3. **Disagreement telemetry** — when the hint and the
           keyword chain both produce *different* non-GENERAL
           scenarios, emit
           ``brain_decision_classifier_hint_disagreement_total``
           but trust the hint (LLM has full multilingual context;
           keywords are EN-leaning).

        Empty hint (the BusinessFlagClassifier's default when the
        LLM omitted the field or the call failed) returns
        ``keyword_scenario`` — preserves pre-Stage-2 behaviour
        verbatim.
        """
        if not _llm_hints_enabled():
            return keyword_scenario
        raw = (flags.scenario_hint or "").strip().lower()
        if not raw:
            return keyword_scenario
        try:
            hint_scenario = Scenario(raw)
        except ValueError:
            _record_classifier_metric(
                "record_classifier_hint_invalid", raw_value=raw,
            )
            return keyword_scenario
        _record_classifier_metric(
            "record_classifier_hint_used",
            scenario=hint_scenario.value,
        )
        if (
            keyword_scenario is not Scenario.GENERAL
            and keyword_scenario is not hint_scenario
        ):
            _record_classifier_metric(
                "record_classifier_hint_disagreement",
                hint_scenario=hint_scenario.value,
                keyword_scenario=keyword_scenario.value,
            )
        return hint_scenario

    @staticmethod
    def _resolve_decision_type_with_hint(
        flags: BusinessFlags,
        keyword_decision: DecisionType,
    ) -> DecisionType:
        """Apply LLM ``decision_type_hint`` over the keyword chain.

        Same defensive pattern as
        :meth:`_resolve_scenario_with_hint`: feature-flag gate +
        enum validation + telemetry.  Disagreement is *not*
        emitted for decision_type because the keyword chain falls
        back to ``INFORM`` for everything it doesn't recognise —
        ``INFORM`` would always look like a "disagreement" and
        drown the signal.
        """
        if not _llm_hints_enabled():
            return keyword_decision
        raw = (flags.decision_type_hint or "").strip().lower()
        if not raw:
            return keyword_decision
        try:
            hint_decision = DecisionType(raw)
        except ValueError:
            _record_classifier_metric(
                "record_classifier_hint_invalid", raw_value=raw,
            )
            return keyword_decision
        _record_classifier_metric(
            "record_classifier_hint_used",
            scenario=f"decision:{hint_decision.value}",
        )
        return hint_decision

    @staticmethod
    def _detect_all_scenarios(
        *,
        flags: BusinessFlags,
        message: str,
    ) -> tuple[Scenario, ...]:
        """Return every scenario the flags detect, in priority order.

        Mirrors the priority chain in :meth:`_classify_scenario`
        — the first element is therefore guaranteed to equal the
        :meth:`classify` scenario, so callers that only read
        ``result[0]`` see no behaviour change.

        Multi-scenario fan-out is reserved for the additional-
        services bucket where the ``BusinessFlags`` shape can
        carry multiple concurrent intents.  Scenarios that used to
        rely on multilingual keyword detection now return an empty
        tuple here and are rescued by the LLM ``scenario_hint``
        pathway in the caller.
        """
        detected: list[Scenario] = []
        seen: set[Scenario] = set()

        def add(scenario: Scenario) -> None:
            if scenario not in seen:
                detected.append(scenario)
                seen.add(scenario)

        # Safety-critical exclusive tier — first hit wins.
        if flags.is_noise_complaint:
            add(Scenario.NOISE_COMPLAINT)
        elif (
            flags.is_security_issue
            or flags.is_maintenance_issue
            or flags.is_cleaning_issue
            or flags.is_emergency
        ):
            add(Scenario.MAINTENANCE_REQUEST)
        elif flags.is_complaint:
            add(Scenario.COMPLAINT_COMPENSATION)

        # Pricing exclusive group (discount preempts price).
        if flags.is_discount_request:
            add(Scenario.DISCOUNT_REQUEST)
        elif flags.is_price_related:
            add(Scenario.PRICE_NEGOTIATION)

        # Additional services — the multilingual sub-keyword fan-
        # out was retired; the LLM scenario_hint refines to PET /
        # PARKING / EXTRA_BED / AMENITY downstream.
        if flags.is_additional_services:
            add(Scenario.SPECIAL_REQUEST)

        # Pre-booking / informational fallback bucket.
        if not detected and (
            flags.is_availability_related
            or flags.is_alternative_property_requested
            or flags.is_invoice_request
            or flags.is_navigation_query
        ):
            add(Scenario.SPECIAL_REQUEST)

        return tuple(detected)

    # ------------------------------------------------------------------
    # Stage inference
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_stage(
        *,
        scenario: Scenario,
        message: str,
        reservation_id: str | None,
        check_in: str = "",
        check_out: str = "",
        current_time: str = "",
    ) -> BookingStage:
        """Map the (scenario, reservation_id, keywords) tuple to a stage.

        When ``check_in``, ``check_out`` and ``current_time`` are all
        present, the stage is decided by ISO-8601 string comparison
        (lexicographic ordering coincides with chronological ordering
        for ISO timestamps, so this is timezone-safe at the day
        granularity the classifier needs).  This short-circuits the
        keyword path so a guest message dated 15 days after checkout
        cannot be mis-classified as ``CHECKIN`` because it happens to
        contain the words "early check-in".

        Without dated context the stage is a prior, not a ground truth:
        the priority below favours scenario-implied stages (e.g.
        :attr:`Scenario.LATE_CHECKOUT`) and falls back to a safe
        ``IN_STAY`` default when a reservation exists and
        :attr:`PRE_BOOKING` otherwise.
        """
        # Date-aware short-circuit: when all three timestamps are
        # provided the stay window dominates.  We deliberately ignore
        # the scenario refinement here — the test team's sandbox UI
        # surfaces this exact case ("Message Sent Date" picker) and
        # expects deterministic behaviour.
        #
        # Beyond the coarse PRE_BOOKING / IN_STAY / POST_CHECKOUT
        # buckets the original revision used, the refined ladder
        # distinguishes the **proximity-to-arrival** stages the
        # Monday-2026-04-27 test team flagged: a wifi-credentials
        # message sent 24 h before check-in (PRE_ARRIVAL) must not
        # land in the same pattern bucket as one sent 4 h before
        # check-in (CHECKIN), because PMs answer them with
        # different scripts (e.g. address & code vs. live arrival
        # support).
        if check_in and check_out and current_time:
            windowed = classify_stage_by_window(
                message_sent_at=current_time,
                arrival_date=check_in,
                departure_date=check_out,
            )
            if windowed is not None:
                return windowed
            # Parsing failed for at least one timestamp — fall back to
            # the original lexicographic short-circuit so we never
            # regress callers that pass non-ISO strings.
            if current_time < check_in:
                return BookingStage.PRE_BOOKING
            if current_time <= check_out:
                return BookingStage.IN_STAY
            return BookingStage.POST_CHECKOUT

        # No reservation → the guest is almost certainly pre-booking.
        if not reservation_id:
            if scenario in {
                Scenario.DAMAGE_REPORT,
                Scenario.LOST_ITEM,
            }:
                # Mis-classified flag combinations can still land here —
                # keep the safer PRE_BOOKING prior.
                return BookingStage.PRE_BOOKING
            return BookingStage.PRE_BOOKING

        # With reservation — scenario disambiguates the stage.
        if scenario in {
            Scenario.CANCELLATION_REQUEST,
            Scenario.BOOKING_EXTENSION,
            Scenario.MIN_STAY_EXCEPTION,
            Scenario.GUEST_COUNT_MISMATCH,
        }:
            return BookingStage.MODIFICATION

        if scenario in {
            Scenario.EARLY_CHECKIN,
            Scenario.ACCESS_CODE_RELEASE,
        }:
            return BookingStage.CHECKIN

        if scenario == Scenario.LATE_CHECKOUT:
            return BookingStage.CHECKOUT

        if scenario == Scenario.LOST_ITEM:
            return BookingStage.POST_CHECKOUT

        if scenario in {
            Scenario.MAINTENANCE_REQUEST,
            Scenario.NOISE_COMPLAINT,
            Scenario.DAMAGE_REPORT,
            Scenario.COMPLAINT_COMPENSATION,
            Scenario.AMENITY_EXCEPTION,
            Scenario.EXTRA_BED_REQUEST,
            Scenario.PARKING_REQUEST,
            Scenario.PET_POLICY_EXCEPTION,
            Scenario.SPECIAL_REQUEST,
        }:
            return BookingStage.IN_STAY

        if scenario in {
            Scenario.DISCOUNT_REQUEST,
            Scenario.PRICE_NEGOTIATION,
        }:
            # Negotiation on an existing booking is part of review.
            return BookingStage.BOOKING_REVIEW

        # GENERAL / unknown with reservation: safe default.
        return BookingStage.IN_STAY

    # ------------------------------------------------------------------
    # EARLY_INQUIRY_IGNORED upgrade
    # ------------------------------------------------------------------

    @staticmethod
    def _upgrade_to_early_ignored(
        *,
        scenario: Scenario,
        decision_type: DecisionType,
        check_in: str,
        current_time: str,
    ) -> Scenario:
        """Promote check-in / access scenarios that were deferred too early.

        The Mümin "5 days before check-in is too early" pattern only
        makes sense when (a) the PM actually deferred and (b) the
        message arrived well outside the PRE_ARRIVAL window.  This
        upgrade keeps the original ``Scenario`` for non-deferred
        cases so the existing learned tiers do not move.

        Args:
            scenario: Scenario inferred by :meth:`_classify_scenario`.
            decision_type: Output of
                :meth:`_classify_decision_type`.  Only ``DEFER``
                triggers the upgrade.
            check_in: Reservation check-in timestamp (ISO 8601).
            current_time: Wall-clock at message time (ISO 8601).

        Returns:
            ``Scenario.EARLY_INQUIRY_IGNORED`` when every gate
            passes; the original ``scenario`` otherwise.
        """
        if decision_type is not DecisionType.DEFER:
            return scenario
        if scenario not in _EARLY_INQUIRY_SCENARIOS:
            return scenario
        ci = _parse_iso(check_in)
        now = _parse_iso(current_time)
        if ci is None or now is None:
            return scenario
        hours_before = (ci - now).total_seconds() / 3600.0
        if hours_before < _EARLY_INQUIRY_THRESHOLD_HOURS:
            return scenario
        return Scenario.EARLY_INQUIRY_IGNORED

    # ------------------------------------------------------------------
    # Decision-type inference
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_decision_type(
        *,
        business_flags: BusinessFlags,
        tools_used: tuple[str, ...],
        response: str,
    ) -> DecisionType:
        """Infer the :class:`DecisionType` from business flags + tool trail.

        Tool-name signals (RELEASE / DISPATCH / CHARGE /
        FETCH_LIVE_DATA) dominate because they record what the
        agent *actually did* during ReAct.  Silence (no response,
        no tool engagement) maps to :attr:`DecisionType.DEFER` so
        the pattern miner can learn "PM ignores at this lead-time
        / scenario" rather than fabricating an INFORM.

        Non-tool decision types (APPROVE / DENY / OFFER / QUOTE /
        ASK / DEFER) used to be inferred from English / multilingual
        keyword scans over the response text.  Those tables were
        retired; the LLM ``decision_type_hint`` populated by
        :class:`brain_engine.reasoning.business_classifier.
        BusinessFlagClassifier` and the
        :class:`~brain_engine.patterns.intelligent_classifier.
        IntelligentClassifier` rescue the value via
        :meth:`_resolve_decision_type_with_hint`.

        The ``response`` parameter is kept for signature
        compatibility but is not consulted.
        """
        if business_flags.is_emergency or business_flags.is_security_issue:
            return DecisionType.ESCALATE

        if _any_tool_matches(tools_used, _TOOL_RELEASE_HINTS):
            return DecisionType.RELEASE
        if _any_tool_matches(tools_used, _TOOL_DISPATCH_HINTS):
            return DecisionType.DISPATCH
        if _any_tool_matches(tools_used, _TOOL_CHARGE_HINTS):
            return DecisionType.CHARGE
        if _any_tool_matches(tools_used, _TOOL_FETCH_HINTS):
            return DecisionType.FETCH_LIVE_DATA

        # Silence — no response text and no tool engagement.
        # Recorded as DEFER so the pattern miner can learn "PM
        # ignores at this lead-time / scenario" rather than
        # fabricating an INFORM.  The response is only inspected
        # for empty-vs-non-empty; no keyword scanning.
        if not response.strip() and not tools_used:
            return DecisionType.DEFER
        return DecisionType.INFORM


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------




def _any_tool_matches(
    tool_names: tuple[str, ...],
    hints: tuple[str, ...],
) -> bool:
    """Return ``True`` when any tool name contains a hint fragment.

    Tool naming is not standardised across integrations, so substring
    matching is preferred over exact equality.
    """
    return any(any(hint in name for hint in hints) for name in tool_names)
