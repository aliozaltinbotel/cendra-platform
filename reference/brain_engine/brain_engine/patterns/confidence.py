"""Multi-factor confidence + statistical-heuristics framework (P6).

ali.md §10 spells out the practical confidence formula
applied to every :class:`PatternRule` candidate::

    confidence = (
        success_count / total_matching_cases
        - counterexample_penalty
        - staleness_penalty
        - low_support_penalty
        - conflict_penalty
    ) * hidden_variable_penalty

The naive ``success / total`` ratio (legacy
``pattern_miner.py:199``) is a maximum-likelihood point estimate
that ignores the four corrections ali.md flags:

* **Counterexamples** — recent disagreements should drag
  confidence down even when the long-run ratio looks fine.
* **Staleness** — a year-old rule that hasn't fired since is
  weaker evidence than yesterday's.
* **Low support** — even a 100 % ratio is fragile under tiny
  samples (Wilson handles promotion gating, but the surfaced
  *number* should reflect that fragility too).
* **Conflicts** — when the validator detects a conflicting rule
  in the registry the candidate's confidence must shrink.

A multiplicative ``hidden_variable_penalty`` captures the
validator finding that a rule depends on context the case data
never recorded.

ali.md does **not** pin numerical weights — they need
calibration against ~30+ historical cases per scenario per
owner.  This module ships conservative defaults that downgrade
confidence only when a signal is actually present, and that
collapse to ``success / total`` for an empty
:class:`ConfidenceContext` (so existing callers see no
regression until they wire real signals).

§E *Statistical heuristics* describes a parallel category of
soft probabilistic signals (e.g.
``door_code_staticity_confidence = 0.86``) that influence
runtime phrasing rather than gate execution.
:class:`HeuristicRegistry` is the in-memory framework for
recording and looking up those signals; persistence and
calibration loops are out of scope for the framework skeleton.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

__all__ = [
    "CONTRADICTION_UNRESOLVED_CAP",
    "DEFAULT_PENALTY_CONFIG",
    "DEFAULT_SIGNAL_WEIGHTS",
    "ConfidenceContext",
    "HeuristicRegistry",
    "PenaltyConfig",
    "SignalWeights",
    "StatisticalHeuristic",
    "compute_confidence",
]


# ---------------------------------------------------------------------------
# Penalty calibration knobs
# ---------------------------------------------------------------------------
#
# ali.md §10 lists the penalty terms but not their numerical
# weights.  These defaults are conservative — each penalty
# evaluates to ``0.0`` when its signal is absent and the
# combined drag never exceeds ~0.4 of the base ratio for fully
# saturated signals.  Calibrate per scenario / owner once
# enough historical DecisionCases land.

# Staleness ramps linearly: at one half-life the penalty equals
# the cap.  90 days mirrors the reset window the outcome
# labeller already uses for amenity_exception (P4).
DEFAULT_STALENESS_HALFLIFE_DAYS: Final[float] = 90.0
DEFAULT_STALENESS_CAP: Final[float] = 0.20

# ali.md §10 tier table: support_count >= 8 promotes the rule
# to approval mode.  Below the floor we apply a per-unit
# penalty so a 4-support rule still surfaces but reads as a
# ~0.20 weaker signal than an otherwise identical 8-support
# rule.
DEFAULT_LOW_SUPPORT_FLOOR: Final[int] = 8
DEFAULT_LOW_SUPPORT_PER_UNIT: Final[float] = 0.05
DEFAULT_LOW_SUPPORT_CAP: Final[float] = 0.20

# Counterexamples are already implicit in success / total, so
# the additional penalty is small — it captures the *recency*
# concern from validator check #5 and decays the confidence
# slightly more for every recent counter-vote.
DEFAULT_COUNTEREXAMPLE_PER_UNIT: Final[float] = 0.02
DEFAULT_COUNTEREXAMPLE_CAP: Final[float] = 0.15

# Conflict drag is sharper — a single registry conflict is
# worth losing 0.15 of confidence; two conflicts cap the
# penalty at 0.30 to avoid driving the value to zero from a
# single noisy validator finding.
DEFAULT_CONFLICT_PER_UNIT: Final[float] = 0.15
DEFAULT_CONFLICT_CAP: Final[float] = 0.30

# Hidden-variable findings act as a multiplier per ali.md §10.
# 0.85 is gentle: a rule the validator suspects is missing
# context still surfaces, just with a visibly lower confidence.
DEFAULT_HIDDEN_VARIABLE_MULT: Final[float] = 0.85


@dataclass(frozen=True, slots=True)
class PenaltyConfig:
    """Numerical knobs for each penalty term.

    Every field has a module-level default; teams override the
    handful they want to calibrate without touching the
    formula.  The defaults are documented above; see ali.md §10
    for the underlying rationale.
    """

    staleness_halflife_days: float = DEFAULT_STALENESS_HALFLIFE_DAYS
    staleness_cap: float = DEFAULT_STALENESS_CAP
    low_support_floor: int = DEFAULT_LOW_SUPPORT_FLOOR
    low_support_per_unit: float = DEFAULT_LOW_SUPPORT_PER_UNIT
    low_support_cap: float = DEFAULT_LOW_SUPPORT_CAP
    counterexample_per_unit: float = DEFAULT_COUNTEREXAMPLE_PER_UNIT
    counterexample_cap: float = DEFAULT_COUNTEREXAMPLE_CAP
    conflict_per_unit: float = DEFAULT_CONFLICT_PER_UNIT
    conflict_cap: float = DEFAULT_CONFLICT_CAP
    hidden_variable_mult: float = DEFAULT_HIDDEN_VARIABLE_MULT


DEFAULT_PENALTY_CONFIG: Final[PenaltyConfig] = PenaltyConfig()


# ---------------------------------------------------------------------------
# Signal-weight ontology (FL-06 — §5 of the Proactive Foundation MD)
# ---------------------------------------------------------------------------
#
# The proactive foundation document recommends explicit weights
# for PM actions that should *raise* the rule confidence faster
# than passive guest signals.  Storing them here as a typed value
# object keeps the formula readable and lets operators recalibrate
# without touching :func:`compute_confidence` itself.
#
# §5 reads verbatim:
#
#   * PM explicit rule:        +0.30
#   * PM repeated edit:        +0.15
#   * PM approval / rejection: +0.10
#   * Guest complaint:         +0.07
#   * Task reopen:             +0.10
#   * Vendor SLA breach:       +0.10
#   * Review mention:          +0.20
#   * Contradiction unresolved: cap confidence below 0.70 until
#                              resolved
#
# The contradiction rule is qualitatively different — it does not
# add a positive signal but rather *caps* whatever confidence the
# rest of the formula produced.  We track it as a constant cap
# (:data:`CONTRADICTION_UNRESOLVED_CAP`) rather than a per-unit
# weight so a single unresolved contradiction is enough to enforce
# the ceiling.

DEFAULT_PM_EXPLICIT_RULE_WEIGHT: Final[float] = 0.30
DEFAULT_PM_REPEATED_EDIT_WEIGHT: Final[float] = 0.15
DEFAULT_PM_APPROVAL_WEIGHT: Final[float] = 0.10
DEFAULT_GUEST_COMPLAINT_WEIGHT: Final[float] = 0.07
DEFAULT_TASK_REOPEN_WEIGHT: Final[float] = 0.10
DEFAULT_VENDOR_SLA_BREACH_WEIGHT: Final[float] = 0.10
DEFAULT_REVIEW_MENTION_WEIGHT: Final[float] = 0.20
CONTRADICTION_UNRESOLVED_CAP: Final[float] = 0.70


@dataclass(frozen=True, slots=True)
class SignalWeights:
    """Per-signal additive boosts (FL-06 — §5 of the Proactive doc).

    All weights default to the recommendation pinned in
    ``Cendra_Brain_Engine_Proactive_Guest_Journey_OperationsFoundation
    .md`` §5.  Operators that want to recalibrate (e.g. tighten
    review-mention weight for a noisy OTA stream) construct a
    fresh :class:`SignalWeights` with the overrides; the default
    value object is shared as :data:`DEFAULT_SIGNAL_WEIGHTS`.

    The weights apply *multiplicatively to the count* of each
    signal observed under the candidate rule's scope — N PM
    approvals add ``N * pm_approval`` to the base confidence,
    capped to ``1.0`` by :func:`compute_confidence` afterwards.
    Counts of zero contribute nothing so the legacy ``success /
    total`` collapses gracefully for callers that do not populate
    the new fields on :class:`ConfidenceContext`.

    Attributes:
        pm_explicit_rule: Weight added per explicit PM rule
            written down for the scenario.  Strongest signal —
            equivalent to roughly three approvals.
        pm_repeated_edit: Weight per repeated PM edit.
        pm_approval: Weight per PM approval or rejection of the
            engine's draft action.
        guest_complaint: Weight per guest complaint about the
            same scenario.  Smallest weight by design — passive
            signal, often noisy.
        task_reopen: Weight per task that was reopened after the
            engine marked it complete (operational disagreement).
        vendor_sla_breach: Weight per vendor SLA breach attached
            to the scope.
        review_mention: Weight per public review mention tagged
            to the scenario.  Heavy because public reviews carry
            reputation cost.
    """

    pm_explicit_rule: float = DEFAULT_PM_EXPLICIT_RULE_WEIGHT
    pm_repeated_edit: float = DEFAULT_PM_REPEATED_EDIT_WEIGHT
    pm_approval: float = DEFAULT_PM_APPROVAL_WEIGHT
    guest_complaint: float = DEFAULT_GUEST_COMPLAINT_WEIGHT
    task_reopen: float = DEFAULT_TASK_REOPEN_WEIGHT
    vendor_sla_breach: float = DEFAULT_VENDOR_SLA_BREACH_WEIGHT
    review_mention: float = DEFAULT_REVIEW_MENTION_WEIGHT


DEFAULT_SIGNAL_WEIGHTS: Final[SignalWeights] = SignalWeights()


# ---------------------------------------------------------------------------
# ConfidenceContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfidenceContext:
    """Signals consumed by :func:`compute_confidence`.

    Every field defaults to a no-op value (``0`` for counts,
    ``None`` for timestamps, ``False`` for flags) so callers
    fill only what they know.  An empty
    ``ConfidenceContext()`` causes the formula to collapse to
    ``success / total``, matching the pre-P6 behaviour at
    ``pattern_miner.py:199``.

    Args:
        counterexample_count: Cases under the same scope and
            scenario that took a different action.  Already
            implicit in ``success / total``; this field lets
            recency-aware callers nudge the value further.
        last_seen_at: Newest evidence timestamp (UTC).  Drives
            the staleness penalty against ``now``.
        now: Reference instant for staleness; defaults to the
            current UTC instant when ``None``.  Tests inject a
            frozen value here.
        conflict_count: Number of registry conflicts the
            validator surfaced for the candidate.
        has_hidden_variable: Validator flag — when ``True``
            the formula multiplies the post-penalty confidence
            by :attr:`PenaltyConfig.hidden_variable_mult`.
    """

    counterexample_count: int = 0
    last_seen_at: datetime | None = None
    now: datetime | None = None
    conflict_count: int = 0
    has_hidden_variable: bool = False
    # FL-06 — Signal-weight ontology from §5 of the Proactive
    # foundation MD.  Counts default to 0 so callers that have
    # not yet wired the new signals see the legacy formula
    # unchanged.  See :class:`SignalWeights` for the per-signal
    # boost magnitudes.
    pm_explicit_rule_count: int = 0
    pm_repeated_edit_count: int = 0
    pm_approval_count: int = 0
    guest_complaint_count: int = 0
    task_reopen_count: int = 0
    vendor_sla_breach_count: int = 0
    review_mention_count: int = 0
    # ``True`` enforces the §5 ceiling of
    # :data:`CONTRADICTION_UNRESOLVED_CAP` (0.70).  The flag is
    # set by the contradiction detector (FL-07) when a workflow
    # has an unresolved source-of-truth conflict; the cap stays
    # active until the conflict is resolved upstream.
    contradiction_unresolved: bool = False


# ---------------------------------------------------------------------------
# Penalty term computations
# ---------------------------------------------------------------------------


def _staleness_penalty(
    last_seen: datetime | None,
    now: datetime | None,
    cfg: PenaltyConfig,
) -> float:
    """Return the staleness penalty in ``[0, cfg.staleness_cap]``.

    A ``None`` timestamp or a non-positive half-life disables
    the penalty.  Naive datetimes are treated as UTC (matching
    how ``DecisionCase.created_at`` is currently stored).
    """
    if last_seen is None or cfg.staleness_halflife_days <= 0.0:
        return 0.0
    reference = now or datetime.now(UTC)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    delta_days = max(
        0.0,
        (reference - last_seen).total_seconds() / 86400.0,
    )
    fraction = delta_days / cfg.staleness_halflife_days
    return min(cfg.staleness_cap, fraction * cfg.staleness_cap)


def _low_support_penalty(success: int, cfg: PenaltyConfig) -> float:
    """Penalise candidates whose support is below the §10 floor."""
    deficit = max(0, cfg.low_support_floor - max(0, success))
    return min(cfg.low_support_cap, deficit * cfg.low_support_per_unit)


def _counterexample_penalty(count: int, cfg: PenaltyConfig) -> float:
    """Per-counterexample drag, capped to avoid double-counting."""
    if count <= 0:
        return 0.0
    return min(cfg.counterexample_cap, count * cfg.counterexample_per_unit)


def _conflict_penalty(count: int, cfg: PenaltyConfig) -> float:
    """Drag for each conflicting rule the validator surfaced."""
    if count <= 0:
        return 0.0
    return min(cfg.conflict_cap, count * cfg.conflict_per_unit)


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------


def _signal_boost(
    ctx: ConfidenceContext,
    weights: SignalWeights,
) -> float:
    """Sum the §5 per-signal weights * observed counts (FL-06).

    Returns ``0.0`` when the caller did not populate any of the
    new ``ConfidenceContext`` count fields so the legacy formula
    is preserved bit-for-bit.  Negative counts are treated as
    zero — defensive against a buggy upstream that flipped a
    count's sign.
    """
    return (
        max(0, ctx.pm_explicit_rule_count) * weights.pm_explicit_rule
        + max(0, ctx.pm_repeated_edit_count) * weights.pm_repeated_edit
        + max(0, ctx.pm_approval_count) * weights.pm_approval
        + max(0, ctx.guest_complaint_count) * weights.guest_complaint
        + max(0, ctx.task_reopen_count) * weights.task_reopen
        + max(0, ctx.vendor_sla_breach_count) * weights.vendor_sla_breach
        + max(0, ctx.review_mention_count) * weights.review_mention
    )


def compute_confidence(
    success: int,
    total: int,
    ctx: ConfidenceContext | None = None,
    cfg: PenaltyConfig | None = None,
    weights: SignalWeights | None = None,
) -> float:
    """Apply the ali.md §10 + Proactive §5 confidence formula.

    Pipeline (each step clamps to ``[0.0, 1.0]``):

    1. ``base = success / total``.
    2. Subtract the ali.md §10 penalties (counterexamples,
       staleness, low support, conflicts).
    3. Add the Proactive §5 signal boosts (PM rule, edit,
       approval, complaint, reopen, vendor SLA, review mention).
    4. Apply the ``hidden_variable_mult`` when the validator
       flagged a missing input.
    5. Cap to :data:`CONTRADICTION_UNRESOLVED_CAP` (``0.70``)
       when ``ctx.contradiction_unresolved`` is ``True``.

    The function returns a value clamped to ``[0.0, 1.0]`` and
    rounded to four decimals (matching the precision used at
    ``pattern_miner.py:422``).  ``ctx=None`` or an empty
    :class:`ConfidenceContext` causes every penalty / boost to
    no-op, so the result equals ``success / total`` — the legacy
    behaviour.

    Args:
        success: Cases supporting the candidate rule
            (``support_count``).
        total: Cases observed under the same scope and
            scenario.  Returns ``0.0`` for ``total <= 0``.
        ctx: Optional signal bag; see :class:`ConfidenceContext`.
        cfg: Optional penalty knobs; defaults to
            :data:`DEFAULT_PENALTY_CONFIG`.
        weights: Optional signal-weight overrides; defaults to
            :data:`DEFAULT_SIGNAL_WEIGHTS` (the §5 recommendation).

    Returns:
        The post-formula confidence in ``[0.0, 1.0]`` rounded
        to four decimals.
    """
    if total <= 0:
        return 0.0
    if success < 0:
        raise ValueError("success must be non-negative")
    if success > total:
        raise ValueError("success cannot exceed total")
    base = success / total
    if ctx is None:
        return round(base, 4)
    cfg = cfg or DEFAULT_PENALTY_CONFIG
    weights = weights or DEFAULT_SIGNAL_WEIGHTS
    penalty = (
        _counterexample_penalty(ctx.counterexample_count, cfg)
        + _staleness_penalty(ctx.last_seen_at, ctx.now, cfg)
        + _low_support_penalty(success, cfg)
        + _conflict_penalty(ctx.conflict_count, cfg)
    )
    raw = max(0.0, base - penalty)
    raw += _signal_boost(ctx, weights)
    if ctx.has_hidden_variable:
        raw *= cfg.hidden_variable_mult
    clamped = min(1.0, max(0.0, raw))
    if ctx.contradiction_unresolved:
        clamped = min(clamped, CONTRADICTION_UNRESOLVED_CAP)
    return round(clamped, 4)


# ---------------------------------------------------------------------------
# Statistical heuristics framework (ali.md §E)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatisticalHeuristic:
    """A soft probabilistic signal recorded at runtime.

    Examples from ali.md §E::

        probability_owner_approves_discount = 0.72
        probability_two_open_nights_will_sell = 0.31
        door_code_staticity_confidence       = 0.86

    Heuristics are *not* hard rules — they affect how the
    runtime *phrases* a suggestion (``"suggest approval with
    72 % confidence"``) rather than gating execution.  Pattern
    promotion still flows through the validator + Wilson
    gates.
    """

    name: str
    value: float
    sample_size: int = 0
    last_updated: datetime | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("value must be in [0.0, 1.0]")
        if self.sample_size < 0:
            raise ValueError("sample_size must be non-negative")


class HeuristicRegistry:
    """In-memory registry for :class:`StatisticalHeuristic`.

    The framework is deliberately small — register / get /
    snapshot / remove.  Persistence (Postgres-backed store,
    rolling-window recalibration) is out of scope for the P6
    skeleton; downstream code can wrap this registry once
    enough cases accumulate to drive recalibration.
    """

    def __init__(
        self,
        initial: Mapping[str, StatisticalHeuristic] | None = None,
    ) -> None:
        self._items: dict[str, StatisticalHeuristic] = (
            dict(initial) if initial else {}
        )

    def register(self, heuristic: StatisticalHeuristic) -> None:
        """Insert or replace ``heuristic`` keyed by its name."""
        self._items[heuristic.name] = heuristic

    def get(self, name: str) -> StatisticalHeuristic | None:
        """Return the heuristic with ``name`` or ``None``."""
        return self._items.get(name)

    def remove(self, name: str) -> bool:
        """Drop ``name`` from the registry; ``True`` if found."""
        return self._items.pop(name, None) is not None

    def snapshot(self) -> tuple[StatisticalHeuristic, ...]:
        """Return a deterministic tuple snapshot, sorted by name."""
        return tuple(
            sorted(self._items.values(), key=lambda h: h.name),
        )

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items
