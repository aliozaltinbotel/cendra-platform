"""Assembles DecisionCases from conversation + PMS + calendar + ops data.

CaseBuilder is the single entry point for creating DecisionCase objects.
It joins the guest message with all relevant operational snapshots so
that each case captures the *full* context of a decision — not just the
conversation text.

Dependency injection: CaseBuilder receives a FeatureBuilder and a logger
at construction time.  It performs no I/O — all data is passed in by the
caller (typically ConversationService or an API endpoint).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from brain_engine.patterns.feature_builder import (
    FeatureBuilder,
)
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    Scenario,
)
from brain_engine.patterns.refusal_extractor import RefusalSignal
from brain_engine.patterns.scenario_derivation import (
    derive_scenario_from_foundation_slug,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Entity extraction patterns
# ---------------------------------------------------------------------------

_AMOUNT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:[$€£¥₺])\s*(\d[\d,]*\.?\d*)|(\d[\d,]*\.?\d*)\s*(?:USD|EUR|GBP|TRY|usd|eur|gbp|try)",
)
_GUEST_COUNT_PATTERN: re.Pattern[str] = re.compile(
    r"(\d+)\s*(?:guest|person|adult|child|kid|infant|baby|pet|dog|cat)",
    re.IGNORECASE,
)
_DATE_PATTERN: re.Pattern[str] = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}",
)
_NIGHT_PATTERN: re.Pattern[str] = re.compile(
    r"(\d+)\s*(?:night|nuit|noche|Nacht|gece)",
    re.IGNORECASE,
)
_PERCENTAGE_PATTERN: re.Pattern[str] = re.compile(
    r"(\d{1,3})%",
)

# Canonical amenity labels keyed by lowercase keyword.  The case
# builder normalises every variant — including TR, "Nespresso" brand
# names, and multi-word forms — to a single label so the
# ``ConditionSynthesizer`` can mine equality conditions on
# ``requested_amenity`` without fighting spelling variance.  Order
# matters only for tie-breaking; the longest keyword wins.
_AMENITY_LABEL_BY_KEYWORD: Final[tuple[tuple[str, str], ...]] = (
    ("coffee capsule", "coffee_capsule"),
    ("kahve kapsülü", "coffee_capsule"),
    ("coffee pod", "coffee_capsule"),
    ("nespresso", "coffee_capsule"),
    ("kapsül", "coffee_capsule"),
    ("kahve", "coffee"),
    ("coffee", "coffee"),
    ("breakfast", "breakfast"),
    ("kahvaltı", "breakfast"),
    ("towel", "towel"),
    ("havlu", "towel"),
    ("toiletries", "toiletries"),
    ("shampoo", "toiletries"),
    ("şampuan", "toiletries"),
    ("iron", "iron"),
    ("ütü", "iron"),
    ("hairdryer", "hairdryer"),
    ("saç kurutma", "hairdryer"),
    ("crib", "baby_crib"),
    ("baby bed", "baby_crib"),
    ("bebek yatağı", "baby_crib"),
)


# ---------------------------------------------------------------------------
# CaseBuilder
# ---------------------------------------------------------------------------


class CaseBuilder:
    """Constructs DecisionCase objects by joining all operational context.

    Responsibilities:
    - Extract entities from the guest message (amounts, counts, dates).
    - Build PMS and calendar snapshots with computed booking features.
    - Assemble the full DecisionCase ready for storage and learning.

    This class owns *assembly*, not *persistence*.  Storage is handled
    by DecisionCaseStore.

    Attributes:
        _feature_builder: Computes deterministic booking features.
        _log: Bound structured logger.
    """

    def __init__(self, feature_builder: FeatureBuilder) -> None:
        self._feature_builder = feature_builder
        self._log = logger.bind(component="case_builder")

    async def build(
        self,
        *,
        message_text: str,
        response_text: str,
        property_id: str,
        owner_id: str,
        stage: BookingStage,
        scenario: Scenario,
        decision_type: DecisionType,
        decision_params: dict[str, Any] | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        message_language: str = "en",
        pms_data: dict[str, Any] | None = None,
        calendar_data: dict[str, Any] | None = None,
        ops_data: dict[str, Any] | None = None,
        guest_data: dict[str, Any] | None = None,
        executed_actions: tuple[str, ...] = (),
        outcome: CaseOutcome | None = None,
        evidence_source_ids: tuple[str, ...] = (),
        source: CaseSource = CaseSource.LIVE,
        orchestrator_verdict: Mapping[str, Any] | None = None,
        pm_refusal_signals: tuple[RefusalSignal, ...] = (),
        decision_at: datetime | None = None,
        foundation_scenario_id: str | None = None,
        origin: PatternOrigin | None = None,
    ) -> DecisionCase:
        """Build a complete DecisionCase from raw operational data.

        All parameters use keyword-only syntax to prevent argument-order
        mistakes (there are many string fields).

        Args:
            message_text: Original guest or PM message.
            response_text: Engine response sent to the guest.
            property_id: PMS property identifier.
            owner_id: Property owner identifier.
            stage: Current booking lifecycle stage.
            scenario: Operational scenario classification.
            decision_type: Type of action taken or proposed.
            decision_params: Action-specific parameters.
            reservation_id: PMS reservation identifier.
            guest_id: Guest identifier.
            message_language: Detected language code.
            pms_data: Raw PMS reservation data.
            calendar_data: Raw calendar availability data.
            ops_data: Raw operational state (cleaning, maintenance).
            guest_data: Raw guest history data.
            executed_actions: IDs of executed tool actions.
            outcome: Observable outcome (may be filled later).
            evidence_source_ids: Memory/knowledge entry IDs used.
            source: Origin tag — defaults to ``LIVE``.  Historical
                replay callers (e.g. :class:`HistoricalCaseExtractor`)
                must pass ``CaseSource.HISTORICAL`` so the miner can
                down-weight bootstrap evidence.
            orchestrator_verdict: §10 priority-chain verdict captured
                at decision time.  ``None`` for legacy / orchestrator-
                disabled paths.  Stored verbatim on
                :attr:`DecisionCase.orchestrator_verdict` so pattern
                miners can attribute outcomes to the tier that fired.
            pm_refusal_signals: Pre-computed refusal signals mined
                from the PM ``response_text`` by
                :class:`RefusalExtractor`.  Folded into
                ``extracted_entities['refusal_signals']`` so the
                pattern miner can correlate refusal taxonomy with
                stage / scenario without re-running NLP at query
                time.  Empty tuple is the no-signal default.
            origin: Provenance trail to persist on the resulting
                :class:`DecisionCase`.  Mümin 2026-05-15 round-5 #3
                — bridging the FL-16 orchestrator's
                :class:`AnalysisResult.origin` onto the case so
                ``source_event_ids`` flows through the miner into
                :attr:`PatternRule.origin` (closes the empty
                ``/rules/{id}/origin.source_event_ids`` complaint).
                Callers that do not run the orchestrator (e.g. the
                bootstrap historical extractor) may either construct
                a :class:`PatternOrigin` from their own context or
                pass ``None`` to keep the default empty origin.

        Returns:
            Fully assembled DecisionCase.
        """
        pms = pms_data or {}
        calendar = calendar_data or {}
        ops = ops_data or {}
        guest = guest_data or {}

        entities = self._extract_entities(message_text, scenario)
        if pm_refusal_signals:
            entities["refusal_signals"] = [
                _serialise_refusal(signal) for signal in pm_refusal_signals
            ]
        pms_snapshot = self._build_pms_snapshot(
            pms,
            calendar=calendar,
            stage=stage,
            decision_at=decision_at,
        )
        calendar_snapshot = self._build_calendar_snapshot(calendar, pms)
        ops_snapshot = self._build_ops_snapshot(ops)
        guest_snapshot = self._build_guest_snapshot(guest)

        decision = DecisionAction(
            action_type=decision_type,
            params=decision_params or {},
        )

        # The legacy DecisionClassifier is keyword-regex on English
        # patterns — on multilingual traffic (Czech, Slovak, Polish,
        # Russian, Turkish) it falls to ``Scenario.GENERAL`` for ~95%
        # of cases.  When the Foundation matcher (FL-16) gave us a
        # precise slug, recover a coarser :class:`Scenario` from it
        # so the dashboard / Postman queries see real variety on
        # ``case.scenario`` (early_checkin, late_checkout,
        # extra_bed_request, maintenance_request, ...) instead of a
        # single bucket.  Only overrides ``GENERAL``; never changes
        # an enum the classifier was confident about.
        effective_scenario = scenario
        normalised_foundation_id = (
            foundation_scenario_id if foundation_scenario_id else None
        )
        if (
            scenario is Scenario.GENERAL
            and normalised_foundation_id is not None
        ):
            derived = derive_scenario_from_foundation_slug(
                normalised_foundation_id,
            )
            if derived is not None:
                effective_scenario = derived

        case_kwargs: dict[str, Any] = {
            "stage": stage,
            "scenario": effective_scenario,
            "property_id": property_id,
            "owner_id": owner_id,
            "reservation_id": reservation_id,
            "guest_id": guest_id,
            "message_text": message_text,
            "message_language": message_language,
            "extracted_entities": entities,
            "pms_snapshot": pms_snapshot,
            "calendar_snapshot": calendar_snapshot,
            "ops_snapshot": ops_snapshot,
            "guest_snapshot": guest_snapshot,
            "decision": decision,
            "response_text": response_text,
            "executed_actions": executed_actions,
            "outcome": outcome or CaseOutcome(),
            "evidence_source_ids": evidence_source_ids,
            "source": source,
            "orchestrator_verdict": dict(orchestrator_verdict or {}),
            "foundation_scenario_id": normalised_foundation_id,
            # Anchor the case on the real event time so the temporal KG
            # (fanout._record_kg reads ``decision_at or created_at``)
            # places historical replay cases on the archive timeline
            # instead of the extraction wall-clock.  ``None`` for live
            # callers that omit it — ``created_at`` ≈ event time there.
            "decision_at": decision_at,
        }
        # ``DecisionCase.origin`` defaults to an empty
        # :class:`PatternOrigin` via ``field(default_factory=...)``;
        # only override when the caller supplies one so callers that
        # never opt in keep their pre-PR-B behaviour byte-for-byte.
        if origin is not None:
            case_kwargs["origin"] = origin
        case = DecisionCase(**case_kwargs)

        self._log.info(
            "decision_case_built",
            case_id=case.case_id[:8],
            scenario=effective_scenario.value,
            stage=stage.value,
            property_id=property_id,
        )
        return case

    # -------------------------------------------------------------------
    # Entity extraction
    # -------------------------------------------------------------------

    def _extract_entities(
        self,
        message: str,
        scenario: Scenario,
    ) -> dict[str, Any]:
        """Parse structured entities from a free-text message.

        Extracts amounts, guest counts, dates, night counts, and
        percentages.  Scenario-specific extraction can be added by
        extending this method.

        Args:
            message: Raw message text.
            scenario: Current scenario (enables targeted extraction).

        Returns:
            Dict of entity_type → extracted values.
        """
        entities: dict[str, Any] = {}

        amounts = _extract_amounts(message)
        if amounts:
            entities["amounts"] = amounts

        guest_counts = _extract_guest_counts(message)
        if guest_counts:
            entities["guest_counts"] = guest_counts

        dates = _extract_dates(message)
        if dates:
            entities["dates"] = dates

        nights = _extract_nights(message)
        if nights is not None:
            entities["nights"] = nights

        percentages = _extract_percentages(message)
        if percentages:
            entities["percentages"] = percentages

        if scenario == Scenario.DISCOUNT_REQUEST and amounts:
            entities["discount_amount"] = amounts[0]
        elif scenario == Scenario.GUEST_COUNT_MISMATCH and guest_counts:
            entities["claimed_count"] = guest_counts[0]
        elif scenario == Scenario.AMENITY_EXCEPTION:
            label = _extract_amenity_label(message)
            if label is not None:
                entities["requested_amenity"] = label

        return entities

    # -------------------------------------------------------------------
    # Snapshot builders
    # -------------------------------------------------------------------

    def _build_pms_snapshot(
        self,
        pms_data: dict[str, Any],
        *,
        calendar: dict[str, Any] | None = None,
        stage: BookingStage | None = None,
        decision_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Build a normalised PMS snapshot from raw data.

        Selects only fields relevant for pattern learning and strips
        potentially sensitive data (credit card, full address).

        ``calendar`` and ``stage`` are optional inputs added so the
        snapshot can carry the *temporal* axes that
        :class:`~brain_engine.patterns.condition_synthesizer.ConditionSynthesizer`
        needs to discover patterns like "PM defers when ``lead_time_hours
        >= 120``, informs when ``< 48``" — the access-code-release
        question Mümin raised on 2026-05-05.  Without these fields in
        the snapshot the synthesiser's allowlist read at
        ``_flatten`` time has no way to surface the split as a
        candidate condition (the runtime ``FeatureBuilder`` exposes
        them, but the *mining* path consumes the case snapshot, not
        the runtime feature dict).  Both kwargs default to ``None``
        so older callers stay compatible — they just lose the
        temporal mining surface for that case.

        Args:
            pms_data: Raw PMS reservation data.
            calendar: Optional raw calendar dict; passed straight
                into :meth:`FeatureBuilder.build` so the computed
                ``lead_time_hours`` lands in the snapshot.  When
                ``None`` (or when the feature build raises) the
                temporal fields are omitted, never fabricated.
            stage: Optional :class:`BookingStage` of the decision —
                stored as the lowercase string so categorical
                synthesis can treat it as an ``eq`` candidate.

        Returns:
            Normalised PMS snapshot dict.
        """
        if not pms_data:
            return {}
        snapshot: dict[str, Any] = {
            "reservation_id": pms_data.get("reservation_id"),
            "status": pms_data.get("status"),
            "check_in": pms_data.get("check_in"),
            "check_out": pms_data.get("check_out"),
            "adults": pms_data.get("adults"),
            "children": pms_data.get("children"),
            "infants": pms_data.get("infants"),
            "total_price": pms_data.get("total_price"),
            "currency": pms_data.get("currency"),
            "source": pms_data.get("source"),
            "payment_status": pms_data.get("payment_status"),
            "property_id": pms_data.get("property_id"),
            "property_name": pms_data.get("property_name"),
            "listing_id": pms_data.get("listing_id"),
        }
        if stage is not None:
            snapshot["stage"] = stage.value
        # Lead-time computation only needs the PMS reservation
        # (``created_at`` + ``check_in``); calendar is consulted for
        # gap / occupancy fields that live in ``calendar_snapshot``,
        # not here.  Pass an empty dict when calendar is missing so
        # the FeatureBuilder still gets a runnable call shape.  The
        # ``now`` kwarg unlocks ``hours_before_checkin`` —
        # *proximity-to-check-in at the moment of decision*, which
        # answers Mümin's access-code-release question more precisely
        # than the lead time fixed at booking creation.
        #
        # Mümin 2026-05-11: for historical replay ``decision_at`` is
        # the message ``sent_at`` of the guest turn the case is
        # being built around — otherwise the snapshot dates
        # ``hours_before_checkin`` from extraction wall-clock,
        # which on a 2-month-old conversation produces values like
        # ``1012`` (~42 days) when the real lead time at the moment
        # the PM responded was ``144`` (~6 days).  Live callers
        # omit the kwarg and pick up the current wall-clock.
        reference_now = (
            decision_at if decision_at is not None else datetime.now(UTC)
        )
        try:
            features = self._feature_builder.build(
                pms_data,
                calendar or {},
                now=reference_now,
            )
        except Exception:
            self._log.debug(
                "case_builder.feature_build_failed",
                exc_info=True,
            )
        else:
            if features.lead_time_hours:
                snapshot["lead_time_hours"] = features.lead_time_hours
            if features.hours_before_checkin is not None:
                snapshot["hours_before_checkin"] = (
                    features.hours_before_checkin
                )
        return snapshot

    def _build_calendar_snapshot(
        self,
        calendar_data: dict[str, Any],
        pms_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build calendar snapshot enriched with computed features.

        Args:
            calendar_data: Raw calendar availability.
            pms_data: Raw PMS data (needed for feature computation).

        Returns:
            Calendar snapshot with computed features embedded.
        """
        if not calendar_data:
            return {}

        features = self._feature_builder.build(pms_data, calendar_data)
        return {
            "gap_before": features.gap_before_nights,
            "gap_after": features.gap_after_nights,
            "occupancy_7d": features.occupancy_7d,
            "occupancy_30d": features.occupancy_30d,
            "season": features.season,
            "same_day_turnover": features.same_day_turnover,
            "weekday_count": features.weekday_mix.weekday_count,
            "weekend_count": features.weekday_mix.weekend_count,
            "nights": features.nights,
            "adr": features.adr,
        }

    def _build_ops_snapshot(
        self,
        ops_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build normalised operational snapshot.

        Captures cleaning schedule, maintenance state, and any active
        operational flags.

        Args:
            ops_data: Raw ops data.

        Returns:
            Normalised ops snapshot dict.
        """
        if not ops_data:
            return {}
        return {
            "cleaning_status": ops_data.get("cleaning_status"),
            "cleaning_scheduled": ops_data.get("cleaning_scheduled"),
            "maintenance_pending": ops_data.get("maintenance_pending"),
            "maintenance_items": ops_data.get("maintenance_items", []),
            "last_inspection": ops_data.get("last_inspection"),
            "active_flags": ops_data.get("active_flags", []),
        }

    def _build_guest_snapshot(
        self,
        guest_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build normalised guest-history snapshot.

        Captures relevant guest attributes without leaking PII into
        pattern storage.

        Args:
            guest_data: Raw guest data.

        Returns:
            Normalised guest snapshot dict.
        """
        if not guest_data:
            return {}
        return {
            "total_bookings": guest_data.get("total_bookings", 0),
            "total_incidents": guest_data.get("total_incidents", 0),
            "language": guest_data.get("language"),
            "rating": guest_data.get("rating"),
            "id_verified": guest_data.get("id_verified", False),
            "tags": guest_data.get("tags", []),
            "is_repeat_guest": guest_data.get("total_bookings", 0) > 1,
        }


# ---------------------------------------------------------------------------
# Module-level extraction helpers
# ---------------------------------------------------------------------------


def _extract_amounts(text: str) -> list[float]:
    """Extract monetary amounts from text.

    Args:
        text: Message text.

    Returns:
        List of parsed amounts.
    """
    results: list[float] = []
    for match in _AMOUNT_PATTERN.finditer(text):
        raw = match.group(1) or match.group(2)
        if raw:
            cleaned = raw.replace(",", "")
            try:
                results.append(float(cleaned))
            except ValueError:
                continue
    return results


def _extract_guest_counts(text: str) -> list[int]:
    """Extract guest/person counts from text.

    Args:
        text: Message text.

    Returns:
        List of integer counts.
    """
    return [int(m.group(1)) for m in _GUEST_COUNT_PATTERN.finditer(text)]


def _extract_dates(text: str) -> list[str]:
    """Extract date strings from text.

    Args:
        text: Message text.

    Returns:
        List of raw date strings (not parsed — format varies).
    """
    return _DATE_PATTERN.findall(text)


def _extract_nights(text: str) -> int | None:
    """Extract a night count from text.

    Args:
        text: Message text.

    Returns:
        Integer night count or None if not found.
    """
    match = _NIGHT_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


def _extract_percentages(text: str) -> list[int]:
    """Extract percentage values from text.

    Args:
        text: Message text.

    Returns:
        List of integer percentages.
    """
    return [int(m.group(1)) for m in _PERCENTAGE_PATTERN.finditer(text)]


def _serialise_refusal(signal: RefusalSignal) -> dict[str, Any]:
    """Render a :class:`RefusalSignal` into JSON-safe dict form.

    Keys are deliberately defined in code (not in
    ``AI Pattern for Devlet Brain Engine.md``) — the design doc
    invented field names; persisted shape is owned by the patterns
    subsystem and decoupled from any external schema.

    Args:
        signal: Immutable refusal signal from
            :class:`RefusalExtractor`.

    Returns:
        Dict with refusal type, language, trigger phrase, optional
        conditional clause, and confidence score.  Suitable for
        direct JSONB storage on
        :attr:`DecisionCase.extracted_entities`.
    """
    return {
        "type": signal.refusal_type.value,
        "language": signal.language.value,
        "trigger": signal.trigger_phrase,
        "conditional": signal.conditional_clause,
        "confidence": signal.confidence,
    }


def _extract_amenity_label(text: str) -> str | None:
    """Return the canonical amenity label for ``text``.

    Scans the message (case-folded) against
    :data:`_AMENITY_LABEL_BY_KEYWORD` in order — the first match
    wins.  Multi-word keywords precede single-word ones in the
    table so ``"coffee capsule"`` does not collapse to plain
    ``"coffee"`` and lose the request specificity that
    :class:`ConditionSynthesizer` needs to mine the Mümin example.

    Args:
        text: Raw guest or PM message.

    Returns:
        Canonical amenity label (e.g. ``"coffee_capsule"``,
        ``"baby_crib"``) or ``None`` when no keyword matches.
        ``None`` is preferred over an empty string so callers can
        guard with ``if label is not None``.
    """
    if not text:
        return None
    folded = text.casefold()
    for keyword, label in _AMENITY_LABEL_BY_KEYWORD:
        if keyword in folded:
            return label
    return None
