"""Runtime routing of learned :class:`PatternRule` objects.

Pattern extraction produces rules; extraction alone has no effect on
guest conversations until some runtime component consults them.
:class:`PatternRuleRouter` is that component.  Given the scenario
inferred by :class:`~brain_engine.patterns.classifier.DecisionClassifier`
and a feature dict derived from the live
:class:`~brain_engine.conversation.models.PipelineState`, the router
returns the single highest-confidence active rule whose conditions are
satisfied — or ``None`` when no rule matches.

The router does not decide *how* a matched rule is used (prompt
injection, direct execution, approval request); those policies live in
the consumers that call :meth:`match`.  The roadmap Section §10
priority chain (manual → blocker → deterministic safety → PatternRule →
preference → ASK) is enforced by the caller, not by this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from brain_engine.patterns.models import (
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.store import PatternRuleStore


@dataclass(frozen=True, slots=True)
class RuleMatch:
    """A successful :class:`PatternRule` match at runtime.

    Attributes:
        rule: The matched PatternRule.
        scope: The scope at which the match was made.
    """

    rule: PatternRule
    scope: PatternScope


class PatternRuleRouter:
    """Selects the best matching active :class:`PatternRule` for a scenario.

    The router queries the underlying :class:`PatternRuleStore`, filters
    out expired rules, and returns the first rule (by confidence
    descending) whose conditions are satisfied by the supplied feature
    dict.  Scope fallback order is: ``PROPERTY`` → ``OWNER`` →
    ``PORTFOLIO``.  Guest-level rules are not consulted here — they are
    the responsibility of the guest-history retriever.

    The class is stateless and can be shared across requests.

    Attributes:
        _rule_store: PatternRule persistence layer.
    """

    def __init__(self, rule_store: PatternRuleStore) -> None:
        self._rule_store = rule_store

    async def match(
        self,
        *,
        scenario: Scenario,
        property_id: str | None = None,
        owner_id: str | None = None,
        portfolio_id: str | None = None,
        features: dict[str, Any],
        as_of: datetime | None = None,
    ) -> RuleMatch | None:
        """Return the highest-confidence active rule that applies.

        Args:
            scenario: Scenario inferred by
                :class:`~brain_engine.patterns.classifier.DecisionClassifier`.
            property_id: Property scope identifier (may be empty).
            owner_id: Owner scope identifier (may be empty).
            portfolio_id: Portfolio scope identifier (may be empty).
            features: Flat dict consumed by
                :meth:`PatternRule.matches_conditions`.
            as_of: Optional point-in-time anchor.  When supplied,
                rules whose ``invalid_at`` is on or before this
                instant — or whose ``deactivated_at`` was already
                set — are filtered out, so the router answers
                "what would Brain Engine have applied for THIS
                reservation, given the registry state at THIS
                date?".  ``None`` (the default) preserves the
                pre-Sprint-1 behaviour: rely solely on the store's
                ``active``/``valid_to`` filter.

        Returns:
            A :class:`RuleMatch` when an active, non-expired rule with
            satisfied conditions exists, ``None`` otherwise.
        """
        for scope, scope_id in (
            (PatternScope.PROPERTY, property_id),
            (PatternScope.OWNER, owner_id),
            (PatternScope.PORTFOLIO, portfolio_id),
        ):
            if not scope_id:
                continue
            matched = await self._match_in_scope(
                scenario=scenario,
                scope=scope,
                scope_id=scope_id,
                features=features,
                as_of=as_of,
            )
            if matched is not None:
                return matched
        return None

    async def _match_in_scope(
        self,
        *,
        scenario: Scenario,
        scope: PatternScope,
        scope_id: str,
        features: dict[str, Any],
        as_of: datetime | None,
    ) -> RuleMatch | None:
        """Return a matching rule at a single scope, or ``None``.

        The store contract guarantees rules sorted by confidence
        descending, so the first rule that matches wins.
        """
        candidates = await self._rule_store.get_active_rules(
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
        )
        for rule in candidates:
            if rule.is_expired:
                continue
            if not _is_valid_at(rule, as_of):
                continue
            if rule.matches_conditions(features):
                return RuleMatch(rule=rule, scope=scope)
        return None


def _is_valid_at(rule: PatternRule, as_of: datetime | None) -> bool:
    """Return ``True`` when ``rule`` is valid at the ``as_of`` instant.

    When ``as_of`` is ``None`` the function reduces to the legacy
    "rule must not be deactivated" check (pre-Sprint-1 behaviour).
    When ``as_of`` is supplied the bi-temporal filter applies:

    * ``valid_from <= as_of`` — rule had already taken effect
    * ``invalid_at`` IS NULL or > ``as_of`` — rule had not yet been
      supplanted in the *real world*
    * ``deactivated_at`` IS NULL or > ``as_of`` — Brain Engine had
      not yet *learned* about the supplanting
    """
    if as_of is None:
        return rule.deactivated_at is None
    if rule.valid_from is not None and rule.valid_from > as_of:
        return False
    if rule.invalid_at is not None and rule.invalid_at <= as_of:
        return False
    return (
        rule.deactivated_at is None or rule.deactivated_at > as_of
    )
