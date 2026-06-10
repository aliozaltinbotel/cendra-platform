"""Pareto-frontier search across multi-stakeholder utility scores.

An :class:`ActionCandidate` is on the Pareto frontier when no other
candidate *dominates* it: there is no candidate ``B`` such that
``B`` scores at least as well for every stakeholder *and* strictly
better for at least one.

The two helpers here are pure-Python and ``O(n²)`` in the number
of candidates — fine for the small action sets the planner emits
(typically under a hundred).  Larger sets would warrant a smarter
sweep, but the current call sites do not need it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from brain_engine.stakeholders.models import (
    ActionCandidate,
    StakeholderId,
)


__all__ = ["dominates", "pareto_frontier"]


def dominates(
    a: Mapping[StakeholderId, float],
    b: Mapping[StakeholderId, float],
) -> bool:
    """Return ``True`` when ``a`` Pareto-dominates ``b``.

    Both inputs must share the same keys.  ``a`` dominates ``b``
    iff ``a[k] >= b[k]`` for every ``k`` and ``a[k] > b[k]`` for
    at least one ``k``.
    """
    if a.keys() != b.keys():
        raise KeyError(
            "utility maps must share the same stakeholder keys"
        )
    strictly_greater = False
    for key in a:
        if a[key] < b[key]:
            return False
        if a[key] > b[key]:
            strictly_greater = True
    return strictly_greater


def pareto_frontier(
    candidates: Sequence[ActionCandidate],
    utilities: Mapping[
        str, Mapping[StakeholderId, float],
    ],
) -> tuple[ActionCandidate, ...]:
    """Return the subset of ``candidates`` on the Pareto frontier.

    Args:
        candidates: All actions under consideration.
        utilities: Per-action per-stakeholder scores keyed by
            ``ActionCandidate.action_id``.

    Returns:
        A tuple of frontier candidates in the input order.
    """
    frontier: list[ActionCandidate] = []
    for outer in candidates:
        outer_score = utilities[outer.action_id]
        if not _is_dominated(
            outer_score, candidates, utilities, exclude=outer.action_id
        ):
            frontier.append(outer)
    return tuple(frontier)


def _is_dominated(
    score: Mapping[StakeholderId, float],
    candidates: Sequence[ActionCandidate],
    utilities: Mapping[str, Mapping[StakeholderId, float]],
    *,
    exclude: str,
) -> bool:
    """Return ``True`` when any other candidate dominates ``score``."""
    for inner in candidates:
        if inner.action_id == exclude:
            continue
        if dominates(utilities[inner.action_id], score):
            return True
    return False
