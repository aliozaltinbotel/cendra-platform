"""Pattern rule validation — ensures candidate rules are safe to activate.

PatternValidator enforces safety guardrails that the statistical
extraction cannot guarantee:

- **NEVER_AUTO_LEARN**: Certain domains (legal, tax, security, eviction,
  high-value charges) must never produce autonomous rules, regardless of
  confidence.
- **One-off exception detection**: A single positive case should not
  become a durable rule (e.g. a one-time complaint discount).
- **Counterexample ratio**: High contradiction rates invalidate rules.
- **Hidden variable detection**: Rules that seem confident may be
  confounded by an unobserved variable.
- **Temporal decay**: Rules based on very old cases may be outdated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from brain_engine.patterns.models import (
    MAX_COUNTEREXAMPLE_RATIO,
    MIN_SUPPORT_AUTO,
    DecisionType,
    ExecutionMode,
    PatternRule,
    RiskLevel,
    Scenario,
)
from brain_engine.patterns.wilson import (
    PROMOTION_MIN_SUPPORT_AUTO,
    PROMOTION_WILSON_AUTO,
    wilson_lower_bound,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Blacklists
# ---------------------------------------------------------------------------

NEVER_AUTO_LEARN: Final[frozenset[str]] = frozenset(
    {
        "legal_dispute",
        "tax_calculation",
        "tax_filing",
        "security_eviction",
        "guest_eviction",
        "high_value_charge",
        "insurance_claim",
        "regulatory_compliance",
        "contract_modification",
        "refund_over_500",
        "identity_fraud",
        "credit_card_dispute",
        "police_report",
        "building_code_violation",
    }
)

NEVER_AUTO_SCENARIOS: Final[frozenset[Scenario]] = frozenset(
    {
        Scenario.DAMAGE_REPORT,
        Scenario.CANCELLATION_REQUEST,
        # ── Foundation coverage expansion 2026-05-18 ──
        # New safety / fraud / legal-risk scenarios added by the
        # Aybüke 2026-05-18 expansion.  Each one carries enough
        # downside that an auto-fired rule is the wrong default —
        # they belong on the manual-review path even when the
        # pattern miner finds a recurring response.
        Scenario.PROXY_BOOKING_RISK,  # fraud / identity risk
        Scenario.SAFETY_EMERGENCY,  # gas, fire, medical, intruder
        Scenario.SAFETY_SECURITY_CONCERN,  # lock failure, pest, safety
        Scenario.CHARGEBACK_DISPUTE,  # financial / legal exposure
        Scenario.PRIVACY_CONCERN,  # surveillance, GDPR
        Scenario.OFF_PLATFORM_CONTACT,  # off-OTA / fraud vector
    }
)

# Maximum age of the most recent supporting case before a rule is
# considered stale and cannot be promoted.
_MAX_STALENESS_DAYS: Final[int] = 90


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def gate_promotion(
    rule: PatternRule,
    *,
    min_support: int = PROMOTION_MIN_SUPPORT_AUTO,
    min_wilson: float = PROMOTION_WILSON_AUTO,
) -> bool:
    """Decide whether a rule is safe to run in ``ExecutionMode.AUTO``.

    Applies the roadmap's promotion policy: a rule earns AUTO only
    when it carries enough statistical mass (``support_count`` above
    ``min_support``) *and* its Wilson lower bound at 95 % confidence
    meets ``min_wilson``.  The Wilson bound is computed over
    ``support_count`` successes against the total observed cases
    (support + counterexamples), not over raw ``confidence``, so a
    rule with 9/10 positive cases cannot masquerade as a 90/100 rule.

    Args:
        rule: Candidate PatternRule.
        min_support: Minimum positive-case count required for AUTO.
        min_wilson: Minimum Wilson lower bound for AUTO.

    Returns:
        ``True`` when the rule clears both gates, ``False`` otherwise.
    """
    if rule.support_count < min_support:
        return False
    lower = wilson_lower_bound(rule.support_count, rule.total_cases)
    return lower >= min_wilson


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating a candidate PatternRule.

    Attributes:
        valid: Whether the rule passed all checks.
        reasons: Human-readable reasons for failure (empty if valid).
        recommended_mode: Suggested execution mode (may differ from
            the rule's current mode if validation downgrades it).
    """

    valid: bool = True
    reasons: tuple[str, ...] = ()
    recommended_mode: ExecutionMode | None = None


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------

# Default thresholds for the conflict-aware checks added in P3.  Kept
# at module scope so callers and tests can reference them without
# instantiating a ``ValidationContext``.
_HIDDEN_VAR_MIN_SUPPORT: Final[int] = 8
_HIDDEN_VAR_MIN_CONDITIONS: Final[int] = 2
_COMPLAINT_CONTAMINATION_THRESHOLD: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Optional external context for the conflict-aware checks.

    The pre-P3 ``PatternValidator`` was deliberately context-free: it
    inspected the rule itself and nothing else.  The six checks added
    by P3 — manual-rule conflict, PMS/property-fact conflict, active-
    blocker conflict, hidden-variable detection, complaint-recovery
    contamination, static/dynamic source — need information that
    lives outside the rule (manual directives, blocker engine state,
    PMS facts, staticity classifications, per-case scenario flags).

    Rather than coupling :class:`PatternValidator` to those concrete
    stores (which would create import cycles into
    :mod:`brain_engine.blockers`, :mod:`brain_engine.staticity`, …),
    the validator depends only on this small primitive bag.  Each
    field has a no-op default (empty mapping / set), so a check
    whose input is absent silently treats the rule as conflict-free
    — the validator cannot detect a conflict it has no signal for,
    and silently no-opping is preferable to fabricating one.

    Attributes:
        manual_forbidden_actions: Per-scenario set of
            :class:`DecisionType` values that an explicit manual
            directive forbids.  A rule whose ``action.action_type``
            falls into the forbidden set for its scenario is rejected.
        active_blocker_actions: Set of :class:`DecisionType` values
            currently gated by an active blocker.  A rule that wants
            to perform any of those actions is rejected.
        property_constraints: Known PMS / property facts keyed by
            field name (``"max_occupancy"``, ``"allowed_payment_methods"``,
            …).  When a rule condition uses the same key with a
            value that contradicts the constraint (equality
            mismatch, or membership violation if the constraint is a
            collection), the rule is rejected.
        dynamic_field_names: Field names classified as
            ``DYNAMIC_FETCH_LIVE`` or
            ``SECRET_DYNAMIC_FETCH_ONLY`` by the staticity layer.
            A rule whose conditions reference any such field but
            whose action is not :attr:`DecisionType.FETCH_LIVE_DATA`
            is rejected — autonomous execution would race against
            stale data.
        complaint_compensation_case_ids: IDs of cases marked as
            complaint-recovery decisions.  When more than
            :attr:`complaint_contamination_threshold` of a rule's
            ``source_case_ids`` belong to that set the rule is
            considered contaminated by goodwill behaviour and
            rejected.
        hidden_variable_min_support: Support-count threshold above
            which a sparsely-conditioned rule becomes suspicious.
        hidden_variable_min_conditions: Minimum condition count
            required once support exceeds the threshold above.
        complaint_contamination_threshold: Fraction of source cases
            that may be complaint-recovery before the rule is
            marked contaminated (default 0.5 = half).
    """

    manual_forbidden_actions: Mapping[Scenario, frozenset[DecisionType]] = (
        field(default_factory=dict)
    )
    active_blocker_actions: frozenset[DecisionType] = frozenset()
    property_constraints: Mapping[str, Any] = field(default_factory=dict)
    dynamic_field_names: frozenset[str] = frozenset()
    complaint_compensation_case_ids: frozenset[str] = frozenset()
    hidden_variable_min_support: int = _HIDDEN_VAR_MIN_SUPPORT
    hidden_variable_min_conditions: int = _HIDDEN_VAR_MIN_CONDITIONS
    complaint_contamination_threshold: float = (
        _COMPLAINT_CONTAMINATION_THRESHOLD
    )


_EMPTY_CONTEXT: Final[ValidationContext] = ValidationContext()


# ---------------------------------------------------------------------------
# PatternValidator
# ---------------------------------------------------------------------------


class PatternValidator:
    """Validates candidate PatternRules before activation.

    Runs a pipeline of safety checks.  A rule must pass ALL checks
    to be considered valid.  If any check fails, the validator returns
    a detailed explanation.

    Attributes:
        _min_support: Minimum positive cases required.
        _max_counter_ratio: Maximum counterexample ratio.
        _max_staleness_days: Maximum days since last supporting case.
        _log: Bound structured logger.
    """

    def __init__(
        self,
        *,
        min_support: int = MIN_SUPPORT_AUTO,
        max_counter_ratio: float = MAX_COUNTEREXAMPLE_RATIO,
        max_staleness_days: int = _MAX_STALENESS_DAYS,
    ) -> None:
        self._min_support = min_support
        self._max_counter_ratio = max_counter_ratio
        self._max_staleness_days = max_staleness_days
        self._log = logger.bind(component="pattern_validator")

    def validate(
        self,
        rule: PatternRule,
        *,
        context: ValidationContext | None = None,
    ) -> ValidationResult:
        """Run all validation checks on a candidate rule.

        Args:
            rule: The PatternRule to validate.
            context: Optional :class:`ValidationContext` carrying the
                external signals consumed by the conflict-aware
                checks (manual directives, active blockers, PMS
                facts, dynamic fields, complaint-recovery markers).
                When omitted the conflict checks no-op silently —
                they cannot detect a conflict for which they have
                no input — and the validator behaves exactly like
                its pre-P3 self.

        Returns:
            ValidationResult indicating pass/fail with reasons.
        """
        ctx = context if context is not None else _EMPTY_CONTEXT
        failures: list[str] = []

        if not self._check_min_support(rule):
            failures.append(
                f"Insufficient support: {rule.support_count} < "
                f"{self._min_support} required.",
            )

        if not self._check_counterexamples(rule):
            failures.append(
                f"Too many counterexamples: ratio "
                f"{rule.counterexample_ratio:.2f} > "
                f"{self._max_counter_ratio:.2f} maximum.",
            )

        if not self._check_not_one_off_exception(rule):
            failures.append(
                "Rule appears to be a one-off exception (single "
                "source case). One-off exceptions must not become "
                "durable rules.",
            )

        if not self._check_not_blacklisted(rule):
            failures.append(
                f"Scenario '{rule.scenario.value}' or action is in "
                "NEVER_AUTO_LEARN blacklist. Manual rules only.",
            )

        if not self._check_staleness(rule):
            failures.append(
                f"Most recent supporting case is older than "
                f"{self._max_staleness_days} days. Rule may be outdated.",
            )

        if not self._check_empty_conditions(rule):
            failures.append(
                "Rule has no conditions — it would match every case "
                "in the scenario. At least one condition is required.",
            )

        if not self._check_wilson_gate(rule):
            failures.append(
                f"Wilson lower bound "
                f"{wilson_lower_bound(rule.support_count, rule.total_cases):.3f} "
                f"below {PROMOTION_WILSON_AUTO} threshold for AUTO "
                f"mode. Rule must collect more evidence before "
                f"autonomous promotion.",
            )

        manual_conflict = self._check_no_manual_rule_conflict(rule, ctx)
        if not manual_conflict:
            failures.append(
                f"Rule action '{rule.action.action_type.value}' "
                f"conflicts with a manual directive forbidding it "
                f"for scenario '{rule.scenario.value}'.",
            )

        if not self._check_no_pms_conflict(rule, ctx):
            failures.append(
                "Rule conditions contradict known PMS / property "
                "facts; the rule cannot be safely activated until "
                "the conflicting condition is reconciled.",
            )

        if not self._check_no_active_blocker_conflict(rule, ctx):
            failures.append(
                f"Rule action '{rule.action.action_type.value}' is "
                "currently gated by an active blocker; promotion "
                "would short-circuit the blocker.",
            )

        if not self._check_no_hidden_variable(rule, ctx):
            failures.append(
                f"Hidden variable suspected: support "
                f"{rule.support_count} >= "
                f"{ctx.hidden_variable_min_support} but only "
                f"{len(rule.conditions)} conditions "
                f"(< {ctx.hidden_variable_min_conditions}). "
                "Mine for additional discriminative features.",
            )

        if not self._check_no_complaint_recovery_contamination(rule, ctx):
            ratio = self._complaint_recovery_ratio(rule, ctx)
            failures.append(
                f"Rule contaminated by complaint-recovery cases: "
                f"{ratio:.0%} of source cases are complaint "
                f"compensations (> "
                f"{ctx.complaint_contamination_threshold:.0%}). "
                "Goodwill behaviour does not generalise.",
            )

        if not self._check_static_dynamic_source(rule, ctx):
            referenced = sorted(
                set(rule.conditions) & ctx.dynamic_field_names,
            )
            failures.append(
                f"Rule depends on dynamic field(s) {referenced!r} but "
                f"action is "
                f"'{rule.action.action_type.value}', not "
                f"'fetch_live_data'. Static rule would race with "
                "live state.",
            )

        if failures:
            self._log.warning(
                "rule_validation_failed",
                pattern_id=rule.pattern_id[:8],
                scenario=rule.scenario.value,
                failures=failures,
            )
            recommended = self._downgrade_mode(rule)
            return ValidationResult(
                valid=False,
                reasons=tuple(failures),
                recommended_mode=recommended,
            )

        self._log.info(
            "rule_validated",
            pattern_id=rule.pattern_id[:8],
            scenario=rule.scenario.value,
            confidence=rule.confidence,
            mode=rule.execution_mode.value,
        )
        return ValidationResult(valid=True)

    # -------------------------------------------------------------------
    # Individual checks
    # -------------------------------------------------------------------

    def _check_min_support(self, rule: PatternRule) -> bool:
        """Verify minimum support count.

        Args:
            rule: Candidate rule.

        Returns:
            True if support count meets the threshold.
        """
        return rule.support_count >= self._min_support

    def _check_counterexamples(self, rule: PatternRule) -> bool:
        """Verify counterexample ratio is acceptable.

        Args:
            rule: Candidate rule.

        Returns:
            True if counterexample ratio is within limits.
        """
        return rule.counterexample_ratio <= self._max_counter_ratio

    def _check_not_one_off_exception(self, rule: PatternRule) -> bool:
        """Ensure the rule is not derived from a single case.

        A single positive outcome (e.g. a one-time complaint discount)
        should not become a permanent rule.

        Args:
            rule: Candidate rule.

        Returns:
            True if the rule has more than 1 source case.
        """
        return len(rule.source_case_ids) > 1

    def _check_not_blacklisted(self, rule: PatternRule) -> bool:
        """Ensure the rule does not touch blacklisted domains.

        Checks both the scenario and the action parameters against
        the NEVER_AUTO_LEARN set.

        Args:
            rule: Candidate rule.

        Returns:
            True if the rule is not blacklisted.
        """
        if rule.scenario in NEVER_AUTO_SCENARIOS:
            return False

        action_category = rule.action.params.get("category", "")
        if action_category in NEVER_AUTO_LEARN:
            return False

        action_domain = rule.action.params.get("domain", "")
        if action_domain in NEVER_AUTO_LEARN:
            return False

        return True

    def _check_staleness(self, rule: PatternRule) -> bool:
        """Ensure the rule is based on recent-enough evidence.

        Args:
            rule: Candidate rule.

        Returns:
            True if the most recent case is within the staleness window.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._max_staleness_days,
        )
        return rule.last_seen_at >= cutoff

    def _check_empty_conditions(self, rule: PatternRule) -> bool:
        """Ensure the rule has at least one condition.

        A conditionless rule would fire on every matching scenario,
        which is dangerous for learned (non-immutable) rules.

        Args:
            rule: Candidate rule.

        Returns:
            True if at least one condition exists.
        """
        return len(rule.conditions) > 0

    def _check_wilson_gate(self, rule: PatternRule) -> bool:
        """Enforce the Wilson promotion gate for AUTO-mode rules.

        Non-AUTO rules (ASK, APPROVAL, BLOCK) bypass the gate — they
        are not autonomous and therefore tolerate statistical noise.
        AUTO rules must clear :func:`gate_promotion`, which combines
        a minimum-support threshold with a Wilson lower-bound floor.

        Args:
            rule: Candidate rule.

        Returns:
            ``True`` when the rule is not AUTO or passes the gate.
        """
        if rule.execution_mode is not ExecutionMode.AUTO:
            return True
        return gate_promotion(rule)

    # -------------------------------------------------------------------
    # P3 conflict-aware checks
    # -------------------------------------------------------------------

    def _check_no_manual_rule_conflict(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Reject rules that violate an explicit manual directive.

        ``manual_forbidden_actions`` is keyed by :class:`Scenario`.
        For the rule's scenario the validator pulls the set of
        actions the owner / PM has explicitly forbidden (e.g. "never
        offer discounts on Villa Azul") and rejects the rule when
        its ``action.action_type`` falls into that set.

        Args:
            rule: Candidate rule.
            context: Optional context.  An empty
                ``manual_forbidden_actions`` mapping is treated as
                "no manual directives loaded" — the check no-ops.

        Returns:
            ``True`` when no conflict is observed.
        """
        forbidden = context.manual_forbidden_actions.get(rule.scenario)
        if not forbidden:
            return True
        return rule.action.action_type not in forbidden

    def _check_no_pms_conflict(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Reject rules whose conditions contradict known PMS facts.

        For every key shared between ``rule.conditions`` and
        ``property_constraints`` the validator compares values:

        * Scalar constraint ``(==)``: condition must equal the
          constraint, otherwise reject.
        * Collection constraint (list/tuple/set/frozenset): the
          condition value must be a member of the collection.

        Unrelated keys are ignored — the validator does not require
        the rule to acknowledge every property fact, only to avoid
        contradicting the ones it touches.

        Args:
            rule: Candidate rule.
            context: Optional context.  An empty
                ``property_constraints`` mapping no-ops.

        Returns:
            ``True`` when no contradictions are observed.
        """
        if not context.property_constraints:
            return True
        for key, expected in context.property_constraints.items():
            if key not in rule.conditions:
                continue
            actual = rule.conditions[key]
            if isinstance(expected, (list, tuple, set, frozenset)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def _check_no_active_blocker_conflict(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Reject rules that would short-circuit an active blocker.

        Active blockers gate specific actions at runtime (e.g. "no
        late checkouts during Eid weekend").  A learned rule that
        recommends one of those actions cannot be promoted to AUTO
        without bypassing the blocker, so the validator rejects it
        outright; the priority chain still routes the request via
        the blocker tier at runtime.

        Args:
            rule: Candidate rule.
            context: Optional context.  Empty
                ``active_blocker_actions`` no-ops.

        Returns:
            ``True`` when no active blocker covers the rule's action.
        """
        if not context.active_blocker_actions:
            return True
        return rule.action.action_type not in context.active_blocker_actions

    def _check_no_hidden_variable(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Flag rules whose conditions look too sparse for their support.

        High statistical confidence on a sparsely-conditioned rule is
        a classic hidden-variable warning sign: the dominant decision
        is real, but the rule fails to capture *why* — meaning an
        unobserved feature is doing the work and the rule will
        misfire when that feature changes.

        The heuristic compares ``support_count`` against
        ``hidden_variable_min_support``: once the support exceeds the
        threshold the rule must carry at least
        ``hidden_variable_min_conditions`` conditions to remain
        valid.  Below the support threshold the rule is too small
        for hidden-variable analysis to be meaningful and the check
        no-ops.

        Args:
            rule: Candidate rule.
            context: Optional context (uses thresholds from the
                context, defaults from module constants when
                ``context`` is the empty default).

        Returns:
            ``True`` when no hidden-variable warning is raised.
        """
        if rule.support_count < context.hidden_variable_min_support:
            return True
        return len(rule.conditions) >= context.hidden_variable_min_conditions

    def _check_no_complaint_recovery_contamination(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Reject rules dominated by complaint-recovery source cases.

        Goodwill compensations granted during complaint resolution
        do not generalise to ordinary requests: the PM's intent was
        to defuse, not to set policy.  A rule whose source cases are
        majority-complaint-recovery is therefore filtered out.  The
        ratio is computed against ``len(rule.source_case_ids)``;
        rules whose support contains zero recorded ids no-op.

        Args:
            rule: Candidate rule.
            context: Optional context (consults
                ``complaint_compensation_case_ids`` and
                ``complaint_contamination_threshold``).

        Returns:
            ``True`` when the contamination ratio is at or below the
            threshold (or no signal is available).
        """
        if not context.complaint_compensation_case_ids:
            return True
        if not rule.source_case_ids:
            return True
        ratio = self._complaint_recovery_ratio(rule, context)
        return ratio <= context.complaint_contamination_threshold

    def _check_static_dynamic_source(
        self,
        rule: PatternRule,
        context: ValidationContext,
    ) -> bool:
        """Reject static rules that condition on dynamic-fetch fields.

        The staticity layer classifies sensitive fields (access
        codes, wifi passwords, balances, …) as
        ``DYNAMIC_FETCH_LIVE`` or
        ``SECRET_DYNAMIC_FETCH_ONLY``.  A rule that reads any such
        field in its conditions but does not declare its own action
        as :attr:`DecisionType.FETCH_LIVE_DATA` would race against
        live state and is therefore rejected — the rule must either
        drop the dynamic condition or upgrade its action.

        Args:
            rule: Candidate rule.
            context: Optional context.  Empty
                ``dynamic_field_names`` no-ops.

        Returns:
            ``True`` when no dynamic-field collision is observed.
        """
        if not context.dynamic_field_names:
            return True
        if rule.action.action_type is DecisionType.FETCH_LIVE_DATA:
            return True
        return not (set(rule.conditions) & context.dynamic_field_names)

    # -------------------------------------------------------------------
    # P3 helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _complaint_recovery_ratio(
        rule: PatternRule,
        context: ValidationContext,
    ) -> float:
        """Return the share of ``rule.source_case_ids`` flagged as complaint.

        Returns ``0.0`` when the rule has no source cases on file —
        the contamination signal is undefined for those rules and
        the contamination check no-ops separately.
        """
        if not rule.source_case_ids:
            return 0.0
        contaminated = sum(
            1
            for cid in rule.source_case_ids
            if cid in context.complaint_compensation_case_ids
        )
        return contaminated / len(rule.source_case_ids)

    # -------------------------------------------------------------------
    # Mode downgrade
    # -------------------------------------------------------------------

    def _downgrade_mode(self, rule: PatternRule) -> ExecutionMode:
        """Suggest a safer execution mode for a failed rule.

        If a rule fails validation but has some support, it can still
        be useful in ASK or APPROVAL mode rather than being discarded.

        Args:
            rule: The failed rule.

        Returns:
            Recommended safer execution mode.
        """
        if rule.risk_level == RiskLevel.CRITICAL:
            return ExecutionMode.BLOCK
        if rule.risk_level == RiskLevel.HIGH:
            return ExecutionMode.APPROVAL
        if rule.support_count >= 2:
            return ExecutionMode.ASK
        return ExecutionMode.APPROVAL
