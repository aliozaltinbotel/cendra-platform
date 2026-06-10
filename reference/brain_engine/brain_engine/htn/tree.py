"""LATS-style partial-plan tree (Moat #12 v0.1 seam for v1.0 search).

Brain Engine ships the planner of :mod:`brain_engine.htn.planner`
as a deterministic recursive descent — adequate for the moat-
defensibility claim.  The seam where the LATS / MCTS expansion
will plug in for v1.0 is the :class:`PlanTreeNode` data model
below: each node represents one partial plan, carries a
:class:`PlanTreeStatus`, and exposes ``score`` / ``visits``
fields the v1.0 search will populate.

For v0.1 the tree is hand-built from a deterministic plan via
:func:`tree_from_plan` so callers can already audit / render the
plan as a tree even though the search itself is linear.

References:
    Zhou et al. (2023).  *Language Agent Tree Search*.
    arXiv:2310.04406.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from brain_engine.htn.models import HTNPlan, Operator


__all__ = [
    "PlanTreeNode",
    "PlanTreeStatus",
    "ValueEstimator",
    "tree_from_plan",
]


class PlanTreeStatus(StrEnum):
    """Lifecycle of one partial-plan node."""

    UNEXPANDED = "unexpanded"
    EXPANDED = "expanded"
    EVALUATED = "evaluated"
    PRUNED = "pruned"


class ValueEstimator(Protocol):
    """R-WoM-style value estimator the search consults.

    v1.0 implementations retrieve trajectory-similar past plans
    from a knowledge base and score the node accordingly; v0.1
    callers wire a stub that returns a constant.
    """

    def score(self, node: "PlanTreeNode") -> float:
        """Return the predicted value of completing the node."""
        ...


@dataclass(slots=True)
class PlanTreeNode:
    """One partial plan in the LATS tree.

    Attributes:
        depth: Tree depth (root = 0).
        operators: Operators reached from the root.
        children: Ordered tuple of child nodes.
        status: Lifecycle status.
        score: Predicted value (filled by ``ValueEstimator``).
            Defaults to ``0.0``.
        visits: Number of times the v1.0 search visited the node;
            unused in v0.1, kept for API stability.
    """

    depth: int
    operators: tuple[Operator, ...]
    children: tuple["PlanTreeNode", ...] = ()
    status: PlanTreeStatus = PlanTreeStatus.UNEXPANDED
    score: float = 0.0
    visits: int = 0

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.visits < 0:
            raise ValueError("visits must be non-negative")

    @property
    def is_leaf(self) -> bool:
        """Whether the node has no children yet."""
        return not self.children


def tree_from_plan(plan: HTNPlan) -> PlanTreeNode:
    """Build a linear tree from a deterministic :class:`HTNPlan`.

    The tree has exactly one node per operator — the last
    operator is the leaf (no extra empty terminal node).  Useful
    for audit rendering even in v0.1 where the planner is
    non-branching.
    """
    if not plan.operators:
        return PlanTreeNode(
            depth=0,
            operators=(),
            status=PlanTreeStatus.EVALUATED,
        )
    return _chain_node(operators=plan.operators, depth=0)


def _chain_node(
    *,
    operators: Sequence[Operator],
    depth: int,
) -> PlanTreeNode:
    head = operators[0]
    prefix = (head,)
    if len(operators) == 1:
        return PlanTreeNode(
            depth=depth,
            operators=prefix,
            status=PlanTreeStatus.EVALUATED,
        )
    child = _chain_node(
        operators=operators[1:],
        depth=depth + 1,
    )
    # The child carries the head + its own prefix.
    expanded_child = PlanTreeNode(
        depth=child.depth,
        operators=prefix + child.operators,
        children=child.children,
        status=child.status,
        score=child.score,
        visits=child.visits,
    )
    return PlanTreeNode(
        depth=depth,
        operators=prefix,
        children=(expanded_child,),
        status=PlanTreeStatus.EXPANDED,
    )
