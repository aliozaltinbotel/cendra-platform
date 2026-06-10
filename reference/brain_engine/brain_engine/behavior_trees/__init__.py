"""Behavior-tree composition layer for Brain Engine.

Brain Engine ships an HTN + LATS planner stack (M12 / M15 / M16)
for *deliberative* decision-making.  This module adds a
complementary *reactive* layer: hierarchical Behavior Trees
backed by the well-known py_trees library (Apache 2.0).
Behavior Trees excel at composing short-lived guard chains and
recovery fallbacks across heterogeneous tools — exactly the
shape that gluing M1 abstention, M2 owner-policy, M3 autonomy
certificates and the per-tool action implementations together
takes inside the conversation runtime.

Public surface (kept small on purpose):

- :class:`Status` — re-export of ``py_trees.common.Status``.
- :class:`ConditionBehaviour` — wraps any callable
  ``(context) -> bool`` into a BT leaf.
- :class:`ActionBehaviour` — wraps any callable
  ``(context) -> Status`` into a BT leaf with side effects.
- :func:`sequence`, :func:`selector`, :func:`parallel` — small
  helpers that build the py_trees composite primitives with a
  Brain-Engine-flavoured default config (memoryless + explicit
  names so audit logs survive py_trees' implicit uuids).
- :class:`TreeRunner` — orchestrator that ticks a tree against
  a typed :class:`TreeContext` until terminal, collecting per-
  tick :class:`TickRecord` entries for the audit log.

Defensibility note
------------------
Behavior Trees are *commodity* — py_trees, NavStack, BehaviorTree.CPP
have decades of prior art.  Brain Engine does not claim novelty
on the BT runtime; we claim novelty on the *composition* of M1-
M25 moats *inside* the tree (the leaves wrap our patent-defensible
gates).  Keeping py_trees as the engine simply means our patent
work is on the leaves, not on the BT primitives themselves.
"""

from __future__ import annotations

from brain_engine.behavior_trees.models import (
    ActionBehaviour,
    ConditionBehaviour,
    Status,
    TickRecord,
    TreeContext,
)
from brain_engine.behavior_trees.composer import (
    parallel,
    selector,
    sequence,
)
from brain_engine.behavior_trees.runner import (
    DEFAULT_MAX_TICKS,
    TreeRunner,
    TreeRunResult,
)


__all__ = [
    "ActionBehaviour",
    "ConditionBehaviour",
    "DEFAULT_MAX_TICKS",
    "Status",
    "TickRecord",
    "TreeContext",
    "TreeRunner",
    "TreeRunResult",
    "parallel",
    "selector",
    "sequence",
]
