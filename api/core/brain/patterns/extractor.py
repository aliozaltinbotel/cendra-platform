"""Pattern extraction — mines PatternRules from accumulated DecisionCases.

PatternExtractor is the core learning algorithm.  It:

1. Retrieves all cases for a (scenario, property, owner) scope.
2. Separates positive (successful, not overridden) from negative cases.
3. Infers conditions by finding common feature values in positive cases
   that differ from negative cases.
4. Computes confidence, risk, and execution mode.
5. Produces candidate PatternRules for validation.

The algorithm is intentionally conservative: it requires a minimum
number of supporting cases, penalises counterexamples, and refuses to
promote rules for critical-risk scenarios.  One-off exceptions (single
positive case) are never turned into durable rules.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, TypedDict

from core.brain.patterns.condition_synthesizer import (
    _resolve_feature_keys,
)
from core.brain.patterns.models import (
    CONFIDENCE_ASK_THRESHOLD,
    CONFIDENCE_AUTO_THRESHOLD,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ExecutionMode,
    PatternOrigin,
    PatternRule,
    PatternScope,
    RiskLevel,
)
from core.brain.patterns.store import DecisionCaseStore
from core.brain.patterns.wilson import wilson_lower_bound

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_SUPPORT_DEFAULT: Final[int] = 3
_MIN_SUPPORT_DEFER: Final[int] = 1
_MIN_CONFIDENCE_DEFAULT: Final[float] = 0.6
_MAX_EXTRACTION_CASES: Final[int] = 500
_DOMINANCE_THRESHOLD: Final[float] = 0.75

# Mümin round-4 #5b: closes the "ugly threshold" degeneracy where
# ``_infer_numeric_condition`` produced rules like
# ``hours_before_checkin gte -1185.65`` (~49 days into the stay,
# clearly noise) on tiny one-sided positive pools.
#
# Each entry constrains the *threshold* the function may emit on the
# corresponding feature.  Features absent from this table are not
# bound — the function falls back to its prior behaviour.  Keys MUST
# match the snapshot keys defined in
# :mod:`core.brain.patterns.condition_synthesizer._PMS_KEYS` /
# ``_CALENDAR_KEYS`` / ``_GUEST_KEYS`` so the registry and the
# extractor stay in lock-step.
_NUMERIC_DOMAIN_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    # Signed hours from "now" to check-in; legit range is from
    # ~30 days before to ~30 days into the stay.
    "hours_before_checkin": (-720.0, 720.0),
    # Booking lead time: 0 hours (same-minute) to ~1 year ahead.
    "lead_time_hours": (0.0, 8760.0),
    # Calendar gap windows: bounded by the same 30-day horizon.
    "gap_before": (0.0, 720.0),
    "gap_after": (0.0, 720.0),
    # Occupancy proportions are by construction in ``[0, 1]``.
    "occupancy_7d": (0.0, 1.0),
    "occupancy_30d": (0.0, 1.0),
    # PMS party-size fields.  Match short-stay-rental realism, not
    # corporate-events / hostel-bed-bunks corners.
    "adults": (1.0, 16.0),
    "children": (0.0, 10.0),
    "infants": (0.0, 5.0),
    "nights": (1.0, 365.0),
    # Currency-denominated fields.  100k is the per-stay-line ceiling
    # we have ever observed in the dev DB; anything above is data
    # noise or a wrong-currency import.
    "total_price": (0.0, 100_000.0),
    "adr": (0.0, 5_000.0),
    # Calendar booleans encoded as counts.
    "weekday_count": (0.0, 5.0),
    "weekend_count": (0.0, 2.0),
    # Guest profile fields.
    "total_bookings": (0.0, 1_000.0),
    "total_incidents": (0.0, 100.0),
    "rating": (0.0, 5.0),
}

# Minimum positive-pool size for the one-sided ``gte`` branch
# (``neg_values`` empty).  Below this, the median is statistically
# unreliable — the round-4 closing log noted the
# ``hours_before_checkin gte -4197.65`` rule had only 6 supporting
# cases; we now require the same ``_MIN_SUPPORT_DEFAULT`` floor that
# the rest of the extractor already applies.
_MIN_NUMERIC_ONE_SIDED_SUPPORT: Final[int] = _MIN_SUPPORT_DEFAULT


def _threshold_within_domain(name: str, threshold: float) -> bool:
    """Return whether ``threshold`` is admissible for feature ``name``.

    Features without an entry in :data:`_NUMERIC_DOMAIN_BOUNDS` are
    considered unbounded and always admit any finite threshold.
    Non-finite thresholds (NaN / ±inf) are rejected unconditionally
    so a pathological positive pool cannot pollute the rule store.
    """
    if threshold != threshold or threshold in (
        float("inf"),
        float("-inf"),
    ):
        return False
    bound = _NUMERIC_DOMAIN_BOUNDS.get(name)
    if bound is None:
        return True
    low, high = bound
    return low <= threshold <= high


# Vertical vocabulary note (genericised at port time, golden rule 4): the
# reference pinned HIGH_RISK_SCENARIOS (and an inline medium-risk pricing
# set in _classify_risk) as kernel frozensets of hospitality scenarios.
# Risk vocabularies are pack / tenant data now — injected per extractor via
# the high_risk_scenarios / medium_risk_scenarios constructor params (see
# packs/hospitality/risk_scenarios.yaml for vertical #1's sets).

CRITICAL_RISK_DECISION_TYPES: Final[frozenset[DecisionType]] = frozenset(
    {
        DecisionType.CHARGE,
        DecisionType.BLOCK,
        DecisionType.RELEASE,
    }
)


# ---------------------------------------------------------------------------
# Extraction result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Per-action outcome of the extract pipeline's gating logic.

    One :class:`GateDecision` is emitted for each action group that
    held at least one supporting case — whether the group passed the
    support and confidence gates (``accepted=True`` → a rule was
    formed) or was rejected by one of them (``accepted=False`` →
    ``reason`` names which gate failed).

    Mümin 2026-05-08 round-3 feedback: the existing ``skipped_reasons``
    tuple holds strings like ``"action_inform_confidence_0.43_below_0.60"``
    that surface *why* a candidate was dropped, but they require regex
    parsing to read and they only appear for skips — accepted actions
    leave no audit trail.  Surfacing the structured GateDecision
    closes both halves of the explainability gap: the PM can see
    every action's support / counterexamples / confidence vs. the
    threshold without parsing strings, including the actions that
    *did* form a rule.

    Attributes:
        action: Action type that the group represents (e.g.
            ``"inform"``, ``"defer"``, ``"deny"``).  Mirrors
            :class:`DecisionType` values without the enum import so
            the dataclass stays JSON-friendly.
        accepted: ``True`` when the gate produced a rule for this
            action; ``False`` when one of the gates rejected the
            group.
        reason: Machine-friendly tag identifying the failed gate when
            ``accepted=False`` — currently one of
            ``"insufficient_support"`` or ``"low_confidence"``.
            ``None`` for accepted groups.
        support_count: Number of positive cases in the group.
        counterexample_count: Negative cases evaluated against the
            group.  Constant across actions (the negative pool is
            shared) but included on each decision for self-contained
            interpretation.
        confidence: Computed confidence score in ``[0.0, 1.0]``, or
            ``None`` when the support gate failed before confidence
            was computed.
        min_support: Support threshold the group was checked against
            — :data:`_MIN_SUPPORT_DEFER` for DEFER, the configured
            ``min_support`` otherwise.
        min_confidence: Confidence threshold the group was checked
            against (``min_confidence`` from extractor config).
    """

    action: str
    accepted: bool
    reason: str | None
    support_count: int
    counterexample_count: int
    confidence: float | None
    min_support: int
    min_confidence: float


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Outcome of a pattern extraction run.

    Attributes:
        rules: Candidate PatternRules extracted.
        total_cases: Number of cases analysed.
        positive_cases: Number of positive-signal cases.
        negative_cases: Number of negative-signal cases.
        defer_count: Number of cases the PM deferred (recorded as
            DecisionType.DEFER).  Reported separately from
            negative_cases because deferring is *not* a refusal — it
            is the "wait and revisit" pattern from ali.md §7 and
            mumi-feedback Phase 4 ("5 days before check-in is too
            early — answer later").  Surfacing the count makes it
            visible whenever the per-action support threshold
            (1 for DEFER, ``_min_support`` for everything else)
            keeps the deferred bucket from forming its own rule.
        skipped_reasons: Reasons why some groups were skipped.
            Backward-compatible string surface, kept alongside
            :attr:`gate_decisions` so callers that have not migrated
            to the structured form keep working.
        gate_decisions: Structured per-action gate outcomes — one
            entry for every action group that held at least one
            positive case, regardless of whether it formed a rule.
            Empty when the pipeline short-circuited before per-action
            grouping (no learnable cases or insufficient overall
            support); :attr:`skipped_reasons` covers those cases.
    """

    rules: tuple[PatternRule, ...] = ()
    total_cases: int = 0
    positive_cases: int = 0
    negative_cases: int = 0
    defer_count: int = 0
    skipped_reasons: tuple[str, ...] = ()
    gate_decisions: tuple[GateDecision, ...] = ()


# ---------------------------------------------------------------------------
# PatternExtractor
# ---------------------------------------------------------------------------


class PatternExtractor:
    """Extracts PatternRules from accumulated DecisionCases.

    This class implements the statistical learning pipeline that turns
    raw operational decisions into structured, confidence-scored rules.

    Attributes:
        _store: DecisionCase persistence.
        _min_support: Minimum positive cases to form a rule.
        _min_confidence: Minimum confidence threshold.
        _log: Bound structured logger.
    """

    def __init__(
        self,
        store: DecisionCaseStore,
        *,
        min_support: int = _MIN_SUPPORT_DEFAULT,
        min_confidence: float = _MIN_CONFIDENCE_DEFAULT,
        high_risk_scenarios: frozenset[str] = frozenset(),
        medium_risk_scenarios: frozenset[str] = frozenset(),
    ) -> None:
        self._store = store
        self._min_support = min_support
        self._min_confidence = min_confidence
        self._high_risk_scenarios = high_risk_scenarios
        self._medium_risk_scenarios = medium_risk_scenarios

    def extract_patterns(
        self,
        *,
        scenario: str,
        property_id: str,
        owner_id: str,
    ) -> ExtractionResult:
        """Run the full extraction pipeline for a given scope.

        Args:
            scenario: Operational scenario to analyse.
            property_id: Property identifier.
            owner_id: Owner identifier.

        Returns:
            ExtractionResult with candidate rules and statistics.
        """
        cases = self._store.search(
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            limit=_MAX_EXTRACTION_CASES,
        )

        learnable = [c for c in cases if c.is_learnable]
        if not learnable:
            return ExtractionResult(
                skipped_reasons=("no_learnable_cases",),
            )

        positive, negative = self._split_by_signal(learnable)

        logger.info(
            "extraction_started scenario=%s property_id=%s total=%s positive=%s negative=%s",
            scenario,
            property_id,
            len(learnable),
            len(positive),
            len(negative),
        )

        if len(positive) < self._min_support:
            return ExtractionResult(
                total_cases=len(learnable),
                positive_cases=len(positive),
                negative_cases=len(negative),
                skipped_reasons=(f"insufficient_support: {len(positive)} < {self._min_support}",),
            )

        grouped = self._group_by_action(positive)
        rules: list[PatternRule] = []
        skipped: list[str] = []
        gate_decisions: list[GateDecision] = []
        defer_count = sum(1 for c in positive if c.decision.action_type is DecisionType.DEFER)

        for action_key, group_cases in grouped.items():
            # Per-action support threshold: DEFER consistently uses a
            # lower bar (``_MIN_SUPPORT_DEFER``) because deferring is
            # the absence-of-action signal from ali.md §7 / mumi-
            # feedback Phase 4 — even one consistent DEFER is policy
            # worth surfacing, whereas an active decision needs the
            # broader statistical floor.
            min_support = _MIN_SUPPORT_DEFER if action_key == DecisionType.DEFER.value else self._min_support
            if len(group_cases) < min_support:
                skipped.append(
                    f"action_{action_key}_support_{len(group_cases)}_below_{min_support}",
                )
                gate_decisions.append(
                    GateDecision(
                        action=action_key,
                        accepted=False,
                        reason="insufficient_support",
                        support_count=len(group_cases),
                        counterexample_count=len(negative),
                        confidence=None,
                        min_support=min_support,
                        min_confidence=self._min_confidence,
                    ),
                )
                continue

            conditions = self._infer_conditions(group_cases, negative)
            confidence = self._compute_confidence(
                len(group_cases),
                len(negative),
            )

            if confidence < self._min_confidence:
                skipped.append(
                    f"action_{action_key}_confidence_{confidence:.2f}_below_{self._min_confidence:.2f}",
                )
                gate_decisions.append(
                    GateDecision(
                        action=action_key,
                        accepted=False,
                        reason="low_confidence",
                        support_count=len(group_cases),
                        counterexample_count=len(negative),
                        confidence=round(confidence, 4),
                        min_support=min_support,
                        min_confidence=self._min_confidence,
                    ),
                )
                continue

            representative = group_cases[0]
            risk = self._determine_risk(
                scenario,
                representative.decision.action_type,
            )
            mode = self._determine_execution_mode(confidence, risk)

            # Deterministic identity tuple — repeated bootstraps over
            # the same data converge on one row instead of N orphans.
            stable_id = PatternRule.deterministic_id(
                scenario=scenario,
                scope=PatternScope.PROPERTY,
                scope_id=property_id,
                action_type=representative.decision.action_type,
                conditions=conditions,
            )

            source_ids = tuple(c.case_id for c in group_cases)
            rationale = _build_rationale(
                scenario=scenario,
                action_type=representative.decision.action_type,
                conditions=conditions,
                support=len(group_cases),
                counterexamples=len(negative),
                cases=group_cases,
            )
            # Embed rationale inside action.params["_rationale"] so the
            # postgres store (which serialises action as JSONB) round-
            # trips it without a schema migration.  The PatternRule
            # itself also carries ``rationale`` directly for API
            # responses produced in the same process — both stay in
            # sync because the _row_to_rule decoder pulls the value
            # out of params back onto the ``rationale`` field.
            decision_with_rationale = DecisionAction(
                action_type=representative.decision.action_type,
                params={
                    **representative.decision.params,
                    "_rationale": rationale,
                },
            )
            rule = PatternRule(
                pattern_id=stable_id,
                scenario=scenario,
                scope=PatternScope.PROPERTY,
                scope_id=property_id,
                conditions=conditions,
                action=decision_with_rationale,
                support_count=len(group_cases),
                counterexample_count=len(negative),
                confidence=round(confidence, 3),
                risk_level=risk,
                stage=_dominant_stage(group_cases),
                execution_mode=mode,
                source_case_ids=source_ids,
                last_seen_at=max(c.created_at for c in group_cases),
                rationale=rationale,
                origin=_origin_from_cases(group_cases),
                foundation_scenario_id=_dominant_foundation_id(group_cases),
            )
            rules.append(rule)
            gate_decisions.append(
                GateDecision(
                    action=action_key,
                    accepted=True,
                    reason=None,
                    support_count=len(group_cases),
                    counterexample_count=len(negative),
                    confidence=round(confidence, 4),
                    min_support=min_support,
                    min_confidence=self._min_confidence,
                ),
            )

        # Subsumption merge: drop rule X when there is a sibling
        # rule Y (same scenario + scope + action_type) whose
        # condition set is a *subset* of X's and whose support is
        # at least as large.  Y's broader-coverage condition fully
        # captures X's slice without the extra threshold noise that
        # ConditionSynthesizer emits when sample variance shifts the
        # numeric cut-off between runs.
        rules = _merge_subsumed_rules(rules)

        logger.info(
            "extraction_complete scenario=%s rules_extracted=%s skipped=%s defer_count=%s",
            scenario,
            len(rules),
            len(skipped),
            defer_count,
        )
        _emit_extract_metrics(rules)

        return ExtractionResult(
            rules=tuple(rules),
            total_cases=len(learnable),
            positive_cases=len(positive),
            negative_cases=len(negative),
            defer_count=defer_count,
            skipped_reasons=tuple(skipped),
            gate_decisions=tuple(gate_decisions),
        )

    # -------------------------------------------------------------------
    # Signal splitting
    # -------------------------------------------------------------------

    def _split_by_signal(
        self,
        cases: list[DecisionCase],
    ) -> tuple[list[DecisionCase], list[DecisionCase]]:
        """Separate cases into positive and negative signal groups.

        Cases with ambiguous outcomes (neither positive nor negative)
        are excluded from both groups.

        Args:
            cases: Learnable DecisionCases.

        Returns:
            Tuple of (positive_cases, negative_cases).
        """
        positive: list[DecisionCase] = []
        negative: list[DecisionCase] = []
        for case in cases:
            if case.outcome.is_positive_signal:
                positive.append(case)
            elif case.outcome.is_negative_signal:
                negative.append(case)
        return positive, negative

    # -------------------------------------------------------------------
    # Action grouping
    # -------------------------------------------------------------------

    def _group_by_action(
        self,
        cases: list[DecisionCase],
    ) -> dict[str, list[DecisionCase]]:
        """Group positive cases by their decision action type.

        Args:
            cases: Positive-signal cases.

        Returns:
            Dict mapping action_type value → list of cases.
        """
        groups: dict[str, list[DecisionCase]] = {}
        for case in cases:
            key = case.decision.action_type.value
            groups.setdefault(key, []).append(case)
        return groups

    # -------------------------------------------------------------------
    # Condition inference
    # -------------------------------------------------------------------

    def _infer_conditions(
        self,
        positive: list[DecisionCase],
        negative: list[DecisionCase],
    ) -> dict[str, Any]:
        """Infer discriminating conditions from positive vs. negative cases.

        For each feature present in positive cases, checks whether the
        values are sufficiently consistent (dominant) and different from
        negative cases.  Only features that pass both tests become rule
        conditions.

        Numeric features produce range conditions (gte/lte); categorical
        features produce equality conditions.

        Args:
            positive: Cases where the action succeeded.
            negative: Cases where the action failed or was overridden.

        Returns:
            Dict of condition definitions (field → operator/value).
        """
        conditions: dict[str, Any] = {}

        # Sprint H wiring (Mümin 2026-05-08 round-3 follow-up): the
        # /patterns/extract path was bypassing
        # ``core.brain.patterns.scenario_features.SCENARIO_FEATURES``
        # because :meth:`_collect_snapshot_features` walked every
        # snapshot key without consulting the per-scenario whitelist
        # — only :class:`ConditionSynthesizer` (used by the bootstrap
        # :class:`PatternMiner`) honoured the whitelist.  The
        # asymmetry meant rules mined through the API still surfaced
        # ``currency`` / ``total_price`` / ``source`` etc. for
        # whitelisted scenarios on dev even after the flag was on.
        # Pass the scenario in so both paths apply the same filter
        # — flag off keeps global defaults, so behaviour is
        # bit-for-bit identical to pre-fix when ``BRAIN_SCENARIO
        # _FEATURES_ENABLED`` is unset.  Property-agnostic.
        scenario = positive[0].scenario if positive else None
        pos_features = self._collect_snapshot_features(positive, scenario)
        neg_features = self._collect_snapshot_features(negative, scenario)

        for feature_name, pos_values in pos_features.items():
            if not pos_values:
                continue

            neg_values = neg_features.get(feature_name, [])

            if all(isinstance(v, (int, float)) for v in pos_values):
                condition = self._infer_numeric_condition(
                    feature_name,
                    pos_values,
                    neg_values,
                )
                if condition is not None:
                    conditions[feature_name] = condition
            elif all(isinstance(v, str) for v in pos_values):
                condition = self._infer_categorical_condition(
                    feature_name,
                    pos_values,
                    neg_values,
                )
                if condition is not None:
                    conditions[feature_name] = condition
            elif all(isinstance(v, bool) for v in pos_values):
                condition = self._infer_boolean_condition(
                    feature_name,
                    pos_values,
                    neg_values,
                )
                if condition is not None:
                    conditions[feature_name] = condition

        return conditions

    def _infer_numeric_condition(
        self,
        name: str,
        pos_values: list[Any],
        neg_values: list[Any],
    ) -> dict[str, Any] | None:
        """Infer a numeric range condition.

        If positive values cluster above negative values, produce a GTE
        condition at the positive median.  If below, produce LTE.

        Mümin round-4 #5b: rejects degenerate thresholds before they
        ever land in the rule store.  A candidate is rejected when:

        * the feature has a registered domain in
          :data:`_NUMERIC_DOMAIN_BOUNDS` and the proposed threshold
          falls outside the bound; or
        * ``neg_values`` is empty and the positive pool is smaller
          than :data:`_MIN_NUMERIC_ONE_SIDED_SUPPORT` — there is not
          enough evidence to publish a one-sided rule.

        Unknown features (not in the registry) keep the prior
        behaviour: the function trusts the median and lets the
        Wilson + conformal gates downstream filter noise.

        Args:
            name: Feature name.
            pos_values: Numeric values from positive cases.
            neg_values: Numeric values from negative cases.

        Returns:
            Condition dict or None if not discriminating or the
            proposed threshold fails a domain-quality guard.
        """
        if not neg_values:
            if len(pos_values) < _MIN_NUMERIC_ONE_SIDED_SUPPORT:
                return None
            threshold = round(
                float(statistics.median(pos_values)),
                2,
            )
            if not _threshold_within_domain(name, threshold):
                return None
            return {"operator": "gte", "value": threshold}

        pos_median = statistics.median(pos_values)
        neg_median = statistics.median(neg_values)
        if pos_median > neg_median:
            threshold = round(float(pos_median), 2)
            if not _threshold_within_domain(name, threshold):
                return None
            return {"operator": "gte", "value": threshold}
        if pos_median < neg_median:
            threshold = round(float(pos_median), 2)
            if not _threshold_within_domain(name, threshold):
                return None
            return {"operator": "lte", "value": threshold}
        return None

    def _infer_categorical_condition(
        self,
        name: str,
        pos_values: list[Any],
        neg_values: list[Any],
    ) -> dict[str, Any] | None:
        """Infer a categorical equality condition.

        If one value dominates positive cases (>= 75%), and that value
        is less common in negative cases, produce an EQ condition.

        Args:
            name: Feature name.
            pos_values: String values from positive cases.
            neg_values: String values from negative cases.

        Returns:
            Condition dict or None.
        """
        counter = Counter(pos_values)
        most_common_value, most_common_count = counter.most_common(1)[0]
        dominance = most_common_count / len(pos_values)

        if dominance < _DOMINANCE_THRESHOLD:
            return None

        neg_counter = Counter(neg_values)
        neg_ratio = neg_counter.get(most_common_value, 0) / len(neg_values) if neg_values else 0

        if neg_ratio < dominance * 0.5:
            return {"operator": "eq", "value": most_common_value}
        return None

    def _infer_boolean_condition(
        self,
        name: str,
        pos_values: list[Any],
        neg_values: list[Any],
    ) -> dict[str, Any] | None:
        """Infer a boolean condition.

        If positive cases dominantly have True and negative have False
        (or vice versa), produce an EQ condition.

        Args:
            name: Feature name.
            pos_values: Boolean values from positive cases.
            neg_values: Boolean values from negative cases.

        Returns:
            Condition dict or None.
        """
        pos_true_ratio = sum(1 for v in pos_values if v) / len(pos_values)

        if pos_true_ratio >= _DOMINANCE_THRESHOLD:
            if neg_values:
                neg_true_ratio = sum(1 for v in neg_values if v) / len(neg_values)
                if neg_true_ratio < 0.5:
                    return {"operator": "eq", "value": True}
            else:
                return {"operator": "eq", "value": True}

        if pos_true_ratio <= (1 - _DOMINANCE_THRESHOLD):
            if neg_values:
                neg_true_ratio = sum(1 for v in neg_values if v) / len(neg_values)
                if neg_true_ratio > 0.5:
                    return {"operator": "eq", "value": False}
            else:
                return {"operator": "eq", "value": False}

        return None

    def _collect_snapshot_features(
        self,
        cases: list[DecisionCase],
        scenario: str | None = None,
    ) -> dict[str, list[Any]]:
        """Collect feature values from case snapshots.

        Extracts numeric and categorical values from PMS, calendar, and
        ops snapshots for statistical analysis.

        When ``scenario`` is supplied and Sprint H is active, the
        per-scenario whitelist in
        :data:`core.brain.patterns.scenario_features.SCENARIO_FEATURES`
        is consulted via
        :func:`core.brain.patterns.condition_synthesizer._resolve_feature_keys`
        and only allow-listed keys are collected.  When the Sprint H
        flag is off (or ``scenario`` is None), the helper returns the
        global defaults so behaviour is bit-for-bit identical to
        pre-Sprint-H.

        Args:
            cases: List of DecisionCases.
            scenario: Optional scenario hint used to resolve the
                whitelist.  ``None`` falls back to the global feature
                surface.

        Returns:
            Dict mapping feature_name → list of observed values.
        """
        if scenario is not None:
            pms_keys, calendar_keys, _ = _resolve_feature_keys(scenario)
            pms_allowed = frozenset(pms_keys)
            calendar_allowed = frozenset(calendar_keys)
        else:
            pms_allowed = None
            calendar_allowed = None
        features: dict[str, list[Any]] = {}
        for case in cases:
            for snapshot, allowed in (
                (case.pms_snapshot, pms_allowed),
                (case.calendar_snapshot, calendar_allowed),
                (case.ops_snapshot, None),
            ):
                for key, value in snapshot.items():
                    if value is None:
                        continue
                    if allowed is not None and key not in allowed:
                        continue
                    if isinstance(value, (int, float, str, bool)):
                        features.setdefault(key, []).append(value)
        return features

    # -------------------------------------------------------------------
    # Confidence, risk, execution mode
    # -------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(
        support: int,
        counter: int,
    ) -> float:
        """Compute rule confidence as the Wilson 95 % lower bound.

        The Wilson score lower bound on the true success rate is the
        statistically-sound replacement for the previous Laplace
        ``(k + 1) / (k + c + 2)`` smoothing.  Laplace reports ``0.80``
        for ``3/0``, whereas the Wilson LB reports ``≈ 0.44`` — the
        latter correctly reflects that three positive observations do
        not justify high confidence.

        The returned value feeds ``execution_mode`` assignment and the
        ``min_confidence`` extraction floor, keeping a single confidence
        definition across the learning subsystem.  See
        :mod:`core.brain.patterns.wilson` for the reference formula.

        Args:
            support: Number of positive cases.
            counter: Number of negative cases.

        Returns:
            Confidence score in ``[0.0, 1.0]``.
        """
        total = support + counter
        if total == 0:
            return 0.0
        return wilson_lower_bound(support, total)

    def _determine_risk(
        self,
        scenario: str,
        action_type: DecisionType,
    ) -> RiskLevel:
        """Classify risk level for a scenario + action combination.

        Args:
            scenario: Operational scenario.
            action_type: Decision type.

        Returns:
            Appropriate RiskLevel.
        """
        if scenario in self._high_risk_scenarios:
            return RiskLevel.HIGH
        if action_type in CRITICAL_RISK_DECISION_TYPES:
            return RiskLevel.HIGH
        if scenario in self._medium_risk_scenarios:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _determine_execution_mode(
        self,
        confidence: float,
        risk: RiskLevel,
    ) -> ExecutionMode:
        """Map confidence and risk to an execution mode.

        Higher risk raises the confidence threshold needed for AUTO
        mode.  CRITICAL risk always requires APPROVAL.

        Args:
            confidence: Statistical confidence (0.0-1.0).
            risk: Risk classification.

        Returns:
            Appropriate ExecutionMode.
        """
        if risk == RiskLevel.CRITICAL:
            return ExecutionMode.APPROVAL

        if risk == RiskLevel.HIGH:
            if confidence >= 0.95:
                return ExecutionMode.ASK
            return ExecutionMode.APPROVAL

        if confidence >= CONFIDENCE_AUTO_THRESHOLD:
            return ExecutionMode.AUTO
        if confidence >= CONFIDENCE_ASK_THRESHOLD:
            return ExecutionMode.ASK
        return ExecutionMode.APPROVAL


def _merge_subsumed_rules(
    rules: list[PatternRule],
) -> list[PatternRule]:
    """Drop rules whose conditions are dominated by another sibling.

    Two rules are siblings when they share scenario, scope, scope_id
    and ``action.action_type``.  Within a sibling group, rule X is
    *subsumed* by rule Y when:

    * Y's condition keys are a subset of X's keys, **and**
    * for every shared key the operator/value pair makes Y the
      strictly broader threshold (``gte`` with smaller value, ``lte``
      with larger value, equality with the same value).

    When subsumption holds and Y's support is at least as large as
    X's, X is dropped — Y already covers the slice X claims with
    additional sample backing.  This collapses the redundant
    ``total_price >= 26`` / ``total_price >= 29`` style overlap that
    Mümin flagged on property 323133 without losing any semantically
    distinct rule.

    Rules with empty conditions are kept regardless — they encode
    "the dominant action for this scenario / action_type" and stand
    apart from any conditional sibling.
    """
    if len(rules) <= 1:
        return rules
    keep_idx = set(range(len(rules)))
    for i, x in enumerate(rules):
        if i not in keep_idx:
            continue
        for j, y in enumerate(rules):
            if i == j or j not in keep_idx:
                continue
            if y.scenario is not x.scenario:
                continue
            if y.scope is not x.scope or y.scope_id != x.scope_id:
                continue
            if y.action.action_type is not x.action.action_type:
                continue
            x_covers_y = _conditions_subsume(x.conditions, y.conditions)
            y_covers_x = _conditions_subsume(y.conditions, x.conditions)
            if x_covers_y and not y_covers_x:
                # X is strictly broader than Y — Y is redundant.
                keep_idx.discard(j)
            elif y_covers_x and not x_covers_y:
                # Y is strictly broader; drop X and stop comparing X.
                keep_idx.discard(i)
                break
            elif x_covers_y and y_covers_x:
                # Conditions are equivalent (typically identical after
                # the deterministic-id collapse); keep the side with
                # the higher support, drop the other.  This is the
                # tie-break that prevents mutual elimination.
                if x.support_count >= y.support_count:
                    keep_idx.discard(j)
                else:
                    keep_idx.discard(i)
                    break
    return [r for i, r in enumerate(rules) if i in keep_idx]


def _conditions_subsume(
    broader: dict[str, Any],
    narrower: dict[str, Any],
) -> bool:
    """Return ``True`` when ``broader`` covers every case of ``narrower``.

    Empty ``broader`` always subsumes (matches every case).  Otherwise
    every key in ``broader`` must appear in ``narrower`` with a value
    that makes ``broader``'s threshold the strictly looser one for the
    operators ``gte`` / ``lte`` / ``eq``.  Anything else (mixed
    operators, unknown shape) returns ``False`` — we err on the side
    of keeping both rules rather than dropping a semantically
    distinct one.
    """
    if not broader:
        return True
    if not narrower:
        return False
    for key, b_cond in broader.items():
        n_cond = narrower.get(key)
        if n_cond is None:
            return False
        if not isinstance(b_cond, dict) or not isinstance(n_cond, dict):
            if b_cond != n_cond:
                return False
            continue
        b_op = b_cond.get("operator")
        n_op = n_cond.get("operator")
        if b_op != n_op:
            return False
        b_val = b_cond.get("value")
        n_val = n_cond.get("value")
        if b_op == "gte":
            if not (isinstance(b_val, (int, float)) and isinstance(n_val, (int, float)) and b_val <= n_val):
                return False
        elif b_op == "lte":
            if not (isinstance(b_val, (int, float)) and isinstance(n_val, (int, float)) and b_val >= n_val):
                return False
        elif b_op == "eq":
            if b_val != n_val:
                return False
        else:
            return False
    return True


# Mümin 2026-05-08 round-3 (complaint #J): the templated DEFER
# rationale "PM deferred (waited / did not respond) for {scenario}
# — {N} supporting case(s) — when {conditions}" is tautological.
# A PM reading the rule card needs to know *why* the deferral
# happened — was the PM waiting for an ID, a payment, owner
# approval, or a hard policy block?  The refusal_extractor already
# mines those signals from each PM message during bootstrap and
# stores them on ``case.extracted_entities['refusal_signals']``;
# this table maps the structured ``RefusalType`` to a phrase that
# fits inline in the rationale string.  Property-agnostic by
# construction — the rationale is built per-rule, not per-property.
_REFUSAL_LABELS: Final[dict[str, str]] = {
    "requires_document": "PM was waiting for a guest document (ID / passport / KYC)",
    "requires_payment": "PM was waiting for the guest payment to clear",
    "requires_approval": "PM was waiting for owner / manager approval",
    "hard_block": "the action is blocked by an explicit property policy",
    "generic_refusal": "the PM expressed a general refusal",
}

# DEFER and DENY are the rule types where "why" matters most — they
# represent inaction or refusal that the PM does not auto-explain.
# Other action types (INFORM / APPROVE / CHARGE / OFFER / ASK)
# already convey "what happened" through ``action_type`` itself.
_REFUSAL_AWARE_ACTIONS: Final[frozenset[DecisionType]] = frozenset(
    {
        DecisionType.DEFER,
        DecisionType.DENY,
        DecisionType.BLOCK,
    }
)


def _summarise_refusal_signals(
    cases: Iterable[DecisionCase],
) -> str:
    """Return a phrase naming the dominant refusal reason across ``cases``.

    Reads ``case.extracted_entities['refusal_signals']`` (populated
    during bootstrap by :class:`RefusalExtractor`) and aggregates by
    ``type``.  The most common refusal type wins; ties break
    deterministically by the type's enum-value ordering so identical
    inputs always yield identical rationales.

    The first non-empty ``conditional`` clause encountered for the
    winning type is appended as a concrete example so the PM can
    recognise the wording without inspecting individual cases.

    Returns an empty string when no signals are present — callers
    fall back to the structural rationale unchanged.
    """
    type_counts: Counter[str] = Counter()
    conditional_examples: dict[str, str] = {}
    for case in cases:
        signals = case.extracted_entities.get("refusal_signals")
        if not isinstance(signals, list):
            continue
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            refusal_type = signal.get("type")
            if not isinstance(refusal_type, str):
                continue
            type_counts[refusal_type] += 1
            conditional = signal.get("conditional")
            if refusal_type not in conditional_examples and isinstance(conditional, str) and conditional.strip():
                conditional_examples[refusal_type] = conditional.strip()
    if not type_counts:
        return ""
    # Stable ordering: count desc, then alphabetical so the winner
    # is reproducible across runs even when two types tie.
    dominant_type = min(
        type_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[0]
    label = _REFUSAL_LABELS.get(
        dominant_type,
        dominant_type.replace("_", " "),
    )
    example = conditional_examples.get(dominant_type)
    if example:
        return f'most often because {label} (e.g. "{example}")'
    return f"most often because {label}"


def _build_rationale(
    *,
    scenario: str,
    action_type: DecisionType,
    conditions: dict[str, Any],
    support: int,
    counterexamples: int,
    cases: Iterable[DecisionCase] | None = None,
) -> str:
    """Render a one-line human-readable explanation for a rule.

    The string is intentionally compact — Mümin's UI surfaces it
    inline next to the rule card and PMs read it at a glance.  When
    ``conditions`` is empty the rationale captures the unconditional
    dominant action ("PM consistently DEFER for access_code_release
    — 8 cases, 0 counterexamples").  For DEFER specifically, the
    phrasing makes the no-action semantics explicit so a reader does
    not assume the engine "did nothing"; this directly closes the
    Mümin-feedback gap where defer rules carried no rationale.

    When ``cases`` is supplied and ``action_type`` is one of the
    refusal-aware actions (DEFER / DENY / BLOCK), an additional
    "most often because …" clause is inserted citing the dominant
    refusal reason mined by :class:`RefusalExtractor` during
    bootstrap.  Closes Mümin 2026-05-08 round-3 complaint #J — the
    DEFER / DENY rationale was tautological without it.  The clause
    is omitted gracefully when no refusal signals are present so
    callers that have not migrated to passing ``cases`` keep the
    pre-J rationale verbatim.
    """
    if action_type is DecisionType.DEFER:
        verb = "deferred (waited / did not respond)"
    else:
        verb = f"chose {action_type.value}"
    parts = [
        f"PM {verb} for {scenario}",
        f"{support} supporting case(s)",
    ]
    if counterexamples:
        parts.append(f"{counterexamples} counterexample(s)")
    if cases is not None and action_type in _REFUSAL_AWARE_ACTIONS:
        semantic_reason = _summarise_refusal_signals(cases)
        if semantic_reason:
            parts.append(semantic_reason)
    if conditions:
        cond_strs: list[str] = []
        for key, cond in conditions.items():
            if isinstance(cond, dict):
                op = cond.get("operator", "eq")
                val = cond.get("value")
                cond_strs.append(f"{key} {op} {val!r}")
            else:
                cond_strs.append(f"{key} == {cond!r}")
        parts.append("when " + ", ".join(cond_strs))
    else:
        parts.append("(no discriminating conditions)")
    return " — ".join(parts)


class _SignalCounts(TypedDict):
    """Fixed key set for the ConfidenceContext signal splat (typing aid)."""

    pm_explicit_rule_count: int
    pm_repeated_edit_count: int
    pm_approval_count: int
    guest_complaint_count: int
    task_reopen_count: int
    vendor_sla_breach_count: int
    review_mention_count: int


def _count_signal_weights(
    cases: Iterable[DecisionCase],
) -> _SignalCounts:
    """Count Proactive §5 signal occurrences across rule-supporting cases.

    Returns a dict keyed by the
    :class:`core.brain.patterns.confidence.ConfidenceContext` field
    names so the caller can splat it directly into the constructor:
    ``ConfidenceContext(**signals, ...)``.

    The mapping from existing :class:`CaseOutcome` fields to §5
    signals (Sprint 6 W6 — conservative subset):

    * ``ResolutionType.PM_APPROVED`` → ``pm_approval_count``.  A PM
      action that explicitly confirmed the engine's decision.
    * ``ResolutionType.PM_MODIFIED`` → ``pm_repeated_edit_count``.
      A PM edit on the engine's response — strong correction
      signal per §5.

    All other §5 signals (``pm_explicit_rule``, ``guest_complaint``,
    ``task_reopen``, ``vendor_sla_breach``, ``review_mention``,
    ``contradiction_unresolved``) stay ``0`` / ``False`` until a
    downstream PR tags the corresponding upstream events on the
    case (e.g. FL-09 proactive signals populating
    ``DecisionCase.contributing_signal_ids``).

    Returns:
        Dict with the four FL-06 ``ConfidenceContext`` field names
        (``pm_explicit_rule_count``, ``pm_repeated_edit_count``,
        ``pm_approval_count``, ``guest_complaint_count``,
        ``task_reopen_count``, ``vendor_sla_breach_count``,
        ``review_mention_count``).  Every count defaults to ``0``
        so a caller that splats the dict gets the legacy formula
        when no signals are present.
    """
    counts: _SignalCounts = {
        "pm_explicit_rule_count": 0,
        "pm_repeated_edit_count": 0,
        "pm_approval_count": 0,
        "guest_complaint_count": 0,
        "task_reopen_count": 0,
        "vendor_sla_breach_count": 0,
        "review_mention_count": 0,
    }
    # Local import to avoid a circular import at module load time —
    # ``models`` already imports from ``confidence`` indirectly via
    # other helpers, and the wider extractor module imports
    # ``ResolutionType`` lazily wherever it needs it.
    from core.brain.patterns.models import ResolutionType

    for case in cases:
        resolution = case.outcome.resolution_type
        if resolution is ResolutionType.PM_APPROVED:
            counts["pm_approval_count"] += 1
        elif resolution is ResolutionType.PM_MODIFIED:
            counts["pm_repeated_edit_count"] += 1
    return counts


def _origin_from_cases(
    cases: Iterable[DecisionCase],
) -> PatternOrigin:
    """Build a :class:`PatternOrigin` from the rule's supporting cases (W5).

    Walks ``cases`` and aggregates three provenance dimensions
    from each contributing :class:`DecisionCase`:

    * ``foundation_scenario_ids`` — derived from each case's
      singular :pyattr:`DecisionCase.foundation_scenario_id`.
      Unique slugs, first-occurrence order.
    * ``source_event_ids`` — collected from
      :pyattr:`DecisionCase.origin.source_event_ids` (orchestrator
      event ids on the live path, conversation ids on the bootstrap
      path; both populated by PR-B).  Mümin 2026-05-15 round-5 #3
      — closes the empty
      ``GET /api/v1/patterns/rules/{rule_id}/origin.source_event_ids``
      complaint.  Without this aggregation the miner would drop the
      upstream identifiers even after PR-B persisted them on the
      case.
    * ``contributing_signal_ids`` — collected from
      :pyattr:`DecisionCase.origin.contributing_signal_ids`.
      Empty until FL-09 (deferred Proactive layer) lands; mirroring
      it here keeps the helper symmetric so future signal sources
      flow through without another miner change.

    Order is preserved (first occurrence wins) so a future audit
    replaying the same input sees the same origin tuple.

    Cases without provenance (legacy rows + rows that pre-date
    PR-B / FL-16 orchestrator wiring) contribute nothing — the
    helper silently skips empty values.  When *every* contributing
    case is legacy, the returned origin is empty and the consumer
    (the ``/rules/{id}/origin`` endpoint) renders that as "no
    provenance recorded" rather than a hard failure.

    Shared between :class:`PatternExtractor` (API extract path)
    and :class:`PatternMiner` (bootstrap path) so the origin trail
    stays bit-for-bit identical regardless of which entry point
    minted the rule.
    """
    foundation_seen: set[str] = set()
    foundation_ids: list[str] = []
    event_seen: set[str] = set()
    event_ids: list[str] = []
    signal_seen: set[str] = set()
    signal_ids: list[str] = []
    for case in cases:
        slug = case.foundation_scenario_id
        if slug and slug not in foundation_seen:
            foundation_seen.add(slug)
            foundation_ids.append(slug)
        case_origin = case.origin
        for event_id in case_origin.source_event_ids:
            if not event_id or event_id in event_seen:
                continue
            event_seen.add(event_id)
            event_ids.append(event_id)
        for signal_id in case_origin.contributing_signal_ids:
            if not signal_id or signal_id in signal_seen:
                continue
            signal_seen.add(signal_id)
            signal_ids.append(signal_id)
    return PatternOrigin(
        foundation_scenario_ids=tuple(foundation_ids),
        source_event_ids=tuple(event_ids),
        contributing_signal_ids=tuple(signal_ids),
    )


def _dominant_foundation_id(
    cases: Iterable[DecisionCase],
) -> str | None:
    """Return the most common ``foundation_scenario_id`` in ``cases``.

    Closes the last gap in the foundation provenance chain (PR #288
    wired the slug onto the case; this helper carries it onto the
    rule).  The miner / extractor call this when constructing the
    emitted :class:`PatternRule` so
    :pyattr:`PatternRule.foundation_scenario_id` always reflects the
    foundation scenario that produced the supporting cases.

    Rules:

    * Cases without a slug are skipped.  When *every* contributing
      case is legacy / pre-W1, the helper returns ``None`` and the
      rule's foundation field stays ``None`` — surfaced verbatim by
      ``/rules/{id}/origin``.
    * The strict-plurality slug wins.  Ties are resolved by first
      occurrence so the result is deterministic for a fixed case
      ordering — the miner's bucket iteration order is stable.

    Symmetric to :func:`_dominant_stage` so the two derived rule
    fields stay in lock-step semantics.
    """
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for index, case in enumerate(cases):
        slug = case.foundation_scenario_id
        if not slug:
            continue
        counts[slug] += 1
        first_seen.setdefault(slug, index)
    if not counts:
        return None
    # Tie-break: highest count first, then earliest first_seen index
    # so equal counts collapse deterministically.
    return min(
        counts.items(),
        key=lambda item: (-item[1], first_seen[item[0]]),
    )[0]


def _dominant_stage(
    cases: Iterable[DecisionCase],
) -> str | None:
    """Return the strict-majority booking stage among ``cases``.

    A stage is reported only when its count strictly exceeds every
    other stage in the supporting cases.  Ties — or any cross-stage
    spread without a unique top — collapse to ``None`` so the rule's
    ``stage`` field never overstates the evidence.  Empty input also
    yields ``None``.

    Shared helper used by both :class:`PatternExtractor` (the API
    extract path) and :class:`PatternMiner` (the bootstrap path) so
    stage derivation stays bit-for-bit identical regardless of entry
    point — same parity contract that ``_build_rationale`` and
    ``_merge_subsumed_rules`` enforce for rationale and subsumption.
    """
    counts = Counter(case.stage for case in cases)
    if not counts:
        return None
    most_common = counts.most_common(2)
    if len(most_common) == 1:
        return most_common[0][0]
    (top_stage, top_count), (_, runner_up_count) = most_common
    if top_count > runner_up_count:
        return top_stage
    return None


def _emit_extract_metrics(rules: list[PatternRule]) -> None:
    """Forward extracted rules to the Prometheus exporter.

    Mirror of ``pattern_miner._emit_mine_metrics`` so the same set
    of ``brain_patterns_rules_emitted_total`` series populates
    regardless of whether the extraction came through
    :class:`PatternExtractor` (the API endpoint path) or
    :class:`PatternMiner` (the orchestrator + nightly path).

    Best-effort — any exporter exception is swallowed so a broken
    metrics registry can never block extraction on the API path.
    """
    if not rules:
        return
    # Port note: the reference forwarded these counts to its own
    # Prometheus exporter (brain_engine.observability — retired, see
    # PORTING_MAP).  Metrics re-land on Dify's observability surface
    # with the runtime wiring (Batch 4/5); the hook stays so call
    # sites and tests keep their shape.
    return
