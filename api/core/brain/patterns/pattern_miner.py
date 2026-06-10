"""Mine :class:`PatternRule` candidates from a :class:`DecisionCase` set.

The miner is the statistical bridge between episodic memory
(``DecisionCase`` rows) and procedural memory (``PatternRule`` rows).
For a given scope (e.g. one property), it groups cases by
``scenario`` and counts which ``DecisionType`` the PM chose most
often.  A rule is emitted when:

1. A dominant ``DecisionType`` appears in at least ``min_support``
   cases, *and*
2. Its frequency relative to every other decision for that scenario
   exceeds ``min_confidence``.

The miner is intentionally condition-free — every emitted rule has
``conditions == {}``.  Condition synthesis (e.g. "fire only when
``nights_count >= 5``") is a separate, heavier pipeline that sits on
top of this baseline.  For cold-start bootstrap the unconditional
frequency is already enough to seed the Trust Meter.

The miner is pure compute; no I/O, no mutation, no async.  A single
instance is safe to cache per process.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from core.brain.patterns.condition_synthesizer import (
    ConditionSynthesizer,
    SynthesisResult,
)
from core.brain.patterns.confidence import (
    ConfidenceContext,
    compute_confidence,
)
from core.brain.patterns.extractor import (
    _build_rationale,
    _conditions_subsume,
    _count_signal_weights,
    _dominant_foundation_id,
    _dominant_stage,
    _merge_subsumed_rules,
    _origin_from_cases,
)
from core.brain.patterns.models import (
    CONFIDENCE_ASK_THRESHOLD,
    CONFIDENCE_AUTO_THRESHOLD,
    MAX_COUNTEREXAMPLE_RATIO,
    MIN_SUPPORT_AUTO,
    SCENARIO_GENERAL,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ExecutionMode,
    PatternRule,
    PatternScope,
    RiskLevel,
)

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_MINORITY_SUPPORT",
    "DEFAULT_MIN_SUPPORT",
    "PatternMiner",
    "PatternMiningReport",
]


logger = logging.getLogger(__name__)


DEFAULT_MIN_SUPPORT: Final[int] = 3
DEFAULT_MIN_CONFIDENCE: Final[float] = 0.4

# Ceiling on confidence when every supporting case is ``HISTORICAL``.
# Bootstrap replay is lossy — side-channel PM decisions (WhatsApp,
# phone) are invisible to the archive loader — so purely-historical
# rules are capped below ``CONFIDENCE_AUTO_THRESHOLD`` and can only
# reach ``ExecutionMode.ASK``.  A single live case is enough to
# unlock the full confidence band.
DEFAULT_HISTORICAL_CONFIDENCE_CAP: Final[float] = 0.6

# Minimum number of minority cases required to attempt condition
# synthesis.  Mümin's coffee-capsule example has 2 ``APPROVE`` cases
# inside a 10-case scenario — below this floor the synthesizer would
# learn from a single anecdote.
DEFAULT_MIN_MINORITY_SUPPORT: Final[int] = 2


@dataclass(frozen=True, slots=True)
class PatternMiningReport:
    """Counters emitted alongside :meth:`PatternMiner.mine`.

    Attributes:
        considered_cases: Number of cases inspected after filters.
        rejected_unlearnable: Cases skipped because ``is_learnable``
            is ``False`` (no outcome yet, or generic scenario).
        grouped_buckets: Distinct ``(scope_id, scenario, action)``
            buckets discovered.
        below_support: Buckets skipped because support count was
            below ``min_support``.
        below_confidence: Buckets skipped because confidence was
            below ``min_confidence``.
        emitted_rules: Number of :class:`PatternRule` objects returned.
        synthesised_rules: Subset of ``emitted_rules`` that carry
            non-empty ``conditions`` mined by
            :class:`ConditionSynthesizer`.
        synthesis_attempts: Minority buckets fed to the synthesizer.
        synthesis_rejected: Synthesis attempts that found no
            condition meeting purity / support thresholds.
    """

    considered_cases: int = 0
    rejected_unlearnable: int = 0
    grouped_buckets: int = 0
    below_support: int = 0
    below_confidence: int = 0
    emitted_rules: int = 0
    synthesised_rules: int = 0
    synthesis_attempts: int = 0
    synthesis_rejected: int = 0


class PatternMiner:
    """Count dominant PM decisions per scenario to seed PatternRules.

    Args:
        min_support: Minimum number of supporting cases before a
            dominant action is promoted to a rule.
        min_confidence: Minimum dominant-action frequency
            (``support / total_scenario_cases``).  Below this the
            scenario is judged too mixed to commit to a single rule.
        require_outcome: When ``True`` (default) only cases whose
            ``is_learnable`` property reports ``True`` participate.
            Set to ``False`` to bootstrap frequency rules purely
            from action choice, even without outcome labels — useful
            for the cold-start replay where outcomes are unknown.
        historical_confidence_cap: Upper bound on the emitted
            :class:`PatternRule.confidence` when *every* supporting
            case has ``source == CaseSource.HISTORICAL``.  Keeps
            bootstrap-only rules out of the ``AUTO`` band until at
            least one live observation confirms them.  Clamped into
            ``(0.0, 1.0]``.
    """

    def __init__(
        self,
        *,
        min_support: int = DEFAULT_MIN_SUPPORT,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        require_outcome: bool = True,
        historical_confidence_cap: float = DEFAULT_HISTORICAL_CONFIDENCE_CAP,
        min_minority_support: int = DEFAULT_MIN_MINORITY_SUPPORT,
        condition_synthesizer: ConditionSynthesizer | None = None,
        forbidden_foundation_ids: frozenset[str] = frozenset(),
    ) -> None:
        if min_support < 1:
            raise ValueError("min_support must be >= 1")
        if not 0.0 < min_confidence <= 1.0:
            raise ValueError("min_confidence must be in (0.0, 1.0]")
        if not 0.0 < historical_confidence_cap <= 1.0:
            raise ValueError(
                "historical_confidence_cap must be in (0.0, 1.0]",
            )
        if min_minority_support < 1:
            raise ValueError("min_minority_support must be >= 1")
        self._min_support = int(min_support)
        self._min_confidence = float(min_confidence)
        self._require_outcome = bool(require_outcome)
        self._historical_cap = float(historical_confidence_cap)
        self._min_minority = int(min_minority_support)
        self._synthesizer = condition_synthesizer or ConditionSynthesizer()
        # Sprint 6 W4 — frozenset of foundation slugs that forbid
        # learning per FL-05 (the six safety-only Critical
        # scenarios + every other "Should AI Learn Pattern: No"
        # row).  Compute once at wiring via
        # :func:`compute_forbidden_foundation_ids`, then pass here.
        # Empty (default) preserves the pre-W4 mining behaviour
        # bit-for-bit so existing call sites do not regress.
        self._forbidden_foundation_ids = forbidden_foundation_ids

    def mine(
        self,
        cases: Iterable[DecisionCase],
        *,
        scope: PatternScope = PatternScope.PROPERTY,
    ) -> tuple[list[PatternRule], PatternMiningReport]:
        """Extract :class:`PatternRule` candidates from ``cases``.

        Args:
            cases: Any iterable of :class:`DecisionCase` rows.  The
                miner consumes it exactly once.
            scope: Scope under which rules will be stored.  Determines
                how cases are grouped before counting.

        Returns:
            ``(rules, report)``.  ``rules`` is ordered by decreasing
            support count then confidence, so callers can prioritise
            the strongest rules first when persisting.
        """
        # Sprint 6 W4 — FL-05 learn gate.  Drop cases whose
        # ``foundation_scenario_id`` lands in the forbidden set
        # before they reach the bucketing step so safety-critical
        # scenarios (gas smell, broken glass, medical, …) never
        # become :class:`PatternRule` candidates.  Empty forbidden
        # set ⇒ no filtering, no behaviour change.
        if self._forbidden_foundation_ids:
            cases = [case for case in cases if case.foundation_scenario_id not in self._forbidden_foundation_ids]
        buckets, considered, rejected = self._bucket(cases, scope=scope)
        scenario_totals = self._count_scenario_totals(buckets)
        rules: list[PatternRule] = []
        below_support = 0
        below_confidence = 0
        emitted_keys: set[tuple[str, str, DecisionType]] = set()
        for key, cases_for_key in buckets.items():
            support = len(cases_for_key)
            if support < self._min_support:
                below_support += 1
                continue
            scope_id, scenario, action_type = key
            total = scenario_totals[(scope_id, scenario)]
            confidence = support / total if total else 0.0
            if confidence < self._min_confidence:
                below_confidence += 1
                continue
            rule = self._build_rule(
                scope=scope,
                scope_id=scope_id,
                scenario=scenario,
                action_type=action_type,
                cases=cases_for_key,
                support=support,
                total=total,
                confidence=confidence,
            )
            rules.append(rule)
            emitted_keys.add(key)
        synthesised, attempts, rejected_synth = self._synthesise_minorities(
            buckets=buckets,
            emitted_keys=emitted_keys,
            scope=scope,
        )
        rules.extend(synthesised)
        # Mümin-feedback parity with the API extractor — collapse
        # ``total_price >= 26`` / ``total_price >= 29`` style overlap
        # so re-bootstraps don't flood the registry with duplicates.
        rules = _merge_subsumed_rules(rules)
        rules.sort(
            key=lambda r: (
                -len(r.conditions),
                -r.confidence,
                -r.support_count,
                r.scenario,
            ),
        )
        report = PatternMiningReport(
            considered_cases=considered,
            rejected_unlearnable=rejected,
            grouped_buckets=len(buckets),
            below_support=below_support,
            below_confidence=below_confidence,
            emitted_rules=len(rules),
            synthesised_rules=len(synthesised),
            synthesis_attempts=attempts,
            synthesis_rejected=rejected_synth,
        )
        _emit_mine_metrics(rules=rules, attempts=attempts, rejected=rejected_synth)
        return rules, report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bucket(
        self,
        cases: Iterable[DecisionCase],
        *,
        scope: PatternScope,
    ) -> tuple[
        dict[tuple[str, str, DecisionType], list[DecisionCase]],
        int,
        int,
    ]:
        """Group cases by ``(scope_id, scenario, action_type)``."""
        buckets: dict[tuple[str, str, DecisionType], list[DecisionCase]] = defaultdict(list)
        considered = 0
        rejected = 0
        for case in cases:
            if self._require_outcome and not case.is_learnable:
                rejected += 1
                continue
            if case.scenario == SCENARIO_GENERAL:
                rejected += 1
                continue
            scope_id = _scope_id_for(case, scope)
            if not scope_id:
                rejected += 1
                continue
            key = (scope_id, case.scenario, case.decision.action_type)
            buckets[key].append(case)
            considered += 1
        return buckets, considered, rejected

    def _count_scenario_totals(
        self,
        buckets: dict[tuple[str, str, DecisionType], list[DecisionCase]],
    ) -> dict[tuple[str, str], int]:
        """Aggregate total case count per ``(scope_id, scenario)``."""
        totals: Counter[tuple[str, str]] = Counter()
        for (scope_id, scenario, _action), cases in buckets.items():
            totals[(scope_id, scenario)] += len(cases)
        return dict(totals)

    def _synthesise_minorities(
        self,
        *,
        buckets: dict[tuple[str, str, DecisionType], list[DecisionCase]],
        emitted_keys: set[tuple[str, str, DecisionType]],
        scope: PatternScope,
    ) -> tuple[list[PatternRule], int, int]:
        """Mine conditional rules from non-dominant buckets.

        For every ``(scope_id, scenario)`` we look at every bucket
        whose dominant rule was not emitted (either skipped for
        support / confidence or simply outranked).  The synthesizer
        compares those minority cases against everything else in the
        same scenario and tries to find a feature split that captures
        them cleanly.

        Args:
            buckets: All buckets returned by :meth:`_bucket`.
            emitted_keys: Keys for which an unconditional rule was
                already emitted.  We still synthesise those when the
                bucket itself is mixed (the conditional rule wins
                naturally because it has a higher confidence).
            scope: Scope under which conditional rules are stored.

        Returns:
            ``(rules, attempts, rejected)`` — synthesised rules and
            the diagnostic counters mirrored on
            :class:`PatternMiningReport`.
        """
        rules: list[PatternRule] = []
        attempts = 0
        rejected = 0
        scenario_index = self._index_by_scenario(buckets)
        for (scope_id, scenario), action_map in scenario_index.items():
            if len(action_map) < 2:
                continue
            for action_type, target_cases in action_map.items():
                if len(target_cases) < self._min_minority:
                    continue
                other_cases = self._collect_other_cases(
                    action_map=action_map,
                    skip_action=action_type,
                )
                attempts += 1
                result, _ = self._synthesizer.synthesize(
                    target_cases=target_cases,
                    other_cases=other_cases,
                )
                if result.is_empty:
                    rejected += 1
                    continue
                rule = self._build_conditional_rule(
                    scope=scope,
                    scope_id=scope_id,
                    scenario=scenario,
                    action_type=action_type,
                    target_cases=target_cases,
                    result=result,
                )
                rules.append(rule)
                logger.info(
                    "synthesised_conditional_rule scenario=%s action=%s scope_id=%s "
                    "conditions=%s confidence=%s support=%s",
                    scenario,
                    action_type.value,
                    scope_id,
                    list(result.conditions.keys()),
                    round(result.confidence, 3),
                    result.support_count,
                )
        return rules, attempts, rejected

    @staticmethod
    def _index_by_scenario(
        buckets: dict[tuple[str, str, DecisionType], list[DecisionCase]],
    ) -> dict[tuple[str, str], dict[DecisionType, list[DecisionCase]]]:
        """Group bucket keys by ``(scope_id, scenario)``."""
        index: dict[tuple[str, str], dict[DecisionType, list[DecisionCase]]] = defaultdict(dict)
        for (scope_id, scenario, action), cases in buckets.items():
            index[(scope_id, scenario)][action] = cases
        return index

    @staticmethod
    def _collect_other_cases(
        *,
        action_map: dict[DecisionType, list[DecisionCase]],
        skip_action: DecisionType,
    ) -> list[DecisionCase]:
        """Return every case for the scenario except ``skip_action``."""
        collected: list[DecisionCase] = []
        for action, cases in action_map.items():
            if action is skip_action:
                continue
            collected.extend(cases)
        return collected

    def _build_conditional_rule(
        self,
        *,
        scope: PatternScope,
        scope_id: str,
        scenario: str,
        action_type: DecisionType,
        target_cases: list[DecisionCase],
        result: SynthesisResult,
    ) -> PatternRule:
        """Assemble a conditional :class:`PatternRule` from synthesis."""
        params = _consensus_params(target_cases)
        risk = RiskLevel.MEDIUM
        last_seen = max(case.created_at for case in target_cases)
        # ali.md §10 multi-factor formula on the synthesised
        # condition's own purity (success / (success + counter)).
        condition_total = result.support_count + result.counterexample_count
        # Sprint 6 W6 — fold §5 signal counts into the confidence
        # context so PM approvals / edits across the supporting
        # cases boost the rule's confidence per the proactive
        # foundation guidance.  Legacy cases without a populated
        # ``resolution_type`` contribute 0 to every count → the
        # formula collapses to its pre-W6 output.
        signal_counts = _count_signal_weights(target_cases)
        confidence = compute_confidence(
            result.support_count,
            condition_total,
            ConfidenceContext(
                counterexample_count=result.counterexample_count,
                last_seen_at=last_seen,
                **signal_counts,
            ),
        )
        if _all_historical(target_cases) and confidence > self._historical_cap:
            confidence = self._historical_cap
        execution_mode = _mode_for(confidence, risk, result.support_count)
        source_ids = tuple(case.case_id for case in target_cases)
        conditions = dict(result.conditions)
        # Mümin-feedback parity with the API extractor —
        # deterministic identity + rationale, mirroring _build_rule.
        rationale = _build_rationale(
            scenario=scenario,
            action_type=action_type,
            conditions=conditions,
            support=result.support_count,
            counterexamples=result.counterexample_count,
            cases=target_cases,
        )
        stable_id = PatternRule.deterministic_id(
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
            action_type=action_type,
            conditions=conditions,
        )
        return PatternRule(
            pattern_id=stable_id,
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
            conditions=conditions,
            action=DecisionAction(
                action_type=action_type,
                params={**params, "_rationale": rationale},
            ),
            support_count=result.support_count,
            counterexample_count=result.counterexample_count,
            confidence=round(confidence, 4),
            risk_level=risk,
            stage=_dominant_stage(target_cases),
            execution_mode=execution_mode,
            source_case_ids=source_ids,
            last_seen_at=last_seen,
            rationale=rationale,
            active=True,
            origin=_origin_from_cases(target_cases),
            foundation_scenario_id=_dominant_foundation_id(target_cases),
        )

    def _build_rule(
        self,
        *,
        scope: PatternScope,
        scope_id: str,
        scenario: str,
        action_type: DecisionType,
        cases: list[DecisionCase],
        support: int,
        total: int,
        confidence: float,
    ) -> PatternRule:
        """Assemble a :class:`PatternRule` from one winning bucket.

        When every supporting case is ``CaseSource.HISTORICAL`` the
        emitted confidence is capped at ``self._historical_cap`` so
        that :func:`_mode_for` cannot promote the rule into
        ``ExecutionMode.AUTO`` on bootstrap evidence alone.
        """
        counterexample_count = max(0, total - support)
        params = _consensus_params(cases)
        risk = RiskLevel.MEDIUM
        last_seen = max(case.created_at for case in cases)
        # ali.md §10 multi-factor formula — counterexample +
        # staleness penalties applied here; conflict /
        # hidden_variable signals are validator-side and stay 0.
        # Sprint 6 W6 — fold §5 signal counts (PM approvals + PM
        # edits) into the context so confidence reflects how
        # often the PM has corroborated the rule.  Legacy cases
        # collapse to the pre-W6 formula.
        signal_counts = _count_signal_weights(cases)
        adjusted_confidence = compute_confidence(
            support,
            total,
            ConfidenceContext(
                counterexample_count=counterexample_count,
                last_seen_at=last_seen,
                **signal_counts,
            ),
        )
        effective_confidence = adjusted_confidence
        if _all_historical(cases) and effective_confidence > self._historical_cap:
            effective_confidence = self._historical_cap
        execution_mode = _mode_for(effective_confidence, risk, support)
        source_ids = tuple(case.case_id for case in cases)
        # Mümin-feedback parity with the API extractor:
        # deterministic identity collapse (so re-bootstraps UPSERT
        # one row instead of N orphans) + human-readable rationale
        # round-tripped through ``action.params['_rationale']``.
        rationale = _build_rationale(
            scenario=scenario,
            action_type=action_type,
            conditions={},
            support=support,
            counterexamples=counterexample_count,
            cases=cases,
        )
        stable_id = PatternRule.deterministic_id(
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
            action_type=action_type,
            conditions={},
        )
        return PatternRule(
            pattern_id=stable_id,
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
            conditions={},
            action=DecisionAction(
                action_type=action_type,
                params={**params, "_rationale": rationale},
            ),
            support_count=support,
            counterexample_count=counterexample_count,
            confidence=round(effective_confidence, 4),
            risk_level=risk,
            stage=_dominant_stage(cases),
            execution_mode=execution_mode,
            source_case_ids=source_ids,
            last_seen_at=last_seen,
            rationale=rationale,
            active=True,
            origin=_origin_from_cases(cases),
            foundation_scenario_id=_dominant_foundation_id(cases),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_historical(cases: Iterable[DecisionCase]) -> bool:
    """Return ``True`` when every case was replayed from archive."""
    cases_list = list(cases)
    if not cases_list:
        return False
    return all(case.source is CaseSource.HISTORICAL for case in cases_list)


def _scope_id_for(case: DecisionCase, scope: PatternScope) -> str:
    """Return the identifier of the supplied ``scope`` on ``case``."""
    if scope is PatternScope.PROPERTY:
        return case.property_id
    if scope is PatternScope.OWNER:
        return case.owner_id
    if scope is PatternScope.PORTFOLIO:
        return case.owner_id
    if scope is PatternScope.GUEST:
        return case.guest_id or ""
    return ""


def _consensus_params(cases: list[DecisionCase]) -> dict[str, object]:
    """Keep only parameter keys that agree across every supporting case.

    Parameters that differ between cases are dropped so the generated
    rule never commits to a value the evidence does not unanimously
    support.  Callers that need per-case parameter variation can look
    at ``source_case_ids`` on the returned rule.
    """
    if not cases:
        return {}
    first = dict(cases[0].decision.params)
    if not first:
        return {}
    shared: dict[str, object] = {}
    for key, value in first.items():
        if all(case.decision.params.get(key) == value for case in cases[1:]):
            shared[key] = value
    return shared


def _mode_for(
    confidence: float,
    risk: RiskLevel,
    support: int,
) -> ExecutionMode:
    """Map ``(confidence, risk, support)`` into an execution mode."""
    if risk is RiskLevel.CRITICAL:
        return ExecutionMode.BLOCK
    if (
        confidence >= CONFIDENCE_AUTO_THRESHOLD
        and support >= MIN_SUPPORT_AUTO
        and risk in (RiskLevel.LOW, RiskLevel.MEDIUM)
    ):
        return ExecutionMode.AUTO
    if confidence >= CONFIDENCE_ASK_THRESHOLD:
        return ExecutionMode.ASK
    return ExecutionMode.APPROVAL


# ``MAX_COUNTEREXAMPLE_RATIO`` is re-exported only so downstream
# callers can gate rule activation without re-importing from
# ``patterns.models``.
__all__ += ["MAX_COUNTEREXAMPLE_RATIO"]


# ---------------------------------------------------------------------------
# Bi-temporal contradiction resolution (Sprint 1 — Graphiti port)
# ---------------------------------------------------------------------------


def _conditions_overlap(
    a: dict[str, object],
    b: dict[str, object],
) -> bool:
    """Return ``True`` when two condition dicts can match the same case.

    Used to decide whether two sibling rules (same scope+scenario,
    different ``action_type``) actually contradict each other or merely
    cover disjoint slices of the feature space.

    The check is conservative — when overlap is *uncertain* (mixed
    operators, unknown shape) we return ``True`` so the resolver still
    considers them, then the caller's validity-interval test (see
    :func:`_resolve_pattern_rule_contradictions`) decides whether to
    invalidate.  Returning ``False`` only when we are confident there
    is no overlap means the resolver never *misses* a real
    contradiction.
    """
    # Empty conditions match everything → guaranteed overlap with
    # anything in the same scope+scenario bucket.
    if not a or not b:
        return True
    # If either side fully subsumes the other (broader covers narrower),
    # the two slices overlap on the narrower side.
    if _conditions_subsume(a, b) or _conditions_subsume(b, a):
        return True
    # Neither subsumes the other — check shared keys for definitive
    # disjointness on the gte/lte/eq operators we actually emit.
    shared = set(a.keys()) & set(b.keys())
    for key in shared:
        a_cond = a[key]
        b_cond = b[key]
        if not isinstance(a_cond, dict) or not isinstance(b_cond, dict):
            if a_cond != b_cond:
                return False
            continue
        a_op = a_cond.get("operator")
        b_op = b_cond.get("operator")
        a_val = a_cond.get("value")
        b_val = b_cond.get("value")
        if a_op == "eq" and b_op == "eq" and a_val != b_val:
            return False
        if (
            a_op == "gte"
            and b_op == "lte"
            and isinstance(a_val, (int, float))
            and isinstance(b_val, (int, float))
            and a_val > b_val
        ):
            return False
        if (
            a_op == "lte"
            and b_op == "gte"
            and isinstance(a_val, (int, float))
            and isinstance(b_val, (int, float))
            and b_val > a_val
        ):
            return False
    # No definitive disjointness found → assume overlap.
    return True


def _resolve_pattern_rule_contradictions(
    new_rule: PatternRule,
    candidates: list[PatternRule],
) -> list[PatternRule]:
    """Mark older rules invalid when a newer one supplants them.

    Direct port of Graphiti's ``resolve_edge_contradictions``
    (``graphiti_core/utils/maintenance/edge_operations.py:537-572``,
    arXiv 2501.13956 §3.2) adapted to PatternRule's structured
    identity tuple — **no LLM is involved**.

    A candidate is contradictory when it shares scope, scope_id and
    scenario with ``new_rule`` but commits to a *different*
    ``action_type`` over an *overlapping* condition slice.  When the
    candidate started earlier (in application time) than the new
    rule, we close it: ``invalid_at`` records when the world shifted
    (set to ``new_rule.valid_from``); ``deactivated_at`` records when
    Brain Engine learned (set to ``utc_now()``).  ``active`` is
    flipped to ``False`` for fast index scans.

    Returns the list of rules that were modified — callers persist
    them via the existing ``rule_store.store`` UPSERT path.

    Mirrors Graphiti's two-scale clarity:
      * ``invalid_at`` (T-scale)  — what happened in the world
      * ``deactivated_at`` (T'-scale) — what the system noticed

    Pure compute, idempotent, safe to re-run.
    """
    if not candidates:
        return []

    invalidated: list[PatternRule] = []
    new_valid = new_rule.valid_from
    new_invalid = new_rule.invalid_at

    for cand in candidates:
        # Skip identical-identity self-match (deterministic_id collapse
        # already handles update-in-place; never invalidate yourself).
        if cand.pattern_id == new_rule.pattern_id:
            continue
        # Skip cross-scope / cross-scenario candidates — caller is
        # expected to pre-filter, but defend against API misuse.
        if (
            cand.scope is not new_rule.scope
            or cand.scope_id != new_rule.scope_id
            or cand.scenario is not new_rule.scenario
        ):
            continue
        # Same action_type → not a contradiction, just an update path.
        if cand.action.action_type is new_rule.action.action_type:
            continue
        # Already invalidated and deactivated — leave alone.
        if cand.deactivated_at is not None and not cand.active:
            continue
        # Disjoint condition slices → both can co-exist (e.g.
        # lead_time_hours >= 120 → defer + lead_time_hours < 48 →
        # inform: temporal split per ali.md §3, NOT a contradiction).
        if not _conditions_overlap(cand.conditions, new_rule.conditions):
            continue
        # Validity-interval overlap check (Graphiti algorithm).
        cand_invalid = cand.invalid_at
        cand_valid = cand.valid_from
        if cand_invalid is not None and new_valid is not None and cand_invalid <= new_valid:
            continue
        if cand_valid is not None and new_invalid is not None and new_invalid <= cand_valid:
            continue
        # Older candidate → invalidate.  ``PatternRule`` is frozen
        # (per master_guide_2026 — value objects must be immutable),
        # so we emit a replacement via ``dataclasses.replace`` and
        # let the caller persist it through the existing UPSERT path.
        if cand_valid is not None and new_valid is not None and cand_valid < new_valid:
            invalidated.append(
                replace(
                    cand,
                    invalid_at=new_valid,
                    deactivated_at=(cand.deactivated_at if cand.deactivated_at is not None else datetime.now(UTC)),
                    active=False,
                ),
            )

    return invalidated


__all__ += [
    "_resolve_pattern_rule_contradictions",
]


def _emit_mine_metrics(
    *,
    rules: list[PatternRule],
    attempts: int,
    rejected: int,
) -> None:
    """Forward miner outcomes to the Prometheus exporter.

    Best-effort — any exporter exception is swallowed so a broken
    metrics registry can never block rule mining.  The function
    folds three series: cases ingested are counted upstream by the
    case-store; this hook reports rules emitted (split by
    conditional vs baseline), synthesis attempts, and rejects.
    """
    # Port note: the reference forwarded these counts to its own
    # Prometheus exporter (brain_engine.observability — retired, see
    # PORTING_MAP).  Metrics re-land on Dify's observability surface
    # with the runtime wiring (Batch 4/5); the hook stays so call
    # sites and tests keep their shape.
    _ = (rules, attempts, rejected)
