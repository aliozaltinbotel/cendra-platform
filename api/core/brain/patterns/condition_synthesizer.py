"""Synthesise conditions that explain when a minority action wins.

The plain :class:`PatternMiner` only emits an unconditional dominant
action per scenario.  In Mümin-style feedback the dominant action
might be ``DENY`` (8 of 10 coffee-capsule requests refused) while the
minority ``APPROVE`` cases (2 of 10) carry the actual learning
signal — they were all on ``Booking.com`` reservations above $1000.

This module mines that signal as a feature split.  For one
``(scenario, scope_id, target_action)`` bucket it compares the
*target* cases against every other case in the scenario and looks for
a feature condition (or a small conjunction of conditions) that

1. Matches a usable share of the target cases (``min_support_after``).
2. Excludes most counterexamples (``min_purity``).

Each emitted condition is a `{operator, value}` dict that
:meth:`PatternRule.matches_conditions` already knows how to evaluate,
so the synthesizer never touches runtime evaluation logic.

Pure compute: no I/O, no LLM, no async.  Greedy 1-2 level decision
splits — deeper trees would over-fit on the small per-property case
sets the cold-start replay produces.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Final

from core.brain.patterns.models import DecisionCase
from core.brain.patterns.scenario_features import (
    SCENARIO_FEATURES,
)

__all__ = [
    "DEFAULT_MAX_CONDITIONS",
    "DEFAULT_MIN_PURITY",
    "DEFAULT_MIN_SUPPORT_AFTER",
    "ConditionCandidate",
    "ConditionSynthesizer",
    "SynthesisReport",
    "SynthesisResult",
]


logger = logging.getLogger(__name__)


# Minimum precision (target_matched / total_matched) for a candidate
# condition to be considered.  0.8 means at most 20% of the cases
# admitted by the condition may belong to a competing action — the
# remaining 20% become the rule's counterexamples.
DEFAULT_MIN_PURITY: Final[float] = 0.8

# Minimum number of target cases that must remain matched after a
# condition is applied.  Without this floor the synthesizer would
# happily emit 100%-pure rules that fire on a single case.
DEFAULT_MIN_SUPPORT_AFTER: Final[int] = 2

# Hard cap on conjunctive depth.  Two thresholds (e.g. ``booking_value
# >= 1000`` AND ``booking_source == "Booking.com"``) is enough to
# express the Mümin coffee-capsule example without inviting overfit.
DEFAULT_MAX_CONDITIONS: Final[int] = 2

# Numeric quantile probes used when scanning thresholds.  The
# synthesizer evaluates both ``gte`` and ``lte`` at each probe; the
# order matters only for tie-breaking and is therefore deterministic.
_NUMERIC_QUANTILES: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)

# Sprint 7 — categorical ``in`` operator (feature-flagged, off by default).
# Existing ``eq`` candidates always emit; ``in`` candidates only join the
# pool when ``BRAIN_SYNTH_IN_OPERATOR_ENABLED`` is truthy.  This keeps
# mining behaviour bit-for-bit identical to pre-Sprint-7 until the team
# has live evidence the new operator helps.
_IN_OPERATOR_FLAG_ENV: Final[str] = "BRAIN_SYNTH_IN_OPERATOR_ENABLED"

# Cardinality cap for categorical fields considered for ``in``.  Above
# this threshold the candidate space is too wide to enumerate without
# overfitting the small per-property case sets the cold-start replay
# produces.
_IN_OPERATOR_MAX_DISTINCT: Final[int] = 8

# Subset sizes to enumerate.  Singletons reduce to ``eq`` (already
# emitted) and full-set subsets always match every record (useless).
# Sizes 2 and 3 capture the practical "Booking.com OR Airbnb" pairings
# without exploding the candidate count for fields with 8 distinct
# values (C(8,2) + C(8,3) = 28 + 56 = 84 probes worst-case).
_IN_OPERATOR_SUBSET_SIZES: Final[tuple[int, ...]] = (2, 3)

# Sprint H — per-scenario feature whitelist (feature-flagged, off by
# default).  Mining runs against the global ``_PMS_KEYS`` /
# ``_CALENDAR_KEYS`` / ``_GUEST_KEYS`` surface unless
# ``BRAIN_SCENARIO_FEATURES_ENABLED`` is truthy, in which case
# scenarios listed in
# :data:`core.brain.patterns.scenario_features.SCENARIO_FEATURES`
# get a hand-curated subset.  Keeps behaviour bit-for-bit identical
# to pre-Sprint-H until the team explicitly opts in.
_SCENARIO_FEATURES_FLAG_ENV: Final[str] = "BRAIN_SCENARIO_FEATURES_ENABLED"

# Feature snapshot keys that are copied into the flattened evaluation
# surface.  Keys present on a snapshot but not in this allowlist are
# skipped to keep the synthesised conditions stable across schema
# tweaks (a stray runtime-only field cannot accidentally become a
# learned condition).
_PMS_KEYS: Final[tuple[str, ...]] = (
    "status",
    "adults",
    "children",
    "infants",
    "total_price",
    "currency",
    "source",
    "payment_status",
    # Temporal mining surface — populated by
    # ``CaseBuilder._build_pms_snapshot`` so the synthesiser can
    # discover splits like "PM defers when ``lead_time_hours >= 120``,
    # informs when ``< 48``" (Mümin's access-code-release example,
    # 2026-05-05) and stage-driven splits ("ASK in PRE_ARRIVAL,
    # INFORM in IN_STAY").  Both keys default-out cleanly when the
    # snapshot does not carry them, so older cases continue to mine
    # exactly as before.
    "lead_time_hours",
    "hours_before_checkin",
    "stage",
)
_CALENDAR_KEYS: Final[tuple[str, ...]] = (
    "gap_before",
    "gap_after",
    "occupancy_7d",
    "occupancy_30d",
    "season",
    "same_day_turnover",
    "weekday_count",
    "weekend_count",
    "nights",
    "adr",
)
_GUEST_KEYS: Final[tuple[str, ...]] = (
    "total_bookings",
    "total_incidents",
    "id_verified",
    "is_repeat_guest",
    "rating",
    "language",
)

# Snapshot-key aliases.  The runtime feature dict assembled by
# ``brain_engine.orchestrator.resolvers.default_feature_builder``
# merges ``ctx.pms_snapshot`` top-level keys verbatim into the dict
# that :meth:`PatternRule.matches_conditions` sees.  The case_builder
# stores the PMS amount under ``total_price`` and the channel under
# ``source``, so the synthesised conditions must use exactly those
# names — *no* renaming.  Aliases are kept as an empty mapping so
# downstream callers can override if their feature builder rewrites
# keys (no-op by default keeps mining and runtime in sync).
_FIELD_ALIASES: Final[Mapping[str, str]] = {}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConditionCandidate:
    """One candidate ``{field, operator, value}`` triple.

    Attributes:
        field_name: Runtime feature name the condition references.
        operator: One of ``gte``, ``lte``, ``eq`` — matches the
            taxonomy understood by
            :func:`core.brain.patterns.models._evaluate_condition`.
        value: Threshold (numeric) or category (categorical) the
            operator compares against.
        target_matched: Target cases satisfied by this condition.
        other_matched: Counterexample cases satisfied by it.
    """

    field_name: str
    operator: str
    value: Any
    target_matched: int
    other_matched: int

    @property
    def total_matched(self) -> int:
        """Sum of target plus counterexample cases satisfied."""
        return self.target_matched + self.other_matched

    @property
    def purity(self) -> float:
        """Precision — share of matched cases belonging to target."""
        if self.total_matched == 0:
            return 0.0
        return self.target_matched / self.total_matched


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Output of one synthesis call.

    Attributes:
        conditions: Mapping ready for :class:`PatternRule.conditions`.
            Empty when no acceptable split was found.
        support_count: Target cases that satisfy every condition.
        counterexample_count: Other cases that satisfy every condition.
        confidence: ``support / (support + counterexample)`` rounded
            to four decimals — bounded by ``[min_purity, 1.0]``.
    """

    conditions: dict[str, dict[str, Any]] = field(default_factory=dict)
    support_count: int = 0
    counterexample_count: int = 0
    confidence: float = 0.0

    @property
    def is_empty(self) -> bool:
        """Whether no condition was synthesised."""
        return not self.conditions


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    """Diagnostic counters returned alongside the synthesised rule.

    Useful for tests and for the structured-log entries that the
    miner emits when it falls through to the unconditional path.

    Attributes:
        candidates_considered: Field/operator/value triples scored.
        candidates_above_purity: Triples that passed the purity gate.
        depth_reached: Number of conditions in the final rule.
        rejected_low_support: Triples skipped because too few target
            cases survived the split.
    """

    candidates_considered: int = 0
    candidates_above_purity: int = 0
    depth_reached: int = 0
    rejected_low_support: int = 0


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------


class ConditionSynthesizer:
    """Greedy decision-tree style miner for one ``(scenario, action)``.

    Args:
        min_purity: Lower bound on ``target / (target + other)`` for
            a candidate to be retained.  Defaults to
            :data:`DEFAULT_MIN_PURITY`.
        min_support_after: Minimum number of target cases that must
            still match after the conjunction is applied.  Defaults
            to :data:`DEFAULT_MIN_SUPPORT_AFTER`.
        max_conditions: Maximum conjunctive depth.  Defaults to
            :data:`DEFAULT_MAX_CONDITIONS`.
    """

    def __init__(
        self,
        *,
        min_purity: float = DEFAULT_MIN_PURITY,
        min_support_after: int = DEFAULT_MIN_SUPPORT_AFTER,
        max_conditions: int = DEFAULT_MAX_CONDITIONS,
    ) -> None:
        if not 0.0 < min_purity <= 1.0:
            raise ValueError("min_purity must be in (0.0, 1.0]")
        if min_support_after < 1:
            raise ValueError("min_support_after must be >= 1")
        if max_conditions < 1:
            raise ValueError("max_conditions must be >= 1")
        self._min_purity = float(min_purity)
        self._min_support = int(min_support_after)
        self._max_conditions = int(max_conditions)

    def synthesize(
        self,
        target_cases: Sequence[DecisionCase],
        other_cases: Sequence[DecisionCase],
    ) -> tuple[SynthesisResult, SynthesisReport]:
        """Mine the best conjunctive condition for ``target_cases``.

        Args:
            target_cases: Cases whose decision is the action we want
                to learn (e.g. all ``APPROVE`` cases).
            other_cases: Cases for the same scenario+scope whose
                decision is anything else (counterexamples).

        Returns:
            ``(result, report)``.  When ``result.is_empty`` is true
            no condition met the purity / support thresholds and the
            caller should fall back to the unconditional rule path.
        """
        if not target_cases:
            return SynthesisResult(), SynthesisReport()
        target_features = [_flatten(case) for case in target_cases]
        other_features = [_flatten(case) for case in other_cases]
        feature_keys = _shared_feature_keys(target_features, other_features)
        if not feature_keys:
            return SynthesisResult(), SynthesisReport()
        return self._greedy_search(
            target_features=target_features,
            other_features=other_features,
            feature_keys=feature_keys,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _greedy_search(
        self,
        *,
        target_features: list[dict[str, Any]],
        other_features: list[dict[str, Any]],
        feature_keys: tuple[str, ...],
    ) -> tuple[SynthesisResult, SynthesisReport]:
        """Iterate up to ``max_conditions`` greedy condition picks."""
        chosen: list[ConditionCandidate] = []
        target_pool = list(target_features)
        other_pool = list(other_features)
        considered = 0
        above_purity = 0
        rejected_low = 0
        for _ in range(self._max_conditions):
            candidates = self._enumerate_candidates(
                target_features=target_pool,
                other_features=other_pool,
                feature_keys=feature_keys,
            )
            considered += len(candidates)
            best = self._pick_best(candidates)
            if best is None:
                break
            above_purity += sum(1 for c in candidates if c.purity >= self._min_purity)
            rejected_low += sum(
                1 for c in candidates if c.purity >= self._min_purity and c.target_matched < self._min_support
            )
            chosen.append(best)
            target_pool, other_pool = _filter_by_candidate(
                best,
                target_pool=target_pool,
                other_pool=other_pool,
            )
            if best.purity >= 1.0:
                break
        if not chosen:
            return (
                SynthesisResult(),
                SynthesisReport(
                    candidates_considered=considered,
                    candidates_above_purity=above_purity,
                    rejected_low_support=rejected_low,
                ),
            )
        result = _materialise_result(chosen)
        report = SynthesisReport(
            candidates_considered=considered,
            candidates_above_purity=above_purity,
            depth_reached=len(chosen),
            rejected_low_support=rejected_low,
        )
        return result, report

    def _enumerate_candidates(
        self,
        *,
        target_features: list[dict[str, Any]],
        other_features: list[dict[str, Any]],
        feature_keys: Iterable[str],
    ) -> list[ConditionCandidate]:
        """Generate every probe condition for the residual pools."""
        candidates: list[ConditionCandidate] = []
        for key in feature_keys:
            target_values = _values_for(target_features, key)
            if not target_values:
                continue
            kind = _classify_feature(target_values)
            if kind == "numeric":
                candidates.extend(
                    _numeric_candidates(
                        key=key,
                        target_features=target_features,
                        other_features=other_features,
                    )
                )
            elif kind == "categorical":
                candidates.extend(
                    _categorical_candidates(
                        key=key,
                        target_features=target_features,
                        other_features=other_features,
                    )
                )
        return candidates

    def _pick_best(
        self,
        candidates: Sequence[ConditionCandidate],
    ) -> ConditionCandidate | None:
        """Return the highest-purity candidate that meets the gates."""
        admissible = [c for c in candidates if c.purity >= self._min_purity and c.target_matched >= self._min_support]
        if not admissible:
            return None
        admissible.sort(
            key=lambda c: (-c.purity, -c.target_matched, c.field_name),
        )
        return admissible[0]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _flatten(case: DecisionCase) -> dict[str, Any]:
    """Project a ``DecisionCase`` into a flat feature dict.

    The flattened dict shadows ``BookingFeatures.to_dict`` so a
    synthesised condition will evaluate against the same surface at
    runtime that PatternRuleResolver feeds into
    :meth:`PatternRule.matches_conditions`.

    Sprint H — when ``BRAIN_SCENARIO_FEATURES_ENABLED`` is truthy
    *and* ``case.scenario`` is listed in
    :data:`core.brain.patterns.scenario_features.SCENARIO_FEATURES`,
    the scenario's hand-curated whitelist replaces the corresponding
    global default for that source.  Scenarios not listed (and any
    source left as ``None`` on a partial override) fall back to the
    global defaults.  When the flag is off, every scenario uses the
    global defaults so behaviour is bit-for-bit identical to
    pre-Sprint-H.
    """
    pms_keys, calendar_keys, guest_keys = _resolve_feature_keys(
        case.scenario,
    )

    flat: dict[str, Any] = {}
    _copy_keys(case.pms_snapshot, pms_keys, flat)
    _copy_keys(case.calendar_snapshot, calendar_keys, flat)
    _copy_keys(case.guest_snapshot, guest_keys, flat)
    for entity_key, entity_value in case.extracted_entities.items():
        # ``extracted_entities`` is LLM-shaped so we do not allowlist.
        # We only skip nested dicts/lists — they cannot become a
        # scalar condition and would inflate the candidate space.
        if isinstance(entity_value, (dict, list)):
            continue
        flat.setdefault(entity_key, entity_value)
    return flat


def _resolve_feature_keys(
    scenario: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return ``(pms_keys, calendar_keys, guest_keys)`` for ``scenario``.

    Honours :func:`_scenario_features_enabled` — when the flag is
    off, all three tuples are the global defaults regardless of
    scenario.  When the flag is on, scenarios listed in
    :data:`SCENARIO_FEATURES` get their per-source override; sources
    left as ``None`` on a partial override fall back to the matching
    global default.
    """
    if not _scenario_features_enabled():
        return _PMS_KEYS, _CALENDAR_KEYS, _GUEST_KEYS
    whitelist = SCENARIO_FEATURES.get(scenario)
    if whitelist is None:
        return _PMS_KEYS, _CALENDAR_KEYS, _GUEST_KEYS
    pms_keys = whitelist.pms_keys or _PMS_KEYS
    calendar_keys = whitelist.calendar_keys or _CALENDAR_KEYS
    guest_keys = whitelist.guest_keys or _GUEST_KEYS
    return pms_keys, calendar_keys, guest_keys


def _scenario_features_enabled() -> bool:
    """Whether the Sprint H per-scenario whitelist is enabled.

    Read on every call so a deploy can flip
    ``BRAIN_SCENARIO_FEATURES_ENABLED`` without restarting the API
    pod.  Default off — every scenario sees the global feature
    surface until the team explicitly opts in.
    """
    raw = (
        os.environ.get(
            _SCENARIO_FEATURES_FLAG_ENV,
            "",
        )
        .strip()
        .lower()
    )
    return raw in ("1", "true", "yes", "on")


def _copy_keys(
    source: Mapping[str, Any],
    allowed: tuple[str, ...],
    target: dict[str, Any],
) -> None:
    """Copy allow-listed keys, applying field-name aliases."""
    for key in allowed:
        value = source.get(key)
        if value is None:
            continue
        target[_FIELD_ALIASES.get(key, key)] = value


def _shared_feature_keys(
    target_features: Sequence[dict[str, Any]],
    other_features: Sequence[dict[str, Any]],
) -> tuple[str, ...]:
    """Return feature keys present on at least one target case.

    Keys exclusive to ``other_features`` cannot help separate the
    target — the synthesizer does not need to enumerate them.
    """
    seen: set[str] = set()
    for record in target_features:
        seen.update(record.keys())
    for record in other_features:
        # Keys also present elsewhere are fine — we just need a
        # baseline set sourced from target evidence.
        pass
    return tuple(sorted(seen))


def _values_for(
    features: Sequence[dict[str, Any]],
    key: str,
) -> tuple[Any, ...]:
    """Return non-None values of ``key`` across ``features``."""
    return tuple(record[key] for record in features if record.get(key) is not None)


def _classify_feature(values: Sequence[Any]) -> str:
    """Decide whether a feature is numeric or categorical."""
    if all(isinstance(v, bool) for v in values):
        return "categorical"
    if all(isinstance(v, (int, float)) for v in values):
        return "numeric"
    return "categorical"


def _numeric_candidates(
    *,
    key: str,
    target_features: Sequence[dict[str, Any]],
    other_features: Sequence[dict[str, Any]],
) -> list[ConditionCandidate]:
    """Probe ``gte`` / ``lte`` thresholds at fixed quantiles."""
    target_values = _values_for(target_features, key)
    if not target_values:
        return []
    thresholds = _quantiles(target_values)
    candidates: list[ConditionCandidate] = []
    for threshold in thresholds:
        for operator in ("gte", "lte"):
            candidates.append(
                _score_candidate(
                    key=key,
                    operator=operator,
                    value=threshold,
                    target_features=target_features,
                    other_features=other_features,
                )
            )
    return candidates


def _categorical_candidates(
    *,
    key: str,
    target_features: Sequence[dict[str, Any]],
    other_features: Sequence[dict[str, Any]],
) -> list[ConditionCandidate]:
    """Probe ``eq`` for every distinct value present on target cases.

    When ``BRAIN_SYNTH_IN_OPERATOR_ENABLED`` is truthy, additionally
    probe ``in`` for non-trivial subsets of distinct values across the
    union of target+other pools (Sprint 7).  The new probes are
    additive — flag-off behaviour is bit-for-bit identical to the
    pre-Sprint-7 path.  A failure inside the ``in`` proposer is
    captured and logged so a bug there can never crash mining; the
    function falls back to the ``eq``-only candidate list.
    """
    target_values = _values_for(target_features, key)
    distinct = sorted({_canonical(v) for v in target_values})
    candidates: list[ConditionCandidate] = []
    for value in distinct:
        candidates.append(
            _score_candidate(
                key=key,
                operator="eq",
                value=value,
                target_features=target_features,
                other_features=other_features,
            )
        )
    if _in_operator_enabled():
        try:
            candidates.extend(
                _categorical_in_candidates(
                    key=key,
                    target_features=target_features,
                    other_features=other_features,
                )
            )
        except Exception:
            logger.exception(
                "in_operator_candidates_failed",
                field=key,
            )
    return candidates


def _categorical_in_candidates(
    *,
    key: str,
    target_features: Sequence[dict[str, Any]],
    other_features: Sequence[dict[str, Any]],
) -> list[ConditionCandidate]:
    """Emit ``in`` candidates for non-trivial subsets of distinct values.

    Args:
        key: Categorical feature name to probe.
        target_features: Flattened feature dicts for the target action.
        other_features: Flattened feature dicts for counterexamples.

    Returns:
        List of :class:`ConditionCandidate` entries with
        ``operator="in"`` and ``value`` a sorted ``list`` of canonical
        values.  Empty when the field has fewer than 3 or more than
        :data:`_IN_OPERATOR_MAX_DISTINCT` distinct values across the
        union of pools — outside that band ``eq`` already covers the
        useful split or the search space is too wide to be safe.
    """
    union_values = (
        *_values_for(target_features, key),
        *_values_for(other_features, key),
    )
    distinct = sorted({_canonical(v) for v in union_values})
    if not 3 <= len(distinct) <= _IN_OPERATOR_MAX_DISTINCT:
        return []
    candidates: list[ConditionCandidate] = []
    for size in _IN_OPERATOR_SUBSET_SIZES:
        if size >= len(distinct):
            continue
        for subset in combinations(distinct, size):
            candidates.append(
                _score_candidate(
                    key=key,
                    operator="in",
                    value=list(subset),
                    target_features=target_features,
                    other_features=other_features,
                )
            )
    return candidates


def _in_operator_enabled() -> bool:
    """Whether the Sprint 7 ``in`` candidate path is enabled.

    Read on every call so a deploy can flip
    ``BRAIN_SYNTH_IN_OPERATOR_ENABLED`` without restarting the API
    pod.  Default off — categorical-in candidates are not generated
    until the team explicitly opts in.
    """
    raw = os.environ.get(_IN_OPERATOR_FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _score_candidate(
    *,
    key: str,
    operator: str,
    value: Any,
    target_features: Sequence[dict[str, Any]],
    other_features: Sequence[dict[str, Any]],
) -> ConditionCandidate:
    """Count target / other cases satisfied by ``(operator, value)``."""
    target_hits = sum(1 for record in target_features if _matches(record.get(key), operator, value))
    other_hits = sum(1 for record in other_features if _matches(record.get(key), operator, value))
    return ConditionCandidate(
        field_name=key,
        operator=operator,
        value=value,
        target_matched=target_hits,
        other_matched=other_hits,
    )


def _matches(actual: Any, operator: str, expected: Any) -> bool:
    """Mirror of :func:`models._evaluate_condition` for synthesis.

    Kept local to avoid an import cycle and to ensure the synthesizer
    treats ``None`` actuals as a non-match — at training time a
    missing value cannot vote for the rule.
    """
    if actual is None:
        return False
    try:
        if operator == "gte":
            return actual >= expected
        if operator == "lte":
            return actual <= expected
        if operator == "eq":
            return _canonical(actual) == _canonical(expected)
        if operator == "in":
            return _canonical(actual) in {_canonical(value) for value in expected}
        if operator == "not_in":
            return _canonical(actual) not in {_canonical(value) for value in expected}
    except TypeError:
        return False
    return False


def _canonical(value: Any) -> Any:
    """Normalise categorical values for stable equality comparisons.

    Strings are stripped and case-folded so ``"Booking.com"`` and
    ``"booking.com  "`` collapse to the same canonical form.  Other
    types are returned unchanged.
    """
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def _quantiles(values: Sequence[float]) -> tuple[float, ...]:
    """Return distinct quantile probes drawn from ``values``."""
    sorted_values = sorted(values)
    probes = [_quantile(sorted_values, q) for q in _NUMERIC_QUANTILES]
    seen: list[float] = []
    for value in probes:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy-free)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = q * (len(sorted_values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = index - lower
    return float(sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac)


def _filter_by_candidate(
    candidate: ConditionCandidate,
    *,
    target_pool: list[dict[str, Any]],
    other_pool: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the candidate as a filter on both pools."""
    field_name = candidate.field_name
    operator = candidate.operator
    value = candidate.value
    target_kept = [record for record in target_pool if _matches(record.get(field_name), operator, value)]
    other_kept = [record for record in other_pool if _matches(record.get(field_name), operator, value)]
    return target_kept, other_kept


def _materialise_result(
    chosen: Sequence[ConditionCandidate],
) -> SynthesisResult:
    """Collapse the greedy chain into a :class:`SynthesisResult`."""
    last = chosen[-1]
    conditions: dict[str, dict[str, Any]] = {}
    for candidate in chosen:
        conditions[candidate.field_name] = {
            "operator": candidate.operator,
            "value": candidate.value,
        }
    confidence = round(last.purity, 4)
    return SynthesisResult(
        conditions=conditions,
        support_count=last.target_matched,
        counterexample_count=last.other_matched,
        confidence=confidence,
    )
