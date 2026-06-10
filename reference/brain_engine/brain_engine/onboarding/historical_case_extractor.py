"""Turn an archived conversation into a single DecisionCase.

The extractor is pure compute: it joins an :class:`ArchivedConversation`
with a :class:`DecisionClassifier` and a :class:`CaseBuilder` to emit
one :class:`DecisionCase` per conversation.  It does **not** persist.
Persistence is the :class:`OnboardingService`'s responsibility so that
``dry_run`` mode can preview bootstrap volume without committing.

Conversations that lack either a guest message or a PM response are
skipped — there is nothing to learn from them.  Any builder/classifier
failure is wrapped in :class:`HistoricalExtractionError` with the full
cause chain preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

import structlog

from brain_engine.conversation.models import BusinessFlags
from brain_engine.integrations.unified_data import to_feature_dict
from brain_engine.onboarding.errors import HistoricalExtractionError
from brain_engine.onboarding.event_bus import SkipReason
from brain_engine.onboarding.models import ArchivedConversation
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.models import (
    CaseOutcome,
    CaseSource,
    DecisionCase,
    DecisionType,
    PatternOrigin,
)
from brain_engine.patterns.refusal_extractor import RefusalExtractor

__all__ = ["ExtractionOutcome", "HistoricalCaseExtractor"]


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """Result of one :meth:`HistoricalCaseExtractor.extract_with_reason` call.

    Pairs the extracted :class:`DecisionCase` (when present) with the
    structured :class:`SkipReason` that explains a ``None`` return.
    Consumers that only care about the case keep using
    :meth:`HistoricalCaseExtractor.extract`; the audit-log path uses
    this richer form so the bootstrap event bus can record *why* a
    conversation was dropped.
    """

    case: DecisionCase | None
    skip_reason: SkipReason | None


logger = structlog.get_logger(__name__)


# Historical conversations rarely carry structured flag signals.  The
# most neutral default tells the classifier "this concerns a property
# interaction" which keeps the scenario inference keyword-driven.
_DEFAULT_FLAGS_KWARGS: Final[dict[str, bool]] = {"is_property_related": True}


# Stateless, hot-path-safe singleton — :class:`RefusalExtractor` is a
# frozen dataclass with no I/O, so a single module-level instance is
# safe to share across the whole bootstrap loop.  Using a singleton
# avoids re-compiling the regex tables for every conversation.
_REFUSAL_EXTRACTOR: Final[RefusalExtractor] = RefusalExtractor()


# Decision-type → outcome mapping for archived PM responses.
# Without an explicit ``resolution_type`` the extractor would emit
# ``CaseOutcome()`` defaults, leaving ``has_outcome`` False and the
# pattern miner unable to mine any historical case
# (``DecisionCase.is_learnable`` requires ``has_outcome``).
#
# Mümin 2026-05-08 round-3 (complaint #C): a property-agnostic
# bug surfaced when 10 fake pre_booking messages with negative PM
# replies were bootstrapped against 323133 — the expected DENY
# rule never formed.  Same code path runs for every property, so
# the bug applies wherever PMs author refusals.  Root cause was
# this mapping marking DENY/BLOCK as ``successful=False`` →
# :meth:`CaseOutcome.is_negative_signal` returned True →
# :meth:`PatternExtractor._split_by_signal` routed those cases to
# the *counterexample* pool → the action-grouping loop never saw
# them, so a DENY rule was structurally impossible regardless of
# how many supporting cases existed.
#
# The fix treats every PM-authored historical decision as a
# successful PM action — what the PM did is captured by
# ``decision.action_type`` (DENY, INFORM, etc.), and the outcome
# records that the action was performed deliberately.  The miner
# can then form one rule per action group (INFORM rule, DENY
# rule, ASK rule, …) instead of conflating DENY with "PM
# overrode the engine".  Live override semantics are unchanged —
# real PM overrides set ``human_overrode=True`` upstream and are
# still routed to the negative pool by
# :meth:`CaseOutcome.is_negative_signal`.
#
# Resolution-type semantics:
#   - APPROVE / CHARGE / OFFER / RELEASE → PM_APPROVED (PM granted).
#   - DENY / BLOCK                       → PM_APPROVED (PM endorsed
#       their own deliberate refusal — the audit field
#       ``decision.action_type=DENY`` carries the "refuse" meaning).
#   - ESCALATE                           → ESCALATED (deferred to
#       human; not a successful automation outcome).
#   - INFORM / ASK / QUOTE / DEFER / DISPATCH / FETCH_LIVE_DATA
#                                        → AUTO_RESOLVED (advanced
#       the thread without a binary verdict).
#
# ``human_overrode`` stays False — the PM authored the response
# themselves, there's nothing to override.  ``CaseSource.HISTORICAL``
# already down-weights the case downstream so the miner does not
# treat replayed evidence on equal footing with a live observation.
def _outcome_for_historical(decision_type: DecisionType) -> CaseOutcome:
    """Materialise a learnable :class:`CaseOutcome` for a replayed case.

    Thin wrapper kept for backward compatibility with prior
    extractor callsites.  The actual derivation lives on
    :meth:`brain_engine.patterns.models.CaseOutcome.from_decision_type`
    so the historical and live ingest paths share one
    implementation — no two-source drift.
    """
    return CaseOutcome.from_decision_type(decision_type)


class HistoricalCaseExtractor:
    """Build one :class:`DecisionCase` per archived conversation.

    Parameters
    ----------
    case_builder:
        Pre-wired :class:`CaseBuilder` instance; the extractor performs
        no feature computation of its own.
    classifier:
        Deterministic stage/scenario/decision-type classifier.
    foundation_orchestrator:
        Optional :class:`FoundationAnalysisOrchestrator`.  When
        supplied, every archived conversation is pushed through the
        orchestrator and the dominant scenario id from the matcher is
        attached to the resulting :class:`DecisionCase` via the
        ``foundation_scenario_id`` field — which the pattern miner
        then propagates into :class:`PatternRule` and the FL-12
        ``/rules/{id}/origin`` endpoint surfaces.  Without it the
        extractor preserves its pre-W1 behaviour bit-for-bit:
        ``case.foundation_scenario_id`` stays ``None`` and origin
        trails come back empty.  Any orchestrator failure is logged
        at WARNING and swallowed — a flaky matcher must never block
        case extraction.
    """

    def __init__(
        self,
        *,
        case_builder: CaseBuilder,
        classifier: DecisionClassifier,
        foundation_orchestrator: Any = None,
    ) -> None:
        self._builder = case_builder
        self._classifier = classifier
        self._foundation_orchestrator = foundation_orchestrator
        self._log = logger.bind(component="historical_case_extractor")

    async def extract(
        self,
        conversation: ArchivedConversation,
    ) -> DecisionCase | None:
        """Return a DecisionCase or ``None`` when the thread is unusable.

        ``None`` is reserved for *expected* skips (no guest message, no
        PM reply).  Any *unexpected* failure inside the classifier or
        builder is re-raised as :class:`HistoricalExtractionError` so
        the orchestrator can count it as an error instead of a skip.
        """
        outcome = await self.extract_with_reason(conversation)
        return outcome.case

    async def extract_with_reason(
        self,
        conversation: ArchivedConversation,
    ) -> ExtractionOutcome:
        """Return the extracted case alongside the structured skip reason.

        Used by the audit-log path so the bootstrap event bus can
        emit ``CASE_SKIPPED`` events carrying the exact reason
        (``empty_thread``, ``no_guest_message``,
        ``no_pm_response_after_guest``) instead of just a counter.
        """
        guest = conversation.first_guest_message()
        pm = conversation.first_pm_response()
        if guest is None and pm is None:
            self._log.debug(
                "historical.skip_empty_thread",
                conversation_id=conversation.conversation_id,
            )
            return ExtractionOutcome(
                case=None,
                skip_reason=SkipReason.EMPTY_THREAD,
            )
        if guest is None:
            self._log.debug(
                "historical.skip_no_guest",
                conversation_id=conversation.conversation_id,
            )
            return ExtractionOutcome(
                case=None,
                skip_reason=SkipReason.NO_GUEST_MESSAGE,
            )
        if pm is None:
            self._log.debug(
                "historical.skip_no_pm",
                conversation_id=conversation.conversation_id,
            )
            return ExtractionOutcome(
                case=None,
                skip_reason=SkipReason.NO_PM_RESPONSE_AFTER_GUEST,
            )

        try:
            classification = self._classifier.classify(
                business_flags=BusinessFlags(**_DEFAULT_FLAGS_KWARGS),
                message_text=guest.text,
                response_text=pm.text,
                reservation_id=conversation.reservation_id or None,
                check_in=_iso_or_empty(conversation.arrival_date),
                check_out=_iso_or_empty(conversation.departure_date),
                current_time=_iso_or_empty(guest.sent_at),
            )
            pms_data = to_feature_dict(conversation.reservation_data or {})
            # Mine the PM response for implicit policy gates
            # (REQUIRES_DOCUMENT, REQUIRES_PAYMENT, HARD_BLOCK, ...).
            # Source text comes from the real ES
            # ``UnifiedMessage.body`` — derived signals are folded
            # into ``extracted_entities['refusal_signals']`` so the
            # pattern miner can correlate refusals with stage/
            # scenario without re-running NLP at query time.
            refusal_signals = _REFUSAL_EXTRACTOR.extract(pm.text)
            historical_outcome = _outcome_for_historical(
                classification.decision_type,
            )
            foundation_scenario_id = (
                await self._resolve_foundation_scenario_id(
                    text=guest.text,
                    property_id=conversation.property_id,
                    reservation_id=conversation.reservation_id or None,
                    guest_id=conversation.guest_id or None,
                    occurred_at=guest.sent_at,
                )
            )
            # Mümin 2026-05-15 round-5 #3 — the bootstrap path does
            # not invoke the FL-16 orchestrator, so build the
            # provenance trail by hand: the conversation_id is the
            # smallest upstream identifier we can quote on
            # :pyattr:`PatternRule.origin.source_event_ids` later,
            # and the resolved foundation slug (when present) seeds
            # ``foundation_scenario_ids``.
            foundation_origin = PatternOrigin(
                foundation_scenario_ids=(
                    (foundation_scenario_id,) if foundation_scenario_id else ()
                ),
                source_event_ids=(
                    (conversation.conversation_id,)
                    if conversation.conversation_id
                    else ()
                ),
            )
            case = await self._builder.build(
                message_text=guest.text,
                response_text=pm.text,
                property_id=conversation.property_id,
                owner_id=conversation.owner_id,
                stage=classification.stage,
                scenario=classification.scenario,
                decision_type=classification.decision_type,
                reservation_id=conversation.reservation_id or None,
                guest_id=conversation.guest_id or None,
                message_language=guest.language or "en",
                pms_data=pms_data or None,
                outcome=historical_outcome,
                source=CaseSource.HISTORICAL,
                pm_refusal_signals=refusal_signals,
                # Mümin 2026-05-11: anchor ``hours_before_checkin``
                # to when the guest actually sent the question so a
                # 2-month-old replay does not pollute the snapshot
                # with the extraction wall-clock lead time.
                decision_at=guest.sent_at,
                foundation_scenario_id=foundation_scenario_id,
                origin=foundation_origin,
            )
            return ExtractionOutcome(case=case, skip_reason=None)
        except Exception as exc:
            raise HistoricalExtractionError(
                str(exc) or exc.__class__.__name__,
                conversation_id=conversation.conversation_id,
                property_id=conversation.property_id,
            ) from exc

    async def _resolve_foundation_scenario_id(
        self,
        *,
        text: str,
        property_id: str,
        reservation_id: str | None,
        guest_id: str | None,
        occurred_at: datetime | None,
    ) -> str | None:
        """Run the FL-16 orchestrator on the guest message + dominant slug.

        Returns ``None`` when the orchestrator is not wired, the
        guest text is empty, the matcher finds nothing, or the
        orchestrator raises.  The historical extraction path must
        never fail because of a flaky matcher.
        """
        orchestrator = self._foundation_orchestrator
        if orchestrator is None:
            return None
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        # Local import to keep the analysis package out of the
        # extractor module's import graph for callers that exercise
        # ``HistoricalCaseExtractor`` without the orchestrator wired
        # (the InMemory test fixtures used in W5 / W9).
        from brain_engine.analysis.models import (
            AnalysisEvent,
            AnalysisEventType,
        )

        event = AnalysisEvent(
            event_id=str(uuid4()),
            event_type=AnalysisEventType.MESSAGE,
            property_id=property_id,
            occurred_at=occurred_at or datetime.now(UTC),
            text=cleaned,
            payload={},
            reservation_id=reservation_id,
            guest_id=guest_id,
        )
        try:
            result = await orchestrator.analyze(event)
        except Exception:
            self._log.warning(
                "historical.foundation_analysis_failed",
                conversation_property=property_id,
                exc_info=True,
            )
            return None
        match = getattr(result, "foundation_match", None)
        dominant = getattr(match, "dominant_scenario_id", None)
        return dominant if dominant else None


def _iso_or_empty(value: datetime | None) -> str:
    """Render a datetime as ISO-8601 or empty string.

    The classifier interprets an empty string as "no timestamp" and
    falls back to its keyword-driven stage prior, so the extractor
    must hand it ``""`` rather than ``"None"`` when a reservation row
    lacks the date.  Naive timestamps are passed through unchanged —
    the loader has already promoted them to UTC.
    """
    if value is None:
        return ""
    return value.isoformat()
