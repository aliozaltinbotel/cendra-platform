"""Composite builders for the BT layer.

Thin wrappers over :mod:`py_trees.composites` with Brain-Engine-
flavoured defaults:

  * ``memory=False`` — every tick re-evaluates the chain instead
    of resuming from the last RUNNING leaf.  That matches our
    reactive use-case (guard chains, recovery fallbacks) and
    keeps audit logs deterministic.
  * Explicit ``name`` — py_trees defaults to a uuid which makes
    the audit log unreadable; we require the caller to name
    every composite so audit consumers can read the trace.
  * Children are added via the constructor's ``children`` kwarg
    so the call sites read top-down.

The helpers return :class:`py_trees.composites.Composite`
instances directly.  Callers wishing to add metadata or wire the
composites under a parallel root do so against the py_trees API
itself; we deliberately do not re-wrap the composite types.
"""

from __future__ import annotations

from collections.abc import Sequence

import py_trees


__all__ = [
    "parallel",
    "selector",
    "sequence",
]


def sequence(
    *,
    name: str,
    children: Sequence[py_trees.behaviour.Behaviour],
    memory: bool = False,
) -> py_trees.composites.Sequence:
    """Build a :class:`Sequence` composite.

    A Sequence ticks its children left-to-right and short-circuits
    on the first non-SUCCESS child.  In Brain Engine terms the
    Sequence is the AND-chain: ``abstention_proceed AND
    owner_policy_allows AND emit_response``.
    """
    if not name:
        raise ValueError("name required")
    return py_trees.composites.Sequence(
        name=name,
        memory=memory,
        children=list(children),
    )


def selector(
    *,
    name: str,
    children: Sequence[py_trees.behaviour.Behaviour],
    memory: bool = False,
) -> py_trees.composites.Selector:
    """Build a :class:`Selector` composite.

    A Selector ticks its children left-to-right and short-circuits
    on the first non-FAILURE child.  In Brain Engine terms the
    Selector is the OR-chain: ``try_template_response OR
    try_retrieval OR fallback_to_human``.
    """
    if not name:
        raise ValueError("name required")
    return py_trees.composites.Selector(
        name=name,
        memory=memory,
        children=list(children),
    )


def parallel(
    *,
    name: str,
    children: Sequence[py_trees.behaviour.Behaviour],
    policy: py_trees.common.ParallelPolicy.Base | None = None,
) -> py_trees.composites.Parallel:
    """Build a :class:`Parallel` composite.

    A Parallel ticks every child once per tick.  The supplied
    ``policy`` decides how to combine child statuses.  Default
    is :class:`SuccessOnAll` — every child must succeed for the
    Parallel to succeed (Brain Engine's compliance + audit
    chains usually compose this way).
    """
    if not name:
        raise ValueError("name required")
    actual_policy = policy or (
        py_trees.common.ParallelPolicy.SuccessOnAll(
            synchronise=False,
        )
    )
    return py_trees.composites.Parallel(
        name=name,
        policy=actual_policy,
        children=list(children),
    )
