"""Behaviour of :class:`StyleRegistry`."""

from __future__ import annotations

from brain_engine.planner.registry import StyleRegistry
from brain_engine.planner.styles import (
    PlannerStyleId,
    PlannerStyleSpec,
)


def test_registry_seeds_six_builtins() -> None:
    """Construction populates every built-in style id."""
    registry = StyleRegistry()
    assert set(registry.known_ids()) == set(PlannerStyleId)


def test_get_returns_spec_for_known_id() -> None:
    """Known id round-trips through :meth:`get`."""
    registry = StyleRegistry()
    spec = registry.get(PlannerStyleId.COMPLIANCE_STRICT)
    assert spec.style_id is PlannerStyleId.COMPLIANCE_STRICT


def test_register_overwrites_existing_spec() -> None:
    """Registering with an existing id replaces the builtin."""
    registry = StyleRegistry()
    custom = PlannerStyleSpec(
        style_id=PlannerStyleId.COOPERATIVE,
        description="overridden by DSL",
    )
    registry.register(custom)
    assert registry.get(PlannerStyleId.COOPERATIVE).description == (
        "overridden by DSL"
    )


def test_registry_is_isolated_per_instance() -> None:
    """A custom override on one registry does not leak to another."""
    a = StyleRegistry()
    b = StyleRegistry()
    a.register(
        PlannerStyleSpec(
            style_id=PlannerStyleId.COOPERATIVE,
            description="only on a",
        )
    )
    assert a.get(PlannerStyleId.COOPERATIVE).description == "only on a"
    assert b.get(PlannerStyleId.COOPERATIVE).description != "only on a"
