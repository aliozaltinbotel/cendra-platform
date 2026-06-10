# Pre-existing Russian docstring in ``_step6_mira_ready`` contains
# Cyrillic letters intentionally.
"""Nightly Consolidator — Memory + Skill evolution cycle (NO training/fine-tuning).

Based on: research29.md Three-Layer Defense:
    Inner:  Frozen weights (no catastrophic forgetting)
    Middle: External memory evolution (skills + knowledge)
    Outer:  Guardrails + monitoring

Nightly cycle (5 steps):
    1. Memory Consolidation — promote high-value episodic to semantic,
       run forgetting curve decay, deduplicate
    2. Skill Evolution — aggregate day's failures, run REFLECT->WRITE
       for each failure pattern, batch evolve skills
    3. Preference Aggregation — analyze owner approval patterns,
       create/update stable rules in ProceduralMemory
    4. Knowledge Graph Update — extract entities from today's episodes,
       add facts/beliefs/relationships to KG
    5. Skill Pruning — deactivate low-confidence, unused, zero-success skills

Monthly cycle (4 steps):
    1. Accuracy evaluation — grader scores, intervention rates
    2. City maturity check — NEW -> LEARNING -> MATURE
    3. Deep procedural cleanup — stricter thresholds
    4. Report generation
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from core.brain.evaluation.golden_cases_runner import (
    GoldenCasesRunner,
    golden_cases_enabled,
)
from core.brain.memory.contradiction_detector import (
    ContradictionDetector,
    Resolution,
)
from core.brain.memory.episodic_dedup import (
    EpisodeRecord,
    EpisodicDedupConsolidator,
)
from core.brain.memory.fact_store import FactStore, StoredFact
from core.brain.memory.kg_deterministic_sync import (
    DeterministicKGSync,
    deterministic_sync_enabled,
    llm_extraction_enabled,
)
from core.brain.patterns.extractor import PatternExtractor
from core.brain.patterns.foundation_update import (
    FoundationUpdateStore,
    detect_foundation_drift,
)
from core.brain.patterns.store import DecisionCaseStore, PatternRuleStore
from core.brain.patterns.validator import PatternValidator

logger = logging.getLogger(__name__)

# Nightly consolidation parameters (from Blueprint v5)
_PROMOTE_THRESHOLD = 0.7  # surprise score to promote to semantic
_DECAY_FACTOR = 0.95  # daily confidence decay
_MAX_EPISODIC_AGE_DAYS = 30  # max age for episodic entries

# Nightly pruning parameters
_PRUNE_CONFIDENCE = 0.15  # deactivate below this
_PRUNE_UNUSED_DAYS = 60  # deactivate if unused this long
_PRUNE_ZERO_SUCCESS_CONF = 0.3  # zero-success + below this = prune

# Monthly pruning (stricter)
_MONTHLY_PRUNE_CONFIDENCE = 0.2
_MONTHLY_PRUNE_AGE_DAYS = 180

# Preference aggregation
_MIN_APPROVALS_FOR_RULE = 3  # need 3+ consistent approvals to create rule
_APPROVAL_CONSISTENCY = 0.8  # 80% same decision = stable rule

# Episodic dedup (advisory §7.3 — fold near-duplicate episodes into a
# single representative summary).  Off by default so the existing
# 7-step nightly cycle stays bit-for-bit identical until a deploy
# explicitly opts in.
_EPISODIC_DEDUP_ENV: Final[str] = "BRAIN_EPISODIC_DEDUP_ENABLED"
_EPISODIC_DEDUP_BATCH = 200  # episodes scanned per nightly run


def _episodic_dedup_enabled() -> bool:
    """Whether step 1 folds duplicate episodes via EpisodicDedupConsolidator.

    Read on every nightly run so a deploy can flip the flag without
    restart. Default off.
    """
    raw = os.environ.get(_EPISODIC_DEDUP_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Contradiction check before fact storage (advisory §7.4 — newly
# extracted facts must not silently overwrite an opposing fact that
# was true earlier).  Off by default so the existing extract→store
# path stays bit-for-bit identical until a deploy explicitly opts in.
_CONTRADICTION_CHECK_ENV: Final[str] = "BRAIN_CONTRADICTION_CHECK_ENABLED"


def _contradiction_check_enabled() -> bool:
    """Whether step 1 runs each new fact through ContradictionDetector.

    Read on every nightly run so a deploy can flip the flag without
    restart. Default off.
    """
    raw = os.environ.get(_CONTRADICTION_CHECK_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


class NightlyConsolidator:
    """Orchestrates nightly and monthly consolidation cycles.

    NO training or fine-tuning. Only external memory and skill evolution.

    Args:
        memory: Full memory system (MemorySystem).
        skills: Skill evolution engine.
        recorder: Interaction recorder.
        grader: APM grader.
    """

    def __init__(
        self,
        memory: Any,
        skills: Any,
        recorder: Any,
        grader: Any,
        fact_store: FactStore | None = None,
        mem0_extractor: Any = None,
        case_store: DecisionCaseStore | None = None,
        rule_store: PatternRuleStore | None = None,
        pattern_extractor: PatternExtractor | None = None,
        pattern_validator: PatternValidator | None = None,
        golden_cases_runner: GoldenCasesRunner | None = None,
        dedup_consolidator: EpisodicDedupConsolidator | None = None,
        contradiction_detector: ContradictionDetector | None = None,
        deterministic_kg_sync: DeterministicKGSync | None = None,
        foundation_update_store: FoundationUpdateStore | None = None,
    ) -> None:
        self._memory = memory
        self._skills = skills
        self._recorder = recorder
        self._grader = grader
        self._fact_store = fact_store
        self._mem0 = mem0_extractor
        self._case_store = case_store
        self._rule_store = rule_store
        self._pattern_extractor = pattern_extractor
        self._pattern_validator = pattern_validator or PatternValidator()
        self._golden_cases_runner = golden_cases_runner
        self._dedup_consolidator = dedup_consolidator
        self._contradiction_detector = contradiction_detector
        self._contradiction_stats: dict[str, int] = {
            "checked": 0,
            "contradictions": 0,
            "newer_wins": 0,
            "flagged_for_pm": 0,
            "skipped": 0,
        }
        # Task 7 of CLAUDE_CODE_WIRING_FIX_PLAN.md — when injected,
        # ``_step4_update_knowledge_graph`` syncs DecisionCases into
        # the temporal knowledge graph deterministically (no LLM
        # tokens) by default.  ``None`` keeps the pre-Task-7 path —
        # the legacy LLM consolidation runs as before, preserving
        # backward compatibility for deployments that have not yet
        # wired the new sync.
        self._deterministic_kg_sync = deterministic_kg_sync
        # Sprint 6 W8 — when injected, ``_step9_detect_foundation_drift``
        # walks recent DecisionCases through
        # :func:`detect_foundation_drift` and persists every produced
        # :class:`FoundationUpdateCandidate` into this store so a
        # human reviewer can triage them.  ``None`` keeps the
        # nightly cycle behaviour identical to pre-W8.
        self._foundation_update_store = foundation_update_store

    # ── Nightly Cycle ───────────────────────────────────────────────── #

    def run_nightly(self) -> dict[str, Any]:
        """Run the full nightly consolidation cycle.

        Returns:
            Stats dict with details for each of the 6 steps.
        """
        logger.info("=== Nightly Consolidation Started ===")
        stats: dict[str, Any] = {}

        stats["step1_memory"] = self._step1_consolidate_memory()
        stats["step2_skills"] = self._step2_evolve_skills()
        stats["step3_preferences"] = self._step3_aggregate_preferences()
        stats["step4_knowledge_graph"] = self._step4_update_knowledge_graph()
        stats["step5_pruning"] = self._step5_prune_skills()
        # Step 6: фиксируем готовность MIRA. Прямого пуллинга pending
        # кандидатов из nightly пока нет — кандидаты приходят через
        # /knowledge/sync или /mira/process. Этот шаг здесь как hook
        # для будущей интеграции (nightly-fetch из Cendra DB).
        stats["step6_mira"] = self._step6_mira_ready()
        stats["step7_patterns"] = self._step7_extract_patterns()
        # Step 8: LLM-as-judge evaluation on yesterday's DecisionCases.
        # No-op when no runner is injected or BRAIN_GOLDEN_CASES_ENABLED
        # is off — keeps the default deploy from spending judge tokens.
        if self._golden_cases_runner is not None and golden_cases_enabled():
            stats["step8_evaluation"] = self._step8_run_evaluation()
        # Step 9: Sprint 6 W8 — foundation drift detection.  No-op
        # when no ``foundation_update_store`` is wired so the
        # default deploy preserves pre-W8 nightly behaviour.
        stats["step9_foundation_drift"] = self._step9_detect_foundation_drift()

        logger.info("=== Nightly Consolidation Complete: %s ===", stats)
        return stats

    def _step8_run_evaluation(self) -> dict[str, Any]:
        """Run LLM-as-judge evaluation on recent DecisionCases.

        Wraps :meth:`GoldenCasesRunner.run_daily` in a try/except so a
        judge or store failure cannot break the nightly cycle — the
        other 7 steps must remain idempotent and isolated.
        """
        try:
            assert self._golden_cases_runner is not None
            report = self._golden_cases_runner.run_daily()
            return {
                "sample_size": report.sample_size,
                "pm_match_rate": report.pm_match_rate,
                "hallucination_rate": report.hallucination_rate,
                "avg_score": report.avg_score,
                "failed_cases": report.failed_cases,
                "duration_seconds": report.duration_seconds,
            }
        except Exception:
            logger.error("Step 8 (evaluation) failed", exc_info=True)
            return {"error": "evaluation_failed"}

    # ── Step 1: Memory Consolidation + Fact Extraction ──────────────── #

    def _step1_consolidate_memory(self) -> dict[str, Any]:
        """Promote episodic -> semantic, extract facts via Mem0, run decay.

        Enhanced in Phase 2 (Task 2.5):
          1. Original consolidation (promote high-value episodic to semantic)
          2. Mem0 batch fact extraction from today's conversations
          3. Dedup against FactStore (similarity > 0.92 = duplicate)
          4. Store new facts in dedicated Qdrant collection
          5. Apply confidence decay to skills

        Returns:
            Consolidation statistics including fact extraction metrics.
        """
        stats: dict[str, Any] = {}

        # Original consolidation (unchanged)
        try:
            result = self._memory.consolidator.consolidate(force=True)
            stats["consolidation"] = result
        except Exception:
            logger.error("Step 1: memory consolidation failed", exc_info=True)
            stats["consolidation_error"] = "memory_consolidation_failed"

        # Mem0 batch fact extraction (Phase 2)
        fact_stats = self._extract_and_store_facts()
        stats["fact_extraction"] = fact_stats

        # Episodic dedup pass (advisory §7.3).  No-op when the
        # consolidator is not injected or the env flag is off; cheap
        # to leave wired because the helper short-circuits before
        # calling the encoder when there is nothing to consolidate.
        stats["episodic_dedup"] = self._dedup_recent_episodes()

        # Confidence decay (unchanged)
        self._decay_skill_confidence()
        stats["confidence_decay_applied"] = True

        return stats

    def _dedup_recent_episodes(self) -> dict[str, Any]:
        """Fold near-duplicate episodes into representative summaries.

        Pulls the last :data:`_EPISODIC_DEDUP_BATCH` episodes, embeds
        each one through the semantic-memory encoder, and runs the
        deterministic greedy consolidator. The caller (production)
        decides what to do with the report — e.g. keep just the
        representatives, retire the duplicates. This step ships as
        an *audit only* signal until the persistence side lands in
        a follow-up PR.

        Skipped (returns ``{"skipped": True, …}``) when:

        - no consolidator is injected
        - ``BRAIN_EPISODIC_DEDUP_ENABLED`` is off
        - the semantic-memory encoder is unavailable (offline mode,
          memory subsystem not initialised, …)
        - there are no recent episodes to consolidate
        """
        if self._dedup_consolidator is None:
            return {"skipped": True, "reason": "no_consolidator"}
        if not _episodic_dedup_enabled():
            return {"skipped": True, "reason": "flag_off"}

        encoder = self._encoder_for_dedup()
        if encoder is None:
            return {"skipped": True, "reason": "no_encoder"}

        try:
            episodes = self._memory.episodic.get_recent(
                n=_EPISODIC_DEDUP_BATCH,
            )
        except Exception:
            logger.error(
                "Episodic dedup: failed to fetch recent episodes",
                exc_info=True,
            )
            return {"error": "fetch_failed"}

        if not episodes:
            return {"input_count": 0, "kept_count": 0, "removed_count": 0}

        records: list[EpisodeRecord] = []
        for episode in episodes:
            try:
                vector = encoder.encode(
                    episode.content,
                    normalize_embeddings=True,
                )
                embedding = tuple(vector.tolist())
            except Exception:
                logger.warning(
                    "Episodic dedup: encoder failed on episode %s",
                    episode.id,
                    exc_info=True,
                )
                continue
            records.append(
                EpisodeRecord(
                    episode_id=episode.id,
                    text=episode.content,
                    embedding=embedding,
                    occurred_at=episode.timestamp,
                ),
            )

        if not records:
            return {"input_count": len(episodes), "encoded_count": 0}

        report = self._dedup_consolidator.consolidate(records)
        return {
            "input_count": report.total_input,
            "kept_count": len(report.kept_ids),
            "removed_count": len(report.removed_ids),
            "summary_count": len(report.summaries),
        }

    def _encoder_for_dedup(self) -> Any:
        """Return the SemanticMemory encoder, or ``None`` when unavailable.

        The dedup pass embeds episode content with the same model the
        rest of the memory system uses, so cluster centroids match
        the production similarity space. We reach into
        ``memory.semantic._encoder`` rather than introducing a new
        encoder dependency — the underlying SentenceTransformer is
        already loaded once per pod.
        """
        semantic = getattr(self._memory, "semantic", None)
        if semantic is None:
            return None
        return getattr(semantic, "_encoder", None)

    def _extract_and_store_facts(self) -> dict[str, Any]:
        """Batch-extract facts from today's conversations using Mem0.

        Fetches recent episodes, runs Mem0 extraction on each conversation,
        deduplicates against FactStore, and stores new facts.

        Skipped entirely if Mem0 extractor or FactStore is not configured.

        Returns:
            Extraction stats: conversations processed, facts found, stored, dupes.
        """
        if self._mem0 is None or self._fact_store is None:
            return {"skipped": True, "reason": "mem0 or fact_store not configured"}

        if not getattr(self._mem0, "is_available", lambda: False)():
            return {"skipped": True, "reason": "mem0 not available"}

        try:
            # Get today's episodes grouped by conversation/guest
            recent_episodes = self._memory.episodic.get_recent(n=100)
        except Exception:
            logger.error("Step 1: failed to fetch recent episodes", exc_info=True)
            return {"error": "fetch_episodes_failed"}

        if not recent_episodes:
            return {"conversations": 0, "facts_found": 0}

        # Group episodes by guest/session for batch processing
        conversations = self._group_episodes_to_conversations(recent_episodes)

        total_extracted = 0
        store_result_totals = {"added": 0, "duplicates": 0, "errors": 0}

        for user_id, messages in conversations.items():
            try:
                facts = self._mem0.extract_facts(
                    conversation=messages,
                    user_id=user_id,
                )
                total_extracted += len(facts)

                if not facts:
                    continue

                # Convert ExtractedFact -> StoredFact for the FactStore
                stored = [
                    StoredFact(
                        fact_id=f.fact_id,
                        content=f.content,
                        fact_type=f.fact_type,
                        entity_id=f.entity_id,
                        confidence=f.confidence,
                        source=f.source,
                        created_at=f.extracted_at,
                    )
                    for f in facts
                ]

                # Contradiction check (advisory §7.4) — drops facts
                # that semantically contradict an existing one and
                # logs a "flag for PM" event when confidence is too
                # low to auto-resolve.  No-op when the detector is
                # not injected or BRAIN_CONTRADICTION_CHECK_ENABLED
                # is off, so the legacy path is preserved verbatim.
                stored = self._filter_contradicting_facts(stored)

                result = self._fact_store.store_facts(stored)
                store_result_totals["added"] += result.added
                store_result_totals["duplicates"] += result.duplicates
                store_result_totals["errors"] += result.errors

            except Exception:
                logger.warning(
                    "Fact extraction failed for user %s",
                    user_id,
                    exc_info=True,
                )
                store_result_totals["errors"] += 1

        logger.info(
            "Step 1 fact extraction: %d conversations, %d facts found, %d stored, %d duplicates, %d contradictions",
            len(conversations),
            total_extracted,
            store_result_totals["added"],
            store_result_totals["duplicates"],
            self._contradiction_stats["contradictions"],
        )

        return {
            "conversations": len(conversations),
            "facts_found": total_extracted,
            "contradictions": dict(self._contradiction_stats),
            **store_result_totals,
        }

    def _filter_contradicting_facts(
        self,
        candidates: list[StoredFact],
    ) -> list[StoredFact]:
        """Drop facts that contradict an existing one before storage.

        Each candidate is run through :class:`ContradictionDetector`,
        scoped per-entity. Resolution is consumed as follows:

        - :class:`Resolution.NEWER_WINS` — keep the candidate (the
          existing fact is the loser; persistence-side supersession
          is a follow-up PR).
        - :class:`Resolution.FLAG_PM` — drop the candidate and log a
          ``flagged_for_pm`` stat so an operator can review.
        - No contradiction — keep the candidate verbatim.

        No-op (returns ``candidates`` unchanged) when no detector is
        injected or ``BRAIN_CONTRADICTION_CHECK_ENABLED`` is off.
        Detector failures fail open: the fact is kept and an error
        is logged so a flaky LLM cannot block persistence.
        """
        if self._contradiction_detector is None or not _contradiction_check_enabled():
            self._contradiction_stats["skipped"] += len(candidates)
            return candidates

        kept: list[StoredFact] = []
        for fact in candidates:
            self._contradiction_stats["checked"] += 1
            try:
                detection = self._contradiction_detector.check(
                    new_content=fact.content,
                    property_id=fact.entity_id,
                    new_timestamp=fact.created_at.isoformat() if fact.created_at else "",
                )
            except Exception:
                logger.warning(
                    "Contradiction check failed for fact %s; keeping",
                    fact.fact_id,
                    exc_info=True,
                )
                kept.append(fact)
                continue

            if not detection.has_contradiction:
                kept.append(fact)
                continue

            self._contradiction_stats["contradictions"] += 1
            if detection.resolution == Resolution.NEWER_WINS:
                self._contradiction_stats["newer_wins"] += 1
                kept.append(fact)
            elif detection.resolution == Resolution.FLAG_PM:
                self._contradiction_stats["flagged_for_pm"] += 1
                logger.info(
                    "Fact %s flagged for PM review: %s",
                    fact.fact_id,
                    fact.content[:80],
                )
            else:
                # Unknown resolution — fail closed (keep) to avoid
                # silent data loss; a future enum value lands as a
                # warning here, not as a missing fact.
                logger.warning(
                    "Unhandled resolution %s for fact %s; keeping",
                    detection.resolution,
                    fact.fact_id,
                )
                kept.append(fact)
        return kept

    @staticmethod
    def _group_episodes_to_conversations(
        episodes: list[Any],
    ) -> dict[str, list[dict[str, str]]]:
        """Group raw episodes into per-user conversation message lists.

        Each episode is expected to have guest_id/user_id and message content.
        Output format matches Mem0's expected conversation input.

        Args:
            episodes: Raw episodic memory entries.

        Returns:
            Dict mapping user_id to list of {"role": ..., "content": ...}.
        """
        grouped: dict[str, list[dict[str, str]]] = {}

        for ep in episodes:
            user_id = getattr(ep, "guest_id", None) or getattr(ep, "user_id", None) or "unknown"

            content = getattr(ep, "content", None) or str(ep)
            role = getattr(ep, "role", "user")

            grouped.setdefault(user_id, []).append(
                {
                    "role": role,
                    "content": content,
                }
            )

        return grouped

    def _decay_skill_confidence(self) -> None:
        """Apply daily confidence decay to all skills.

        Skills that aren't used gradually lose confidence
        (Ebbinghaus forgetting curve principle).
        """
        all_skills = self._memory.procedural.get_all_procedures(
            active_only=True,
        )
        for skill in all_skills:
            if skill.source in ("default", "manual", "immutable", "sop"):
                continue  # don't decay seed/manual/immutable/sop procedures
            if getattr(skill, "immutable", False):
                continue
            old_conf = skill.confidence
            skill.confidence = max(0.1, skill.confidence * _DECAY_FACTOR)
            if abs(old_conf - skill.confidence) > 0.001:
                self._memory.procedural._redis.set(
                    self._memory.procedural._key(skill.procedure_id),
                    __import__("json").dumps(skill.to_dict()),
                )

    # ── Step 2: Skill Evolution ─────────────────────────────────────── #

    def _step2_evolve_skills(self) -> dict[str, Any]:
        """Aggregate today's failures, group by pattern, evolve skills.

        Groups failures by event_type to batch-evolve one skill per
        failure pattern instead of evolving per-interaction.

        Returns:
            Skill evolution statistics.
        """
        since = datetime.now(UTC) - timedelta(days=1)

        try:
            failures = self._recorder.get_failures(since)
        except Exception:
            logger.error("Step 2: failed to get failures", exc_info=True)
            return {"error": "get_failures_failed"}

        if not failures:
            return {"failures_found": 0, "evolved": 0, "blocked": 0}

        # Group failures by event_type for batch evolution
        grouped = self._group_failures_by_type(failures)

        evolved = 0
        blocked = 0
        errors = 0

        for event_type, group in grouped.items():
            result = self._evolve_from_failure_group(
                event_type,
                group,
            )
            if result == "evolved":
                evolved += 1
            elif result == "blocked":
                blocked += 1
            else:
                errors += 1

        return {
            "failures_found": len(failures),
            "patterns_found": len(grouped),
            "evolved": evolved,
            "blocked": blocked,
            "errors": errors,
        }

    @staticmethod
    def _group_failures_by_type(
        failures: list[Any],
    ) -> dict[str, list[Any]]:
        """Group failures by event_type.

        Args:
            failures: List of failed interactions.

        Returns:
            Dict mapping event_type to list of interactions.
        """
        grouped: dict[str, list[Any]] = {}
        for f in failures:
            etype = getattr(f, "event_type", "unknown")
            grouped.setdefault(etype, []).append(f)
        return grouped

    def _evolve_from_failure_group(
        self,
        event_type: str,
        failures: list[Any],
    ) -> str:
        """Evolve a skill from a group of similar failures.

        Uses the most representative failure (worst grader score)
        to drive the evolution.

        Args:
            event_type: The event type of this failure group.
            failures: List of failures of this type.

        Returns:
            Status string: 'evolved', 'blocked', or 'error'.
        """
        # Pick the most representative failure (worst score)
        representative = min(
            failures,
            key=lambda f: getattr(f, "grader_score", 0.5) or 0.5,
        )

        try:
            result = self._skills.evolve_on_failure(
                event_type=event_type,
                event_description=getattr(
                    representative,
                    "input_message",
                    "",
                ),
                action_taken=str(
                    getattr(representative, "output_actions", []),
                ),
                failure_reason=_extract_failure_reason(representative),
                context=getattr(representative, "context", {}),
            )
            return result.status
        except Exception:
            logger.error(
                "Step 2: evolution failed for %s",
                event_type,
                exc_info=True,
            )
            return "error"

    # ── Step 3: Preference Aggregation ──────────────────────────────── #

    def _step3_aggregate_preferences(self) -> dict[str, Any]:
        """Analyze owner approval patterns and create stable rules.

        Looks for consistent approval/rejection patterns:
        - If owner approved same event_type 3+ times -> create auto-approve rule
        - If owner rejected same event_type 3+ times -> create escalation rule

        Returns:
            Preference aggregation statistics.
        """
        since = datetime.now(UTC) - timedelta(days=1)

        try:
            approvals = self._recorder.get_approvals(since)
        except Exception:
            logger.error("Step 3: failed to get approvals", exc_info=True)
            return {"error": "get_approvals_failed"}

        if not approvals:
            return {"approvals_found": 0, "rules_created": 0}

        rules_created = self._create_rules_from_approvals(approvals)

        return {
            "approvals_found": len(approvals),
            "rules_created": rules_created,
        }

    def _create_rules_from_approvals(
        self,
        approvals: list[Any],
    ) -> int:
        """Analyze approval patterns and create procedural rules.

        Args:
            approvals: List of interactions with approval decisions.

        Returns:
            Number of new rules created.
        """
        # Group by event_type and count approve/reject
        patterns: dict[str, dict[str, int]] = {}
        for approval in approvals:
            etype = getattr(approval, "event_type", "unknown")
            if etype not in patterns:
                patterns[etype] = {"approved": 0, "rejected": 0}

            if getattr(approval, "owner_approved", False):
                patterns[etype]["approved"] += 1
            else:
                patterns[etype]["rejected"] += 1

        rules_created = 0
        for event_type, counts in patterns.items():
            total = counts["approved"] + counts["rejected"]
            if total < _MIN_APPROVALS_FOR_RULE:
                continue

            rule = self._create_preference_rule(
                event_type,
                counts,
                total,
            )
            if rule:
                rules_created += 1

        return rules_created

    def _create_preference_rule(
        self,
        event_type: str,
        counts: dict[str, int],
        total: int,
    ) -> Any | None:
        """Create a procedural rule from consistent approval pattern.

        Args:
            event_type: The event type.
            counts: Approve/reject counts.
            total: Total decisions.

        Returns:
            Created Procedure or None.
        """
        approve_rate = counts["approved"] / total

        if approve_rate >= _APPROVAL_CONSISTENCY:
            return self._memory.procedural.add_procedure(
                name=f"auto_approve_{event_type}",
                description=(
                    f"Owner consistently approved {event_type} "
                    f"({counts['approved']}/{total} times). "
                    f"Auto-approve similar events."
                ),
                trigger_conditions={"events": [event_type]},
                actions=["auto_approve", "notify_owner_after"],
                source="preference_aggregation",
                tags=["preference", "auto_approve", event_type],
                confidence=min(0.9, approve_rate),
            )

        if (1 - approve_rate) >= _APPROVAL_CONSISTENCY:
            return self._memory.procedural.add_procedure(
                name=f"always_escalate_{event_type}",
                description=(
                    f"Owner consistently rejected {event_type} "
                    f"({counts['rejected']}/{total} times). "
                    f"Always escalate for approval."
                ),
                trigger_conditions={"events": [event_type]},
                actions=["escalate_to_owner", "wait_for_approval"],
                source="preference_aggregation",
                tags=["preference", "escalate", event_type],
                confidence=min(0.9, 1 - approve_rate),
            )

        return None

    # ── Step 4: Knowledge Graph Update ──────────────────────────────── #

    def _step4_update_knowledge_graph(self) -> dict[str, Any]:
        """Sync entities into the knowledge graph.

        Task 7 of CLAUDE_CODE_WIRING_FIX_PLAN.md replaces the
        LLM-driven entity-extraction default with a deterministic
        mapping from DecisionCases.  Three paths supported:

        1. **Deterministic (default when ``deterministic_kg_sync``
           is injected)** — pulls recent DecisionCases through
           ``DecisionCaseStore.search`` and feeds them into
           :class:`DeterministicKGSync`.  Zero LLM tokens.
        2. **Legacy LLM (default when no sync injected)** — preserves
           the pre-Task-7 ``MemoryConsolidator.consolidate`` call so
           deployments without the new wiring see no behaviour change.
        3. **Both (LLM flag opt-in)** — runs deterministic first, then
           the LLM consolidation as a free-text fallback for guest
           preferences buried in chat that the structured surface
           cannot lift.

        Returns:
            KG update statistics: counters when the deterministic
            path ran, plus the legacy ``consolidation_result`` block
            when the LLM path ran.
        """
        stats: dict[str, Any] = {}
        deterministic_ran = False

        # ── Deterministic path ─────────── #
        if self._deterministic_kg_sync is not None and deterministic_sync_enabled() and self._case_store is not None:
            try:
                cases = self._case_store.search(limit=200)
                sync_stats = self._deterministic_kg_sync.sync_decision_cases(cases)
                stats["deterministic"] = {
                    "cases_seen": sync_stats.cases_seen,
                    "cases_skipped": sync_stats.cases_skipped,
                    "nodes_written": sync_stats.nodes_written,
                    "relationships_written": (sync_stats.relationships_written),
                }
                deterministic_ran = True
            except Exception:
                logger.error(
                    "Step 4 (KG deterministic sync) failed",
                    exc_info=True,
                )
                stats["deterministic_error"] = "sync_failed"

        # ── Legacy LLM path ─────────── #
        # Runs when (a) the deterministic path did not actually run
        # (no sync injected, env flag off, or sync raised) so the
        # KG keeps ingesting *something* and is never silent on a
        # nightly tick, or (b) the operator explicitly opts the LLM
        # extraction back on alongside the deterministic path for
        # free-text fallback through ``BRAIN_KG_LLM_EXTRACTION_ENABLED``.
        run_llm = not deterministic_ran or llm_extraction_enabled()
        if run_llm:
            try:
                recent = self._memory.episodic.get_recent(n=50)
                if recent:
                    result = self._memory.consolidator.consolidate(force=True)
                    stats["llm"] = {
                        "episodes_found": len(recent),
                        "consolidation_result": result,
                    }
                else:
                    stats["llm"] = {"episodes_found": 0}
            except Exception:
                logger.error(
                    "Step 4 (KG LLM consolidation) failed",
                    exc_info=True,
                )
                stats["llm_error"] = "consolidation_failed"
        else:
            stats["llm_skipped"] = True

        return stats or {"noop": True}

    # ── Step 5: Skill Pruning ───────────────────────────────────────── #

    def _step5_prune_skills(self) -> dict[str, Any]:
        """Remove low-quality and unused skills.

        Deactivates skills that are:
        - Below confidence threshold (0.15)
        - Zero successes with low confidence
        - Not default/seed procedures

        Returns:
            Pruning statistics.
        """
        try:
            pruned = self._memory.procedural.cleanup(
                remove_below_confidence=_PRUNE_CONFIDENCE,
                remove_unused_days=_PRUNE_UNUSED_DAYS,
                remove_zero_success=True,
            )
            total = self._memory.procedural.count()
            return {
                "total_skills": total,
                "pruned": pruned,
                "remaining": total - pruned,
            }
        except Exception:
            logger.error("Step 5 (pruning) failed", exc_info=True)
            return {"error": "pruning_failed"}

    # ── Step 6: MIRA readiness ─────────────────────────────────────── #

    def _step6_mira_ready(self) -> dict[str, Any]:
        """Зафиксировать готовность MIRA для ночного цикла.

        На данном этапе прямого источника pending-кандидатов нет — они
        поступают через ``/knowledge/sync`` или ``/mira/process``. Этот
        шаг логирует состояние и служит hook-ом для будущей интеграции,
        когда nightly будет сам забирать кандидатов из Cendra DB.

        Returns:
            Словарь с флагом ``mira_available`` и кратким статусом.
        """
        try:
            logger.info("Step 6: MIRA readiness check — no pending source yet.")
            return {"mira_available": True, "pending_source": "none_yet"}
        except Exception:
            logger.error("Step 6 (MIRA ready) failed", exc_info=True)
            return {"error": "mira_ready_failed"}

    # ── Step 7: Pattern Extraction ─────────────────────────────────── #

    def _step7_extract_patterns(self) -> dict[str, Any]:
        """Extract PatternRules from accumulated DecisionCases.

        Iterates over all scenarios that have enough cases, runs the
        extraction pipeline, validates candidate rules, and stores
        valid rules in the PatternRuleStore.

        Skipped if case_store or pattern_extractor is not configured.

        Returns:
            Pattern extraction statistics.
        """
        if self._case_store is None or self._pattern_extractor is None:
            return {"skipped": True, "reason": "case_store or extractor not configured"}

        stats: dict[str, Any] = {
            "scenarios_checked": 0,
            "rules_extracted": 0,
            "rules_validated": 0,
            "rules_stored": 0,
            "errors": 0,
        }

        # Collect distinct (scenario, property_id, owner_id) from recent cases
        scopes = self._collect_extraction_scopes()
        stats["scenarios_checked"] = len(scopes)

        for scenario, property_id, owner_id in scopes:
            try:
                result = self._pattern_extractor.extract_patterns(
                    scenario=scenario,
                    property_id=property_id,
                    owner_id=owner_id,
                )
                stats["rules_extracted"] += len(result.rules)

                for rule in result.rules:
                    validation = self._pattern_validator.validate(rule)
                    if validation.valid:
                        stats["rules_validated"] += 1
                        if self._rule_store is not None:
                            self._rule_store.store(rule)
                            stats["rules_stored"] += 1
                    else:
                        logger.debug(
                            "Rule rejected: scenario=%s, reasons=%s",
                            rule.scenario.value,
                            validation.reasons,
                        )
            except Exception:
                logger.warning(
                    "Pattern extraction failed for %s/%s",
                    scenario.value,
                    property_id,
                    exc_info=True,
                )
                stats["errors"] += 1

        logger.info(
            "Step 7 pattern extraction: %d scopes, %d extracted, %d validated, %d stored",
            stats["scenarios_checked"],
            stats["rules_extracted"],
            stats["rules_validated"],
            stats["rules_stored"],
        )
        return stats

    def _step9_detect_foundation_drift(self) -> dict[str, Any]:
        """Surface foundation update candidates from PM override history (W8).

        Walks recent :class:`DecisionCase` rows through
        :func:`core.brain.patterns.foundation_update.
        detect_foundation_drift` and upserts every produced
        :class:`FoundationUpdateCandidate` so a human reviewer can
        triage them via the future ``/foundation/updates`` admin
        surface.

        Skipped (returns a tagged no-op stats dict) when either
        the case store or the foundation update store is not
        wired — preserves pre-W8 nightly behaviour for the
        default deployment.

        Returns:
            Stats dict — ``candidates_emitted`` is the count of
            backlog rows produced this run; ``cases_scanned`` is
            how many cases the detector evaluated.
        """
        if self._case_store is None or self._foundation_update_store is None:
            return {
                "skipped": True,
                "reason": "case_store or foundation_update_store not configured",
            }

        stats: dict[str, Any] = {
            "candidates_emitted": 0,
            "cases_scanned": 0,
            "errors": 0,
        }

        try:
            # Pull the full recent window — the detector itself
            # decides which (scenario, scope) buckets cross the
            # threshold so we do not pre-filter here.  ``limit``
            # is generous (a single property rarely accrues more
            # than a few hundred overrides per night) but bounded
            # so the nightly cycle stays predictable.
            cases = self._case_store.search(limit=5000)
            stats["cases_scanned"] = len(cases)
            candidates = detect_foundation_drift(cases)
            for candidate in candidates:
                try:
                    self._foundation_update_store.upsert(candidate)
                    stats["candidates_emitted"] += 1
                except Exception:
                    logger.warning(
                        "Step 9 foundation drift upsert failed: %s",
                        candidate.candidate_id,
                        exc_info=True,
                    )
                    stats["errors"] += 1
        except Exception:
            logger.error(
                "Step 9 (foundation drift) failed",
                exc_info=True,
            )
            stats["errors"] += 1

        logger.info(
            "Step 9 foundation drift: %d cases scanned, %d candidates emitted, %d errors",
            stats["cases_scanned"],
            stats["candidates_emitted"],
            stats["errors"],
        )
        return stats

    def _collect_extraction_scopes(
        self,
    ) -> list[tuple[Scenario, str, str]]:
        """Collect distinct (scenario, property_id, owner_id) tuples to process.

        Queries the case store for all non-GENERAL scenarios that have
        at least 3 cases (minimum for pattern extraction).

        Returns:
            List of (Scenario, property_id, owner_id) tuples.
        """
        scopes: list[tuple[Scenario, str, str]] = []

        for scenario in Scenario:
            if scenario == "general":
                continue

            count = self._case_store.count(scenario=scenario)
            if count < 3:
                continue

            cases = self._case_store.search(
                scenario=scenario,
                limit=1,
            )
            if cases:
                sample = cases[0]
                scopes.append(
                    (
                        scenario,
                        sample.property_id,
                        sample.owner_id,
                    )
                )

        return scopes

    # ── Monthly Cycle ───────────────────────────────────────────────── #

    def run_monthly(self) -> dict[str, Any]:
        """Run the monthly evaluation cycle.

        Returns:
            Report with metrics, maturity, and cleanup stats.
        """
        logger.info("=== Monthly Evaluation Started ===")

        metrics = self._compute_monthly_metrics()
        cleanup = self._monthly_deep_cleanup()

        report = {**metrics, "cleanup": cleanup}
        logger.info("=== Monthly Evaluation Complete: %s ===", report)
        return report

    def _compute_monthly_metrics(self) -> dict[str, Any]:
        """Compute monthly accuracy and quality metrics.

        Returns:
            Metrics dictionary.
        """
        try:
            graded = self._recorder.get_graded(days=30)
        except Exception:
            logger.error("Monthly metrics: get_graded failed", exc_info=True)
            return {"error": "metrics_failed"}

        if not graded:
            return {"total_interactions": 0, "no_data": True}

        scores = [i.grader_score for i in graded if i.grader_score is not None]
        interventions = sum(1 for i in graded if getattr(i, "owner_intervened", False))
        self_resolved = sum(1 for i in graded if getattr(i, "resolved_without_escalation", False))

        # Event type breakdown
        event_counts = Counter(getattr(i, "event_type", "unknown") for i in graded)

        return {
            "total_interactions": len(graded),
            "avg_grader_score": _safe_mean(scores),
            "owner_intervention_rate": round(interventions / len(graded), 3),
            "self_resolution_rate": round(self_resolved / len(graded), 3),
            "skills_evolved": self._skills.evolution_count,
            "skills_total": self._memory.procedural.count(),
            "top_event_types": dict(event_counts.most_common(10)),
        }

    def _monthly_deep_cleanup(self) -> dict[str, Any]:
        """Aggressive monthly skill pruning with stricter thresholds.

        Returns:
            Cleanup statistics.
        """
        try:
            pruned = self._memory.procedural.cleanup(
                remove_below_confidence=_MONTHLY_PRUNE_CONFIDENCE,
                remove_unused_days=_MONTHLY_PRUNE_AGE_DAYS,
                remove_zero_success=True,
            )

            # Also clean expired interaction indices
            expired_cleaned = 0
            if hasattr(self._recorder, "cleanup_expired"):
                expired_cleaned = self._recorder.cleanup_expired(
                    max_age_days=90,
                )

            return {
                "skills_pruned": pruned,
                "expired_interactions_cleaned": expired_cleaned,
            }
        except Exception:
            logger.error("Monthly cleanup failed", exc_info=True)
            return {"error": "cleanup_failed"}


# ── Helpers ─────────────────────────────────────────────────────────── #


def _extract_failure_reason(interaction: Any) -> str:
    """Extract a descriptive failure reason from an interaction.

    Args:
        interaction: The failed interaction.

    Returns:
        Human-readable failure reason string.
    """
    reasons: list[str] = []

    if getattr(interaction, "guest_satisfied", None) == "negative":
        reasons.append("guest was unsatisfied")
    if getattr(interaction, "owner_intervened", False):
        reasons.append("owner had to intervene manually")

    score = getattr(interaction, "grader_score", None)
    if score is not None and score < 0.4:
        reasons.append(f"low quality score ({score:.2f})")

    trace = getattr(interaction, "reasoning_trace", "")
    if trace:
        reasons.append(f"trace: {trace[:100]}")

    return "; ".join(reasons) if reasons else "unknown failure"


def _safe_mean(values: list[float]) -> float:
    """Compute mean with empty-list safety.

    Args:
        values: List of floats.

    Returns:
        Mean or 0.0 if empty.
    """
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
