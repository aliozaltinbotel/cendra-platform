"""Selection logic of :class:`StyleSelector`."""

from __future__ import annotations

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.planner.context import PlannerContext
from brain_engine.planner.registry import StyleRegistry
from brain_engine.planner.selector import StyleSelector
from brain_engine.planner.styles import PlannerStyleId


class _StubResolver:
    """:class:`OwnerStyleResolver` test double."""

    def __init__(
        self,
        mapping: dict[str, PlannerStyleId] | None = None,
    ) -> None:
        self._mapping = mapping or {}

    def resolve(self, owner_id: str) -> PlannerStyleId | None:
        return self._mapping.get(owner_id)


def _ctx(**overrides: object) -> PlannerContext:
    """Build a :class:`PlannerContext` with sensible defaults."""
    defaults: dict[str, object] = {
        "property_id": "prop_x",
        "owner_id": "owner_x",
        "action_kind": CardActionKind.SEND_MESSAGE,
    }
    defaults.update(overrides)
    return PlannerContext(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def empty_resolver() -> _StubResolver:
    """Resolver that never pins any owner."""
    return _StubResolver()


@pytest.fixture
def selector_with(empty_resolver: _StubResolver) -> StyleSelector:
    """Selector wired with an empty resolver and fresh registry."""
    return StyleSelector(
        registry=StyleRegistry(),
        owner_resolver=empty_resolver,
    )


def test_owner_pin_wins_over_jurisdiction() -> None:
    """Owner-pinned style overrides regulated-jurisdiction default."""
    resolver = _StubResolver(
        {"owner_a": PlannerStyleId.AGGRESSIVE_REVENUE}
    )
    selector = StyleSelector(
        registry=StyleRegistry(),
        owner_resolver=resolver,
    )
    decision = selector.pick(
        _ctx(owner_id="owner_a", jurisdiction="BCN")
    )
    assert decision.style_id is PlannerStyleId.AGGRESSIVE_REVENUE
    assert "owner_a" in decision.rationale


def test_regulated_jurisdiction_forces_compliance_strict(
    selector_with: StyleSelector,
) -> None:
    """BCN forces compliance_strict when no owner pin exists."""
    decision = selector_with.pick(_ctx(jurisdiction="BCN"))
    assert decision.style_id is PlannerStyleId.COMPLIANCE_STRICT
    assert "BCN" in decision.rationale


def test_regulated_jurisdiction_lookup_is_case_insensitive(
    selector_with: StyleSelector,
) -> None:
    """Lower-case jurisdiction codes still match the regulated set."""
    decision = selector_with.pick(_ctx(jurisdiction="bcn"))
    assert decision.style_id is PlannerStyleId.COMPLIANCE_STRICT


def test_high_severity_falls_back_to_defensive(
    selector_with: StyleSelector,
) -> None:
    """`critical` severity in unregulated city picks defensive."""
    decision = selector_with.pick(
        _ctx(jurisdiction="DEN", severity="critical"),
    )
    assert decision.style_id is PlannerStyleId.DEFENSIVE


def test_warn_severity_falls_back_to_defensive(
    selector_with: StyleSelector,
) -> None:
    """`warn` severity also picks defensive when no other rule fits."""
    decision = selector_with.pick(_ctx(severity="warn"))
    assert decision.style_id is PlannerStyleId.DEFENSIVE


def test_default_is_cooperative(
    selector_with: StyleSelector,
) -> None:
    """No pin, no risk markers — falls through to cooperative."""
    decision = selector_with.pick(_ctx())
    assert decision.style_id is PlannerStyleId.COOPERATIVE
    assert "default cooperative" in decision.rationale


def test_owner_pin_short_circuits_severity(
    selector_with: StyleSelector,
) -> None:
    """Owner pin wins even when severity would default to defensive."""
    resolver = _StubResolver(
        {"owner_pin": PlannerStyleId.VIP_WHITE_GLOVE}
    )
    selector = StyleSelector(
        registry=StyleRegistry(),
        owner_resolver=resolver,
    )
    decision = selector.pick(
        _ctx(owner_id="owner_pin", severity="critical"),
    )
    assert decision.style_id is PlannerStyleId.VIP_WHITE_GLOVE
