"""GR00T P1 Kinematic Planner layer.

The Planner layer separates *which behaviour style* applies to a
decision from *how the action is executed*.  This is a direct
transfer of NVIDIA GR00T-WholeBodyControl's two-layer architecture
(github.com/NVlabs/GR00T-WholeBodyControl, SONIC paper
arXiv:2511.07820, Nov 2025) from physical robotics to cognitive
autonomy.

Public surface:

- :class:`PlannerStyleId` — six built-in styles enum.
- :class:`PlannerStyleSpec` — constraint envelope per style.
- :class:`PlannerContext` — selector input.
- :class:`PlannerDecision` — selector output (style + rationale).
- :class:`StyleRegistry` — built-in + DSL-extended catalogue.
- :class:`OwnerStyleResolver` — Protocol the Owner-policy DSL
  (Moat #2) implements to pin styles per owner.
- :class:`StyleSelector` — picks one style per context.
- :class:`StyleAppliedCard` / :func:`apply_style` — adjust a built
  :class:`brain_engine.cards.models.DecisionCard` by the picked
  envelope without modifying the builder.

Defensibility: the Planner abstraction (style-first selection
*before* handler dispatch in a regulated-domain agent) is the
architectural fact Moat #4 stakes a USPTO / EPO claim on; Moats
#1, #2, #3 layer on top of it.
"""

from __future__ import annotations

from brain_engine.planner.context import PlannerContext
from brain_engine.planner.decision import PlannerDecision
from brain_engine.planner.integration import (
    StyleAppliedCard,
    apply_style,
)
from brain_engine.planner.registry import (
    OwnerStyleResolver,
    StyleNotFoundError,
    StyleRegistry,
)
from brain_engine.planner.selector import StyleSelector
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
    PlannerStyleSpec,
)


__all__ = [
    "BUILTIN_STYLE_SPECS",
    "OwnerStyleResolver",
    "PlannerContext",
    "PlannerDecision",
    "PlannerStyleId",
    "PlannerStyleSpec",
    "StyleAppliedCard",
    "StyleNotFoundError",
    "StyleRegistry",
    "StyleSelector",
    "apply_style",
]
