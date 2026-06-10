"""End-to-end DSL → resolver → planner integration."""

from __future__ import annotations

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.owner_policy.registry import load_owner_policy
from brain_engine.planner.context import PlannerContext
from brain_engine.planner.registry import StyleRegistry
from brain_engine.planner.selector import StyleSelector
from brain_engine.planner.styles import PlannerStyleId


_SOURCE = '''
owner "owner_alpha" {
  style = vip_white_glove;
  jurisdiction = "BCN";
  forbid: charge_fee;
}

owner "owner_beta" {
  style = aggressive_revenue;
}
'''


def test_resolver_round_trips_owner_pin() -> None:
    """`load_owner_policy` returns a resolver tied to the registry."""
    registry = StyleRegistry()
    resolver = load_owner_policy(source=_SOURCE, registry=registry)
    assert resolver.resolve("owner_alpha") is (
        PlannerStyleId.VIP_WHITE_GLOVE
    )
    assert resolver.resolve("owner_beta") is (
        PlannerStyleId.AGGRESSIVE_REVENUE
    )
    assert resolver.resolve("owner_unknown") is None


def test_resolver_jurisdiction_lookup() -> None:
    """Jurisdiction declared in the DSL is queryable."""
    registry = StyleRegistry()
    resolver = load_owner_policy(source=_SOURCE, registry=registry)
    assert resolver.jurisdiction_for("owner_alpha") == "BCN"
    assert resolver.jurisdiction_for("owner_beta") is None


def test_selector_uses_dsl_resolver_for_pinned_owner() -> None:
    """`StyleSelector` picks the DSL-pinned style for the owner."""
    registry = StyleRegistry()
    resolver = load_owner_policy(source=_SOURCE, registry=registry)
    selector = StyleSelector(
        registry=registry,
        owner_resolver=resolver,
    )
    decision = selector.pick(
        PlannerContext(
            property_id="prop_x",
            owner_id="owner_alpha",
            action_kind=CardActionKind.SEND_MESSAGE,
        )
    )
    assert decision.style_id is PlannerStyleId.VIP_WHITE_GLOVE


def test_dsl_forbid_extends_envelope_visible_to_selector() -> None:
    """The selector's resolved spec includes the DSL forbid."""
    registry = StyleRegistry()
    resolver = load_owner_policy(source=_SOURCE, registry=registry)
    selector = StyleSelector(
        registry=registry,
        owner_resolver=resolver,
    )
    decision = selector.pick(
        PlannerContext(
            property_id="prop_x",
            owner_id="owner_alpha",
            action_kind=CardActionKind.CHARGE_FEE,
        )
    )
    assert decision.spec.forbids(CardActionKind.CHARGE_FEE)


def test_unknown_owner_falls_back_to_default_selector_path() -> None:
    """Owners not in the DSL fall through to the safety default."""
    registry = StyleRegistry()
    resolver = load_owner_policy(source=_SOURCE, registry=registry)
    selector = StyleSelector(
        registry=registry,
        owner_resolver=resolver,
    )
    decision = selector.pick(
        PlannerContext(
            property_id="prop_x",
            owner_id="owner_unknown",
            action_kind=CardActionKind.SEND_MESSAGE,
            jurisdiction="BCN",
        )
    )
    # Regulated jurisdiction kicks in for unknown owners.
    assert decision.style_id is PlannerStyleId.COMPLIANCE_STRICT
