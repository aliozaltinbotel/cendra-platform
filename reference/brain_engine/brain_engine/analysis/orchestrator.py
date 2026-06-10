"""Foundation Analysis Orchestrator — pipeline driver (FL-16).

The orchestrator is the single entry point Brain Engine call sites
use to push an :class:`AnalysisEvent` through the Foundation Layer
pipeline.  In Sprint 2 the pipeline shape is:

    1. ``match_foundation`` — embedding-backed top-K retrieval
       against the parsed reactive foundation catalog (FL-01).
       The dominant slug is enriched with its full catalog entry so
       downstream steps read ``memory_type`` / ``should_auto_reply``
       without an extra round-trip.
    2. ``guardrail_stub`` — placeholder.  FL-05 fills it with the
       "auto-reply forbidden when foundation says No" check.
    3. ``mine_stub`` — placeholder.  FL-05 fills it with the
       "skip pattern miner when foundation says Learn Pattern: No"
       check.
    4. ``route_stub`` — placeholder.  FL-04 fills it with the
       memory-tier fan-out derived from the foundation's
       ``memory_type`` field.
    5. ``log_origin`` — real implementation already.  Builds a
       :class:`PatternOrigin` that the caller persists on the
       resulting :class:`DecisionCase` / :class:`PatternRule`.  The
       FL-12 ``/rules/{id}/origin`` API endpoint then renders the
       trail.

Calling the orchestrator is safe in Sprint 2 even without any of
the downstream stores wired: when ``scenario_matcher`` is ``None``
the match step returns an empty match, and every stub falls back
to the empty / no-op default.  The output is therefore always a
well-formed :class:`AnalysisResult` — never a partial mutation,
never an exception bubble.

Wiring contract:

* The orchestrator owns no I/O of its own — every collaborator is
  injected through the constructor and the orchestrator depends on
  Protocol-style facades rather than concrete classes.  This keeps
  the unit tests pure-Python and lets the future Sprint 3 wiring
  swap the matcher / catalog without touching the orchestrator.
* All steps are ``async`` so the FL-04 router can do I/O when it
  lands.  The Sprint 2 stubs return synchronous values but live
  inside ``async def`` so the signature does not change later.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisResult,
    FoundationMatch,
    FoundationMatchCandidate,
    memory_type_label_to_tier,
)
from brain_engine.analysis.required_data import (
    UNMAPPED,
    classify_required_data_check,
    find_missing_required_data,
)
from brain_engine.analysis.stage_contradiction import (
    derive_stage_from_calendar,
    detect_stage_mismatch,
    scenario_stage_from_catalog,
)
from brain_engine.patterns.models import PatternOrigin

if TYPE_CHECKING:
    from brain_engine.patterns.foundation_registry import (
        FoundationScenario,
    )


__all__ = [
    "FoundationAnalysisOrchestrator",
    "FoundationCatalogFacade",
    "ScenarioMatcherFacade",
]


logger = structlog.get_logger(__name__)


# ── Protocol facades — what the orchestrator depends on ───── #


@runtime_checkable
class ScenarioMatcherFacade(Protocol):
    """Subset of :class:`ScenarioMatcher` the orchestrator uses.

    Defined as a :class:`~typing.Protocol` so the unit tests can
    inject a hand-built stub without spinning up ``fastembed``.  The
    real :class:`brain_engine.patterns.scenario_matcher.ScenarioMatcher`
    satisfies the protocol through duck-typing — its ``top_k``
    method returns ``tuple[ScenarioCandidate, ...]`` whose attribute
    surface is identical to what we use here.
    """

    def top_k(
        self,
        text: str,
        *,
        k: int = 5,
    ) -> tuple[object, ...]:
        """Return at most ``k`` ranked scenario candidates.

        The orchestrator reads ``scenario_id`` and ``similarity``
        attributes off each returned candidate; it does NOT depend
        on ``text`` so the return type stays open-ended.
        """
        ...


@runtime_checkable
class FoundationCatalogFacade(Protocol):
    """Subset of :class:`FoundationCatalogStore` the orchestrator uses."""

    async def get(
        self,
        scenario_id: str,
    ) -> FoundationScenario | None:
        """Return one scenario from the catalog or ``None``."""
        ...


# ── orchestrator ──────────────────────────────────────────── #


_DEFAULT_MATCHER_K = 5
_DEFAULT_MIN_SIMILARITY = 0.45
_ENV_MIN_SIMILARITY = "FOUNDATION_MIN_SIMILARITY"


def _default_min_similarity() -> float:
    """Read ``FOUNDATION_MIN_SIMILARITY`` from the environment.

    Returns :data:`_DEFAULT_MIN_SIMILARITY` (``0.45``) when the
    variable is unset, blank, or cannot be parsed as a float.  An
    unparsable value logs a warning so misconfigured deploys are
    visible without crashing the pipeline — the orchestrator falls
    back to the safe default and keeps the request flowing.
    """
    raw = os.environ.get(_ENV_MIN_SIMILARITY)
    if raw is None or not raw.strip():
        return _DEFAULT_MIN_SIMILARITY
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "foundation_analysis.invalid_min_similarity "
            "raw=%s using_default=%.2f",
            raw,
            _DEFAULT_MIN_SIMILARITY,
        )
        return _DEFAULT_MIN_SIMILARITY


class FoundationAnalysisOrchestrator:
    """Drive an :class:`AnalysisEvent` through the Foundation pipeline.

    Construction is cheap and pure — the orchestrator owns no
    background tasks, no caches, and no thread-local state.  One
    instance can be shared across the FastAPI request lifetime; a
    fresh instance per process is also fine.

    Args:
        scenario_matcher: Optional :class:`ScenarioMatcherFacade`.
            When ``None`` the match step is a no-op and the
            resulting :class:`AnalysisResult` carries an empty
            :class:`FoundationMatch`.  Sprint 2 deploys can leave
            this unwired and still get a well-formed result with
            the FL-12 ``log_origin`` step populating the event id.
        foundation_catalog: Optional :class:`FoundationCatalogFacade`.
            When provided, the dominant match slug is enriched with
            the full :class:`FoundationScenario` so downstream
            steps (FL-04 routing, FL-05 gating) read the
            ``memory_type`` and ``should_*`` fields without a
            second store round-trip.
        matcher_top_k: Number of candidates to request from the
            matcher.  Defaults to ``5`` — enough to feed the FL-15
            LLM-iterative-questioning prompt without inflating the
            stored origin trail.
        min_similarity: Floor for the dominant candidate's cosine
            similarity (Q5-A — Foundation Layer similarity gate).
            When the matcher's top-ranked candidate sits below this
            value the orchestrator keeps the candidates tuple for
            observability but clears ``dominant_catalog_entry`` so
            the FL-05 guardrail / mine / FL-04 routing steps fall
            back to their safe defaults — they already short-circuit
            when no catalog entry is present.  ``None`` (the
            default) reads :data:`_ENV_MIN_SIMILARITY` from the
            process environment via :func:`_default_min_similarity`
            and falls back to :data:`_DEFAULT_MIN_SIMILARITY`
            (``0.45``) when unset.

    The pipeline is exposed exclusively via :meth:`analyze`; the
    pipeline-step methods are public-protected (single underscore)
    so the FL-04 / FL-05 PRs can override them by subclassing
    without touching the public surface.
    """

    __slots__ = (
        "_catalog",
        "_matcher",
        "_matcher_top_k",
        "_min_similarity",
    )

    def __init__(
        self,
        *,
        scenario_matcher: ScenarioMatcherFacade | None = None,
        foundation_catalog: FoundationCatalogFacade | None = None,
        matcher_top_k: int = _DEFAULT_MATCHER_K,
        min_similarity: float | None = None,
    ) -> None:
        if matcher_top_k <= 0:
            raise ValueError("matcher_top_k must be positive")
        self._matcher = scenario_matcher
        self._catalog = foundation_catalog
        self._matcher_top_k = matcher_top_k
        self._min_similarity = (
            _default_min_similarity()
            if min_similarity is None
            else min_similarity
        )

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        """Run ``event`` through the full Foundation pipeline.

        Each step is awaited sequentially: the foundation match
        feeds the guardrail stub, which feeds the mine stub, which
        feeds the route stub, which feeds the origin builder.
        Stubs short-circuit immediately in Sprint 2; downstream PRs
        gradually replace them with real logic.
        """
        match = await self._match_foundation(event)
        guardrail_block = await self._apply_guardrails(event, match)
        missing_required_data = await self._validate_required_data(
            event,
            match,
        )
        stage_mismatch_detail = await self._detect_stage_contradiction(
            event,
            match,
        )
        pattern_candidate = await self._mine_if_learnable(
            event,
            match,
            guardrail_block=guardrail_block,
            missing_required_data=missing_required_data,
        )
        memory_routes = await self._route_to_memory(event, match)
        origin = self._log_origin(event, match)
        logger.debug(
            "foundation_analysis.completed event_id=%s "
            "match_size=%d guardrail_block=%s "
            "pattern_candidate=%s routes=%d missing_data=%d "
            "stage_mismatch=%s",
            event.event_id,
            len(match.candidates),
            guardrail_block,
            pattern_candidate,
            len(memory_routes),
            len(missing_required_data),
            stage_mismatch_detail is not None,
        )
        return AnalysisResult(
            event_id=event.event_id,
            foundation_match=match,
            origin=origin,
            guardrail_block=guardrail_block,
            pattern_candidate_emitted=pattern_candidate,
            memory_routes=memory_routes,
            missing_required_data=missing_required_data,
            stage_mismatch=stage_mismatch_detail is not None,
            stage_mismatch_detail=stage_mismatch_detail or "",
        )

    # ── pipeline steps ────────────────────────────────── #

    async def _match_foundation(
        self,
        event: AnalysisEvent,
    ) -> FoundationMatch:
        """Run the embedding matcher + catalog enrichment.

        Returns an empty :class:`FoundationMatch` when the matcher
        is not wired or when ``event.text`` is blank.  The empty
        case is well-formed: downstream stubs accept it without a
        branch.
        """
        if self._matcher is None or not event.text.strip():
            return FoundationMatch()

        try:
            raw_candidates = self._matcher.top_k(
                event.text,
                k=self._matcher_top_k,
            )
        except (
            ValueError,
            RuntimeError,
            AttributeError,
        ) as exc:
            logger.warning(
                "foundation_analysis.match_failed event_id=%s error=%s",
                event.event_id,
                exc,
            )
            return FoundationMatch()

        if not raw_candidates:
            return FoundationMatch()

        candidates: list[FoundationMatchCandidate] = []
        dominant_catalog: FoundationScenario | None = None
        for index, raw in enumerate(raw_candidates):
            scenario_id = getattr(raw, "scenario_id", "")
            similarity = float(getattr(raw, "similarity", 0.0))
            if not scenario_id:
                continue
            catalog_entry = await self._fetch_catalog_entry(scenario_id)
            candidates.append(
                FoundationMatchCandidate(
                    scenario_id=scenario_id,
                    similarity=similarity,
                    catalog_entry=catalog_entry,
                ),
            )
            if index == 0:
                dominant_catalog = catalog_entry

        if not candidates:
            return FoundationMatch()

        # Q5-A — Foundation Layer similarity gate.  When the
        # top-ranked candidate sits below the configured floor we
        # drop ``dominant_catalog_entry`` so the downstream
        # guardrail / mine / route steps (which already short-circuit
        # on a missing catalog entry) fall back to their safe
        # defaults.  The candidates tuple stays populated for
        # observability — the FL-12 origin trail keeps recording the
        # full slug list so weak matches remain auditable without
        # influencing policy.
        dominant_similarity = candidates[0].similarity
        if dominant_similarity < self._min_similarity:
            logger.info(
                "foundation_analysis.below_threshold "
                "event_id=%s dominant_scenario_id=%s "
                "dominant_similarity=%.3f threshold=%.3f",
                event.event_id,
                candidates[0].scenario_id,
                dominant_similarity,
                self._min_similarity,
            )
            dominant_catalog = None

        return FoundationMatch(
            candidates=tuple(candidates),
            dominant_scenario_id=candidates[0].scenario_id,
            dominant_catalog_entry=dominant_catalog,
        )

    async def _apply_guardrails(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
    ) -> bool:
        """Foundation-driven auto-reply guardrail (FL-05).

        Reads ``match.dominant_catalog_entry.should_auto_reply`` and
        returns ``True`` when the foundation forbids auto-reply
        (``"No"``).  Conditional / unspecified entries do NOT block
        — the orchestrator is a *one-way gate* in Sprint 3: it only
        forces a block when the catalog is explicit about denial,
        so existing call sites that did not consult the foundation
        keep their current behaviour unless the catalog is explicit.

        Blocking outcomes propagate to
        ``AnalysisResult.guardrail_block`` so callers
        (conversation/service.py FL-05b wiring) can switch the
        action from auto-send to draft / escalate.  Until that
        wiring lands, the field is observation-only.
        """
        del event  # kept for FL-05b wiring (per-event override)
        catalog_entry = match.dominant_catalog_entry
        if catalog_entry is None:
            return False
        decision = catalog_entry.should_auto_reply.strip().lower()
        return decision == "no"

    async def _validate_required_data(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
    ) -> tuple[str, ...]:
        """Foundation Layer Q5-B — required-data presence gate.

        Walks the dominant catalog entry's
        ``required_data_checks`` and reports the verbatim labels
        whose target snapshot bucket is empty on the event.

        Returns an empty tuple in three situations:

        * No dominant catalog entry (matcher empty, catalog
          unwired, Q5-A similarity gate trip).  Nothing to
          validate.
        * Dominant entry carries no ``required_data_checks``.
          Foundation explicitly demanded nothing for this
          scenario.
        * Every required check either maps to a snapshot bucket
          that has data on the event, or falls into the
          ``UNMAPPED`` category (knowledge / policy / memory-tier
          references that the Q5-B.2 follow-up will wire to
          MemoryTier loaders).

        Unmapped labels are logged at INFO so production traces
        surface which knowledge-tier checks the catalog uses
        most often — that data drives Q5-B.2 prioritisation.

        The returned tuple lands on
        :pyattr:`AnalysisResult.missing_required_data` for
        observability *and* short-circuits
        :meth:`_mine_if_learnable` — a scenario whose required
        data is physically missing should not seed pattern rules.
        """
        catalog_entry = match.dominant_catalog_entry
        if catalog_entry is None:
            return ()
        if not catalog_entry.required_data_checks:
            return ()
        snapshots: dict[str, dict[str, Any]] = {
            "pms_snapshot": event.pms_snapshot,
            "calendar_snapshot": event.calendar_snapshot,
            "ops_snapshot": event.ops_snapshot,
            "guest_snapshot": event.guest_snapshot,
        }
        missing = find_missing_required_data(
            catalog_entry.required_data_checks,
            snapshots,
        )
        unmapped = tuple(
            check
            for check in catalog_entry.required_data_checks
            if classify_required_data_check(check) == UNMAPPED
        )
        if unmapped:
            logger.info(
                "foundation_analysis.required_data_unmapped "
                "event_id=%s scenario_id=%s unmapped_count=%d",
                event.event_id,
                catalog_entry.scenario_id,
                len(unmapped),
            )
        if missing:
            logger.info(
                "foundation_analysis.required_data_missing "
                "event_id=%s scenario_id=%s missing_count=%d "
                "missing_labels=%s",
                event.event_id,
                catalog_entry.scenario_id,
                len(missing),
                missing,
            )
        return missing

    async def _detect_stage_contradiction(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
    ) -> str | None:
        """Foundation Layer Q5-C — stage contradiction detection.

        Compares the booking stage implied by the event's
        calendar (``check_in`` / ``check_out`` / ``current_time``
        on ``calendar_snapshot``) with the booking stage the
        matched scenario expects.  Returns a short detail string
        when the two disagree, or ``None`` when there is no
        contradiction to surface.

        Returns ``None`` in three situations:

        * No dominant catalog entry (Q5-A cleared, matcher
          empty, catalog unwired).  Nothing to compare against.
        * Calendar snapshot lacks ``check_in`` or ``check_out``.
          The caller didn't carry calendar context — Q5-C
          cannot decide without it (Q5-B.1 wiring concern).
        * Stages match exactly or are an adjacent compatible
          pair (e.g. PRE_ARRIVAL ↔ CHECKIN, the normal arrival
          transition).

        When a hard mismatch is found (e.g. calendar=POST_CHECKOUT
        + scenario=PRE_ARRIVAL — Mümin's classic adversarial
        test), the detail string is logged at INFO so production
        traces surface how often these contradictions appear,
        and returned to the caller for landing on
        :pyattr:`AnalysisResult.stage_mismatch_detail`.

        **Variant A — observation only.**  This method MUST NOT
        gate the guardrail / mine / route steps.  A Q5-C Variant
        B follow-up may add a mining gate once production data
        tells us the legitimate-mismatch rate.
        """
        catalog_entry = match.dominant_catalog_entry
        if catalog_entry is None:
            return None
        calendar = event.calendar_snapshot or {}
        check_in = calendar.get("check_in") or calendar.get("check_in_date")
        check_out = calendar.get("check_out") or calendar.get(
            "check_out_date",
        )
        if not check_in or not check_out:
            return None
        current_time = (
            calendar.get("current_time")
            or calendar.get("message_time")
            or calendar.get("now")
        )
        calendar_stage = derive_stage_from_calendar(
            check_in=str(check_in),
            check_out=str(check_out),
            current_time=(
                str(current_time) if current_time is not None else None
            ),
        )
        scenario_stage = scenario_stage_from_catalog(catalog_entry)
        detail = detect_stage_mismatch(calendar_stage, scenario_stage)
        if detail is not None:
            logger.info(
                "foundation_analysis.stage_mismatch "
                "event_id=%s scenario_id=%s detail=%s",
                event.event_id,
                catalog_entry.scenario_id,
                detail,
            )
        return detail

    async def _mine_if_learnable(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
        *,
        guardrail_block: bool,
        missing_required_data: tuple[str, ...] = (),
    ) -> bool:
        """Foundation-driven pattern-mining gate (FL-05 + Q5-B).

        Returns ``True`` when the dominant foundation entry says
        ``Should AI Learn Pattern: Yes`` *and* the Q5-B required-
        data gate found no missing snapshot data.  Returns
        ``False`` in four situations:

        * The entry explicitly forbids learning (``"No"``).  Sprint
          3 verifies this against the 17 do-not-learn scenarios
          pinned by the FL-01 test — including the six pure-safety
          Critical entries (gas smell, broken glass, medical,
          safety/security, CO alarm, post-stay injury) that must
          never become reusable rules.
        * No dominant catalog entry exists (matcher empty / catalog
          unwired).  The orchestrator stays conservative: without
          foundation context we do not promote a learning
          candidate from this layer.
        * The earlier ``guardrail_block`` short-circuits the check
          so a blocked event never emits a learning candidate even
          if the foundation says learning is allowed.  Safety beats
          learnability when they conflict.
        * The Q5-B presence gate found at least one mappable
          ``required_data_checks`` label whose target snapshot was
          empty on the event.  A scenario whose physical
          prerequisites are missing should not seed rules — the
          extracted condition surface would be confounded by the
          gap.

        Unmapped labels (knowledge / policy / memory-tier
        categories) intentionally do NOT gate here — they are
        observed for Q5-B.2 and would otherwise block ~half of
        all catalog entries.
        """
        del event
        if guardrail_block:
            return False
        if missing_required_data:
            return False
        catalog_entry = match.dominant_catalog_entry
        if catalog_entry is None:
            return False
        decision = catalog_entry.should_learn_pattern.strip().lower()
        return decision == "yes"

    async def _route_to_memory(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
    ) -> tuple[str, ...]:
        """Translate the foundation entry's Memory Type labels (FL-04).

        Reads ``match.dominant_catalog_entry.memory_types`` (a tuple
        of the verbatim labels parsed from the foundation markdown)
        and maps each one to a :class:`MemoryTier` slug.  The result
        is the deduplicated tuple of slugs in the order the catalog
        listed them — preserved so the FL-04b wiring PR (extends
        :class:`MemoryFanOut` to write tier-aware) can mirror the
        canonical ranking when one tier is preferred over another.

        Empty / no-catalog cases return ``()`` so existing callers
        that ignore ``AnalysisResult.memory_routes`` keep working.
        An unknown label (e.g. a catalog typo after a MD edit) is
        logged at WARNING and skipped — better than smuggling a
        bad slug into the routes tuple.
        """
        del event  # currently unused; FL-04b will read property_id
        catalog_entry = match.dominant_catalog_entry
        if catalog_entry is None:
            return ()

        tiers: list[str] = []
        seen: set[str] = set()
        for label in catalog_entry.memory_types:
            tier = memory_type_label_to_tier(label)
            if tier is None:
                logger.warning(
                    "foundation_analysis.unknown_memory_type label=%s "
                    "scenario_id=%s",
                    label,
                    catalog_entry.scenario_id,
                )
                continue
            slug = tier.value
            if slug in seen:
                continue
            seen.add(slug)
            tiers.append(slug)
        return tuple(tiers)

    def _log_origin(
        self,
        event: AnalysisEvent,
        match: FoundationMatch,
    ) -> PatternOrigin:
        """Build the provenance trail (FL-12 — real implementation).

        Records:

        * ``foundation_scenario_ids`` — every slug the matcher
          returned, ordered by descending similarity (the dominant
          first).  Cross-scenario rules thus carry every contributor
          and the FL-13 update-feedback fan-out can walk the whole
          list.
        * ``source_event_ids`` — currently a one-element tuple
          ``(event.event_id,)``.  Future PRs that batch multiple
          upstream events into one decision (e.g. correlated PMS +
          OTA events) will extend this list.
        * ``contributing_signal_ids`` — empty in Sprint 2.  FL-09
          (deferred Proactive layer) will populate it once the
          :class:`ProactiveSignal` store lands.
        """
        foundation_ids: tuple[str, ...] = tuple(
            candidate.scenario_id for candidate in match.candidates
        )
        return PatternOrigin(
            foundation_scenario_ids=foundation_ids,
            source_event_ids=(event.event_id,),
            contributing_signal_ids=(),
        )

    # ── helpers ───────────────────────────────────────── #

    async def _fetch_catalog_entry(
        self,
        scenario_id: str,
    ) -> FoundationScenario | None:
        """Return the foundation catalog row for ``scenario_id``.

        Tolerates a missing catalog store (returns ``None``) and an
        ``await`` error from the store (logs + returns ``None``) so
        a flaky catalog never crashes the pipeline.
        """
        if self._catalog is None:
            return None
        try:
            fetched: Awaitable[FoundationScenario | None] = self._catalog.get(
                scenario_id
            )
            return await fetched
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.warning(
                "foundation_analysis.catalog_fetch_failed scenario_id=%s error=%s",
                scenario_id,
                exc,
            )
            return None
