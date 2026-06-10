"""ExecutionOrchestrator — walks the §10 priority chain.

The chain is the heart of the runtime: every guest-message turn
gets a single :class:`Decision` by walking six tiers in order:

    1. manual / immutable rule
    2. live blocker
    3. deterministic safety rule
    4. high-confidence learned PatternRule
    5. owner preference   (:class:`OwnerFlexibilityProfile`)
    6. low-confidence "ask clarifying question"

The first tier that returns a non-``None`` Decision wins.  No tier
runs after a winner — the chain short-circuits so a one-off
goodwill exception cannot pollute the decision when an explicit
manual rule already fired.

Branch 1 ships:

* the chain and Decision/Context contracts (in
  :mod:`brain_engine.orchestrator.decision`)
* a real preference-tier resolver wired to
  :class:`OwnerProfileStore`
* stub resolvers for the other five tiers — the
  :class:`TierResolver` Protocol fixes the shape so Branches 2-4
  can plug richer evaluators in without rewiring the orchestrator

The orchestrator deliberately does NOT call live integrations at
this layer.  Live PMS / ops fetches happen upstream and land on
:class:`DecisionContext` so the decision logic stays deterministic
and easy to test.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from brain_engine.orchestrator.decision import (
    PRIORITY_TIERS,
    Decision,
    DecisionContext,
    PriorityTier,
)
from brain_engine.owner_profile.store import OwnerProfileStore

__all__ = [
    "ExecutionOrchestrator",
    "TierResolver",
    "preference_tier_from_owner_profile",
]


logger = structlog.get_logger(__name__)


@runtime_checkable
class TierResolver(Protocol):
    """Protocol for one tier of the §10 priority chain.

    A resolver returns a :class:`Decision` when this tier fires,
    or ``None`` to defer to the next tier.
    """

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        """Return a Decision when this tier fires, ``None`` to defer."""
        ...


class _NoopResolver:
    """Default resolver that always defers (``evaluate`` → ``None``).

    Used as the placeholder for tiers Branch 1 does not yet own —
    Branches 2-4 will swap real implementations in without touching
    :class:`ExecutionOrchestrator`.
    """

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        return None


class _OwnerPreferenceResolver:
    """Tier 5 (preference): consults an :class:`OwnerProfileStore`.

    The profile becomes a Decision in two ways:

    * **Hard limits** (e.g. ``max_guests``, ``hard_min_stay_floor``)
      that the message obviously violates produce a ``deny``
      Decision in mode ``"approval"``.
    * **Soft permissions** (e.g. ``pets_allowed=True``) let the
      orchestrator emit an ``approve`` Decision in mode ``"auto"``.

    Branch 1 implements the rules the smoke flow exercises:
    pets-allowed gating, guest-count cap, and hard-min-stay floor.
    Richer rules land in later branches once the pattern engine is
    wired.

    Attributes:
        _store: Underlying :class:`OwnerProfileStore`.
    """

    def __init__(self, store: OwnerProfileStore) -> None:
        self._store = store

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        if not ctx.owner_id or not ctx.property_id:
            return None
        profile = await self._store.get(ctx.owner_id, ctx.property_id)
        if profile is None:
            return None

        decision = self._evaluate_pet_request(ctx, profile)
        if decision is not None:
            return decision

        decision = self._evaluate_guest_count(ctx, profile)
        if decision is not None:
            return decision

        decision = self._evaluate_min_stay(ctx, profile)
        if decision is not None:
            return decision

        return None

    @staticmethod
    def _evaluate_pet_request(
        ctx: DecisionContext,
        profile: object,
    ) -> Decision | None:
        if ctx.scenario != "pet_request":
            return None
        # ``profile`` is typed as ``object`` to keep the helper
        # importable in tests without paying for a circular hint —
        # mypy strict resolves the attribute through the call site.
        allowed = profile.occupancy_capacity.pets_allowed  # type: ignore[attr-defined]
        if allowed is True:
            return Decision(
                action="approve",
                mode="auto",
                tier="preference",
                rationale="owner_profile.pets_allowed=true",
            )
        if allowed is False:
            return Decision(
                action="deny",
                mode="approval",
                tier="preference",
                rationale="owner_profile.pets_allowed=false",
            )
        return None

    @staticmethod
    def _evaluate_guest_count(
        ctx: DecisionContext,
        profile: object,
    ) -> Decision | None:
        stated_count = ctx.extracted_entities.get("stated_guest_count")
        max_guests = profile.occupancy_capacity.max_guests  # type: ignore[attr-defined]
        if (
            isinstance(stated_count, int)
            and isinstance(max_guests, int)
            and stated_count > max_guests
        ):
            return Decision(
                action="deny",
                mode="approval",
                tier="preference",
                params={
                    "max_guests": max_guests,
                    "stated_guest_count": stated_count,
                },
                rationale=(
                    f"stated_guest_count={stated_count} > "
                    f"owner_profile.max_guests={max_guests}"
                ),
            )
        return None

    @staticmethod
    def _evaluate_min_stay(
        ctx: DecisionContext,
        profile: object,
    ) -> Decision | None:
        requested_nights = ctx.extracted_entities.get("requested_nights")
        floor = profile.stay_rules.hard_min_stay_floor  # type: ignore[attr-defined]
        if (
            isinstance(requested_nights, int)
            and isinstance(floor, int)
            and requested_nights < floor
        ):
            return Decision(
                action="deny",
                mode="approval",
                tier="preference",
                params={
                    "hard_min_stay_floor": floor,
                    "requested_nights": requested_nights,
                },
                rationale=(
                    f"requested_nights={requested_nights} < "
                    f"owner_profile.hard_min_stay_floor={floor}"
                ),
            )
        return None


def preference_tier_from_owner_profile(
    store: OwnerProfileStore,
) -> TierResolver:
    """Construct the standard preference-tier resolver."""
    return _OwnerPreferenceResolver(store)


class _AskFallbackResolver:
    """Tier 6 (ask): always emits a clarifying-question Decision.

    Sits at the very end of the chain so the orchestrator never
    returns ``None`` — when no other tier fires, the runtime asks
    the guest for clarification instead of guessing.
    """

    async def evaluate(self, ctx: DecisionContext) -> Decision | None:
        return Decision(
            action="ask",
            mode="ask",
            tier="ask",
            rationale="no higher-priority tier fired — fall through to ask",
        )


class ExecutionOrchestrator:
    """Walks the §10 priority chain to produce a :class:`Decision`.

    Tier resolvers are injected via the constructor — each one must
    satisfy the :class:`TierResolver` Protocol.  Branch 1 supplies
    real implementations for tier 5 (preference) and tier 6 (ask
    fallback); the other four tiers default to no-op resolvers and
    are intended to be replaced by Branches 2-4.

    The orchestrator is async because the preference tier hits the
    Postgres owner-profile store, and Branches 2-4 will hit Redis
    (blockers) and a Postgres pattern store.

    Attributes:
        _resolvers: Mapping of tier name → resolver.
        _log: Structured logger bound to this component.
    """

    def __init__(
        self,
        *,
        preference: TierResolver,
        manual: TierResolver | None = None,
        blocker: TierResolver | None = None,
        safety: TierResolver | None = None,
        learned: TierResolver | None = None,
        ask: TierResolver | None = None,
    ) -> None:
        self._resolvers: dict[PriorityTier, TierResolver] = {
            "manual": manual or _NoopResolver(),
            "blocker": blocker or _NoopResolver(),
            "safety": safety or _NoopResolver(),
            "learned": learned or _NoopResolver(),
            "preference": preference,
            "ask": ask or _AskFallbackResolver(),
        }
        self._log = logger.bind(component="execution_orchestrator")

    async def decide(self, ctx: DecisionContext) -> Decision:
        """Walk the priority chain and return the first decision.

        Args:
            ctx: Inputs for the decision.

        Returns:
            The :class:`Decision` from the highest-priority tier
            that fired.  The "ask" tier always fires when reached,
            so this method never returns ``None``.

        Raises:
            RuntimeError: When the configured ``ask`` resolver
                defers — that is a misconfiguration the orchestrator
                surfaces loudly instead of silently returning a
                falsy value.
        """
        for tier in PRIORITY_TIERS:
            resolver = self._resolvers[tier]
            decision = await resolver.evaluate(ctx)
            if decision is None:
                continue
            self._log.debug(
                "tier_fired",
                tier=tier,
                action=decision.action,
                mode=decision.mode,
                scenario=ctx.scenario,
            )
            _emit_decision_metric(
                tier=str(tier), mode=str(decision.mode),
            )
            return decision
        raise RuntimeError(
            "execution_orchestrator: ask tier returned None — "
            "the ask resolver must always emit a Decision",
        )


def _emit_decision_metric(*, tier: str, mode: str) -> None:
    """Forward the §10 chain decision to the Prometheus exporter.

    Best-effort — any exporter exception is swallowed so a broken
    metrics registry can never block a guest reply.  The hot path
    cannot afford to fail because observability is unhappy.
    """
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        build_default_exporter().record_orchestrator_decision(
            tier=tier, mode=mode,
        )
    except Exception:  # noqa: BLE001 — never break the chain
        return
