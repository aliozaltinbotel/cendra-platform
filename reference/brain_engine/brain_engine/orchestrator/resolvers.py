"""Concrete tier resolvers for the §10 priority chain.

Branch 2 wires the four upper tiers of
:class:`brain_engine.orchestrator.priority_chain.ExecutionOrchestrator`
to the existing Brain Engine infrastructure:

* tier 1 (manual)   — :class:`ManualDirectiveResolver` consults an
  explicit ``ManualDirectiveStore`` for owner-recorded immutable
  rules (e.g. "never offer discounts on Villa Azul").
* tier 2 (blocker)  — :class:`BlockerResolver` asks
  :class:`brain_engine.blockers.engine.BlockerEngine` whether any
  hard blocker covers the scenario's action, and short-circuits
  with ``mode="block"`` when one does.
* tier 3 (safety)   — :class:`SafetyStaticityResolver` runs
  :class:`brain_engine.staticity.guard.StaticityGuard` for
  scenarios that touch dynamic secrets and forces
  ``action="fetch_live_data"`` whenever the verdict is anything
  other than :pyattr:`VerdictKind.ALLOW_CACHED`.
* tier 4 (learned)  — :class:`LearnedPatternResolver` queries
  :class:`brain_engine.patterns.store.PatternRuleStore` and
  returns the highest-confidence promotable rule whose
  conditions match the runtime feature dict.

The §11 *never-auto-learn* allow-list lives here too:
:data:`NEVER_AUTO_LEARN_SCENARIOS` is enforced inside
:class:`LearnedPatternResolver` so high-confidence rules for
legal / financial / safety-sensitive scenarios always degrade to
``mode="approval"`` no matter what their statistical confidence
suggests.

Tiers 5 (preference) and 6 (ask fallback) keep their existing
homes in :mod:`brain_engine.orchestrator.priority_chain`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol, cast, runtime_checkable

import structlog

from brain_engine.approval.models import ActionType
from brain_engine.blockers.engine import BlockerEngine
from brain_engine.orchestrator.decision import (
    DECISION_ACTIONS,
    EXECUTION_MODES,
    Decision,
    DecisionAction,
    DecisionContext,
    ExecutionMode,
)
from brain_engine.patterns.models import (
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.store import PatternRuleStore
from brain_engine.staticity.guard import StaticityGuard, VerdictKind

__all__ = [
    "DEFAULT_SCENARIO_TO_ACTION",
    "DEFAULT_SCENARIO_TO_STATICITY_FIELD",
    "FeatureBuilder",
    "InMemoryManualDirectiveStore",
    "LearnedPatternResolver",
    "ManualDirective",
    "ManualDirectiveResolver",
    "ManualDirectiveStore",
    "NEVER_AUTO_LEARN_SCENARIOS",
    "BlockerResolver",
    "SafetyStaticityResolver",
    "blocker_tier_from_engine",
    "default_feature_builder",
    "learned_tier_from_pattern_store",
    "manual_tier_from_store",
    "safety_tier_from_guard",
]


logger = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Default mappings
# --------------------------------------------------------------------------- #


DEFAULT_SCENARIO_TO_ACTION: Final[Mapping[str, ActionType]] = {
    "access_code_release": ActionType.SEND_ACCESS_CODE,
    "discount_request": ActionType.OFFER_DISCOUNT,
    "price_negotiation": ActionType.OFFER_DISCOUNT,
    "complaint_compensation": ActionType.OFFER_DISCOUNT,
    "late_checkout": ActionType.LATE_CHECKOUT,
    "early_checkin": ActionType.SEND_ACCESS_CODE,
    "damage_report": ActionType.SUBMIT_DAMAGE_CLAIM,
    "charge_request": ActionType.CHARGE_GUEST,
    "cleaner_dispatch": ActionType.DISPATCH_CLEANER,
    "vendor_dispatch": ActionType.CALL_VENDOR,
}
"""Default scenario → :class:`ActionType` map for blocker scoping.

Scenarios absent from the map make
:class:`BlockerResolver` defer (return ``None``) — blockers cannot
fire without a specific action to gate.
"""


DEFAULT_SCENARIO_TO_STATICITY_FIELD: Final[Mapping[str, str]] = {
    "access_code_release": "access_code",
    "early_checkin": "access_code",
    "wifi_credentials_request": "wifi_password",
    "door_code_request": "door_code",
    "parking_instructions_request": "parking_instructions",
    "checkin_instructions_request": "checkin_instructions",
}
"""Default scenario → staticity field-name map.

The staticity guard is only meaningful for scenarios that read a
specific field whose freshness matters; everything else makes
:class:`SafetyStaticityResolver` defer.
"""


NEVER_AUTO_LEARN_SCENARIOS: Final[frozenset[str]] = frozenset(
    {
        "complaint_compensation",
        "damage_report",
        "charge_request",
        "price_negotiation",
        "cancellation_request",
        "access_code_release",
    },
)
"""§11 allow-list of scenarios that must never auto-execute.

When a learned :class:`PatternRule` matches a scenario in this
set, :class:`LearnedPatternResolver` forces the resulting
Decision to ``mode="approval"`` regardless of the rule's stored
``execution_mode`` — protecting legal, financial, and
safety-sensitive paths from over-eager automation.
"""


# --------------------------------------------------------------------------- #
# Tier 1 — manual directive resolver
# --------------------------------------------------------------------------- #


class ManualDirective:
    """An explicit owner directive for one (property, scenario) cell.

    Manual directives are *immutable* operator overrides — they
    sit at the top of the priority chain and short-circuit every
    learned or preference rule below them.  They are used to
    encode "the owner explicitly told us to never offer discounts
    on Villa Azul", "always escalate damage claims above €500",
    etc.

    Attributes:
        owner_id: Owner the directive belongs to.
        property_id: Property the directive scopes (use ``""`` for
            owner-wide directives).
        scenario: Scenario this directive answers.  Stable string
            shared with :class:`DecisionContext.scenario`.
        action: :data:`DecisionAction` the directive forces.
        mode: :data:`ExecutionMode` to emit alongside the action.
        rationale: Human-readable explanation surfaced to the
            decision-case audit log.
    """

    __slots__ = (
        "action",
        "mode",
        "owner_id",
        "property_id",
        "rationale",
        "scenario",
    )

    def __init__(
        self,
        *,
        owner_id: str,
        property_id: str,
        scenario: str,
        action: DecisionAction,
        mode: ExecutionMode,
        rationale: str = "",
    ) -> None:
        self.owner_id = owner_id
        self.property_id = property_id
        self.scenario = scenario
        self.action = action
        self.mode = mode
        self.rationale = rationale


@runtime_checkable
class ManualDirectiveStore(Protocol):
    """Persistent lookup of :class:`ManualDirective` entries."""

    async def lookup(
        self,
        *,
        owner_id: str,
        property_id: str,
        scenario: str,
    ) -> ManualDirective | None:
        """Return the matching directive when one exists."""
        ...


class InMemoryManualDirectiveStore:
    """Dev/test :class:`ManualDirectiveStore` keyed by triple.

    The store first searches for a (owner, property, scenario)
    match, then falls back to (owner, "", scenario) so an owner
    can record portfolio-wide directives without enumerating
    every property.

    Attributes:
        _by_triple: Cache mapping ``(owner, property, scenario)``
            tuples to directive instances.
    """

    def __init__(self) -> None:
        self._by_triple: dict[
            tuple[str, str, str], ManualDirective
        ] = {}

    async def put(self, directive: ManualDirective) -> None:
        """Store the directive under its (owner, property, scenario) key."""
        key = (directive.owner_id, directive.property_id, directive.scenario)
        self._by_triple[key] = directive

    async def lookup(
        self,
        *,
        owner_id: str,
        property_id: str,
        scenario: str,
    ) -> ManualDirective | None:
        """Property-scoped lookup with portfolio fallback."""
        scoped = self._by_triple.get((owner_id, property_id, scenario))
        if scoped is not None:
            return scoped
        return self._by_triple.get((owner_id, "", scenario))


class ManualDirectiveResolver:
    """Tier 1 resolver — owner-recorded immutable directives.

    Defers (returns ``None``) when no directive matches the
    incoming context.  When a directive does match, the resolver
    emits a :class:`Decision` carrying the directive's
    ``action`` / ``mode`` and a rationale tagged with the manual
    tier so audit logs make the precedence obvious.

    Attributes:
        _store: Backing :class:`ManualDirectiveStore`.
    """

    def __init__(self, store: ManualDirectiveStore) -> None:
        self._store = store
        self._log = logger.bind(component="manual_directive_resolver")

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        if not ctx.owner_id or not ctx.scenario:
            return None
        directive = await self._store.lookup(
            owner_id=ctx.owner_id,
            property_id=ctx.property_id,
            scenario=ctx.scenario,
        )
        if directive is None:
            return None
        rationale = directive.rationale or (
            f"manual_directive(owner={directive.owner_id},"
            f"property={directive.property_id or '*'},"
            f"scenario={directive.scenario})"
        )
        self._log.info(
            "manual_directive_fired",
            owner_id=ctx.owner_id,
            property_id=ctx.property_id,
            scenario=ctx.scenario,
            action=directive.action,
            mode=directive.mode,
        )
        return Decision(
            action=directive.action,
            mode=directive.mode,
            tier="manual",
            rationale=rationale,
        )


def manual_tier_from_store(
    store: ManualDirectiveStore,
) -> ManualDirectiveResolver:
    """Construct the standard manual-tier resolver."""
    return ManualDirectiveResolver(store)


# --------------------------------------------------------------------------- #
# Tier 2 — blocker resolver
# --------------------------------------------------------------------------- #


class BlockerResolver:
    """Tier 2 resolver — fires when a hard blocker covers the action.

    The resolver maps ``ctx.scenario`` to a single
    :class:`ActionType` (via the injected mapping) and asks
    :class:`BlockerEngine` whether any hard blocker is currently
    active for that property/reservation/action triple.  When one
    is, the orchestrator gets ``action="block", mode="block"`` and
    no lower tier runs.

    Attributes:
        _engine: Underlying :class:`BlockerEngine`.
        _scenario_to_action: Per-deployment mapping; defaults to
            :data:`DEFAULT_SCENARIO_TO_ACTION`.
    """

    def __init__(
        self,
        engine: BlockerEngine,
        *,
        scenario_to_action: Mapping[str, ActionType] | None = None,
    ) -> None:
        self._engine = engine
        self._scenario_to_action = (
            scenario_to_action
            if scenario_to_action is not None
            else DEFAULT_SCENARIO_TO_ACTION
        )
        self._log = logger.bind(component="blocker_resolver")

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        action_type = self._scenario_to_action.get(ctx.scenario)
        if action_type is None or not ctx.property_id:
            return None
        reservation = ctx.reservation_id or None
        active = await self._engine.check_blockers(
            property_id=ctx.property_id,
            reservation_id=reservation,
            action_type=action_type,
        )
        hard = [b for b in active if b.is_hard]
        if not hard:
            return None
        types = tuple(b.blocker_type.value for b in hard)
        self._log.warning(
            "blocker_tier_fired",
            scenario=ctx.scenario,
            action=action_type.value,
            property_id=ctx.property_id,
            blocker_types=types,
        )
        return Decision(
            action="block",
            mode="block",
            tier="blocker",
            params={
                "action_type": action_type.value,
                "blocker_types": list(types),
            },
            rationale=(
                f"hard_blocker(types={types}, action={action_type.value})"
            ),
        )


def blocker_tier_from_engine(
    engine: BlockerEngine,
    *,
    scenario_to_action: Mapping[str, ActionType] | None = None,
) -> BlockerResolver:
    """Construct the standard blocker-tier resolver."""
    return BlockerResolver(engine, scenario_to_action=scenario_to_action)


# --------------------------------------------------------------------------- #
# Tier 3 — staticity / safety resolver
# --------------------------------------------------------------------------- #


class SafetyStaticityResolver:
    """Tier 3 resolver — forces live fetches for dynamic-secret fields.

    When the scenario reads a field whose
    :class:`brain_engine.staticity.classifier.StaticityClassifier`
    classification is :pyattr:`StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY`
    or :pyattr:`StaticityLevel.DYNAMIC_FETCH_LIVE`, the resolver
    short-circuits the chain with ``action="fetch_live_data"``.
    Stable / verify-periodically fields make the resolver defer
    so a learned rule can run normally.

    Attributes:
        _guard: :class:`StaticityGuard` used to evaluate fields.
        _scenario_to_field: Per-deployment override for the
            scenario → field name lookup.
    """

    def __init__(
        self,
        guard: StaticityGuard,
        *,
        scenario_to_field: Mapping[str, str] | None = None,
    ) -> None:
        self._guard = guard
        self._scenario_to_field = (
            scenario_to_field
            if scenario_to_field is not None
            else DEFAULT_SCENARIO_TO_STATICITY_FIELD
        )
        self._log = logger.bind(component="safety_staticity_resolver")

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        field_name = self._scenario_to_field.get(ctx.scenario)
        if field_name is None or not ctx.property_id:
            return None
        verdict = self._guard.evaluate(
            field_name=field_name,
            property_id=ctx.property_id,
            cached_age_seconds=None,
        )
        if verdict.kind is VerdictKind.ALLOW_CACHED:
            return None
        self._log.info(
            "safety_tier_fired",
            scenario=ctx.scenario,
            field=field_name,
            verdict_kind=verdict.kind.value,
        )
        return Decision(
            action="fetch_live_data",
            mode="auto",
            tier="safety",
            params={
                "field_name": field_name,
                "verdict_kind": verdict.kind.value,
            },
            rationale=(
                f"staticity_guard({field_name})"
                f" -> {verdict.kind.value}: {verdict.reason}"
            ),
        )


def safety_tier_from_guard(
    guard: StaticityGuard,
    *,
    scenario_to_field: Mapping[str, str] | None = None,
) -> SafetyStaticityResolver:
    """Construct the standard safety-tier resolver."""
    return SafetyStaticityResolver(guard, scenario_to_field=scenario_to_field)


# --------------------------------------------------------------------------- #
# Tier 4 — learned pattern resolver
# --------------------------------------------------------------------------- #


FeatureBuilder = Callable[[DecisionContext], Mapping[str, Any]]
"""Callable that turns a :class:`DecisionContext` into a flat feature dict.

The dict's keys must align with
:attr:`PatternRule.conditions` field names so that
:meth:`PatternRule.matches_conditions` can evaluate them.
"""


def default_feature_builder(ctx: DecisionContext) -> Mapping[str, Any]:
    """Default :data:`FeatureBuilder` — read entities + PMS snapshot.

    Produces a flat feature dict by merging
    :attr:`DecisionContext.extracted_entities` with the top-level
    keys of :attr:`DecisionContext.pms_snapshot` and a few
    derived fields (``scenario``, ``has_reservation``).  Callers
    can supply their own feature builder when they need
    calendar-derived features (gap_before / occupancy_7d / …).

    Args:
        ctx: The orchestrator decision context.

    Returns:
        Flat mapping of feature name → value.
    """
    features: dict[str, Any] = {}
    features.update(ctx.extracted_entities)
    features.update(ctx.pms_snapshot)
    features["scenario"] = ctx.scenario
    features["has_reservation"] = bool(ctx.reservation_id)
    return features


class LearnedPatternResolver:
    """Tier 4 resolver — picks the best matching :class:`PatternRule`.

    The resolver runs three lookups against the
    :class:`PatternRuleStore`, in escalating breadth, and returns
    the first promotable rule whose conditions match the feature
    dict:

    1. ``scope=PROPERTY`` keyed by ``ctx.property_id``
    2. ``scope=OWNER`` keyed by ``ctx.owner_id``
    3. ``scope=PORTFOLIO`` keyed by ``ctx.tenant_id``

    A rule is "promotable" iff
    :pyattr:`PatternRule.is_promotable` is ``True``.  Non-
    promotable rules stay in the store but never short-circuit
    the chain — they degrade gracefully to the preference / ask
    tiers below.

    The §11 allow-list :data:`NEVER_AUTO_LEARN_SCENARIOS` clamps
    every emitted Decision down to ``mode="approval"`` whenever
    the scenario is on the list, even when the underlying rule
    is set to ``execution_mode=AUTO``.

    Attributes:
        _store: Backing :class:`PatternRuleStore`.
        _features: Callable that turns ``ctx`` into a feature dict.
    """

    def __init__(
        self,
        store: PatternRuleStore,
        *,
        feature_builder: FeatureBuilder | None = None,
    ) -> None:
        self._store = store
        self._features = feature_builder or default_feature_builder
        self._log = logger.bind(component="learned_pattern_resolver")

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        scenario = self._coerce_scenario(ctx.scenario)
        if scenario is None:
            return None
        features = self._features(ctx)
        for scope, scope_id in self._scope_lookups(ctx):
            rule = await self._best_rule(scenario, scope, scope_id, features)
            if rule is None:
                continue
            return self._decision_from_rule(rule, ctx.scenario)
        return None

    @staticmethod
    def _coerce_scenario(raw: str) -> Scenario | None:
        try:
            return Scenario(raw)
        except ValueError:
            return None

    @staticmethod
    def _scope_lookups(
        ctx: DecisionContext,
    ) -> tuple[tuple[PatternScope, str], ...]:
        lookups: list[tuple[PatternScope, str]] = []
        if ctx.property_id:
            lookups.append((PatternScope.PROPERTY, ctx.property_id))
        if ctx.owner_id:
            lookups.append((PatternScope.OWNER, ctx.owner_id))
        if ctx.tenant_id:
            lookups.append((PatternScope.PORTFOLIO, ctx.tenant_id))
        return tuple(lookups)

    async def _best_rule(
        self,
        scenario: Scenario,
        scope: PatternScope,
        scope_id: str,
        features: Mapping[str, Any],
    ) -> PatternRule | None:
        rules = await self._store.get_active_rules(
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
        )
        for rule in rules:
            if not rule.is_promotable:
                continue
            if not rule.matches_conditions(dict(features)):
                continue
            return rule
        return None

    def _decision_from_rule(
        self,
        rule: PatternRule,
        scenario: str,
    ) -> Decision | None:
        action_value = rule.action.action_type.value
        if action_value not in DECISION_ACTIONS:
            self._log.debug(
                "rule_action_outside_decision_vocab",
                pattern_id=rule.pattern_id,
                action=action_value,
            )
            return None
        mode_value = rule.execution_mode.value
        mode = self._clamp_mode_for_blacklist(scenario, mode_value)
        self._log.info(
            "learned_tier_fired",
            scenario=scenario,
            pattern_id=rule.pattern_id,
            action=action_value,
            confidence=round(rule.confidence, 3),
            mode=mode,
        )
        return Decision(
            action=cast(DecisionAction, action_value),
            mode=mode,
            tier="learned",
            params=dict(rule.action.params),
            rationale=(
                f"pattern_rule(id={rule.pattern_id[:8]},"
                f" scope={rule.scope.value}:{rule.scope_id},"
                f" confidence={rule.confidence:.2f})"
            ),
        )

    @staticmethod
    def _clamp_mode_for_blacklist(
        scenario: str,
        raw_mode: str,
    ) -> ExecutionMode:
        if scenario in NEVER_AUTO_LEARN_SCENARIOS and raw_mode == "auto":
            return "approval"
        # The patterns ExecutionMode StrEnum mirrors the
        # orchestrator Literal (auto / ask / approval / block),
        # so a runtime-validated cast is safe.  Anything outside
        # the known set degrades to "approval" rather than
        # silently widening the type.
        if raw_mode in EXECUTION_MODES:
            return cast(ExecutionMode, raw_mode)
        return "approval"


def learned_tier_from_pattern_store(
    store: PatternRuleStore,
    *,
    feature_builder: FeatureBuilder | None = None,
) -> LearnedPatternResolver:
    """Construct the standard learned-tier resolver."""
    return LearnedPatternResolver(store, feature_builder=feature_builder)
