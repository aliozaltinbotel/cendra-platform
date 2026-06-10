"""LATS-style Monte-Carlo tree search over HTN method choices.

Closes the deferred ``LATS MCTS expansion`` from M12 (PR #218).
Brain Engine's HTN planner in :mod:`brain_engine.htn.planner` is
deterministic — it picks the first applicable method per task.
That is enough when each task has one valid method, but loses
optimisation room when several methods compete (e.g. send_warning
vs send_warning_then_check_history vs escalate_immediately).

This module adds the search:

- :class:`LATSSearch` — UCB1-based MCTS that decides, per branch
  point, which method to fire by Monte-Carlo evaluation of the
  full plan it produces.
- :class:`SearchStatistics` — frozen audit-log dataclass capturing
  iterations, expansions, the final plan's total reward, and the
  per-task best-method tally.

The default reward function is ``1.0 / (1.0 + plan.total_cost)``;
callers wire :class:`brain_engine.htn.ValueEstimator` (the
Protocol shipped in M12 ``tree.py``) for retrieval-augmented
scoring (R-WoM in v1.0).

References:
    Zhou et al. (2023) — *Language Agent Tree Search*.
    arXiv:2310.04406.
    Auer / Cesa-Bianchi / Fischer (2002) — *Finite-time Analysis
    of the Multiarmed Bandit Problem*.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

from brain_engine.htn.models import (
    HTNPlan,
    Method,
    Operator,
    Task,
    TaskNetwork,
)
from brain_engine.htn.planner import HTNPlanFailure


__all__ = [
    "DEFAULT_EXPLORATION",
    "DEFAULT_ITERATIONS",
    "DEFAULT_MAX_DEPTH",
    "LATSSearch",
    "SearchStatistics",
    "default_reward",
]


DEFAULT_ITERATIONS: Final[int] = 64
DEFAULT_EXPLORATION: Final[float] = 1.4
DEFAULT_MAX_DEPTH: Final[int] = 16


RewardFn = Callable[[HTNPlan], float]


def default_reward(plan: HTNPlan) -> float:
    """Reward = ``1.0 / (1.0 + total_cost)``.

    Bounded in ``(0.0, 1.0]``; favours cheap plans without
    penalising long plans absolutely.
    """
    return 1.0 / (1.0 + plan.total_cost)


@dataclass(frozen=True, slots=True)
class SearchStatistics:
    """Audit-log summary of one :meth:`LATSSearch.search` run.

    Attributes:
        iterations_run: Number of MCTS rollouts performed.
        plan_total_cost: Sum of operator costs in the returned
            plan.
        plan_reward: Reward the search assigned to the returned
            plan.
        method_tally: Per-task method-name → visit count taken
            from the root-level statistics.  Useful for the audit
            log to explain *which* method dominated under the
            tested workload.
        failures: Number of rollouts that raised
            :class:`HTNPlanFailure` and contributed reward ``0``.
    """

    iterations_run: int
    plan_total_cost: float
    plan_reward: float
    method_tally: Mapping[str, Mapping[str, int]] = field(
        default_factory=dict,
    )
    failures: int = 0


@dataclass(slots=True)
class _MethodStats:
    """Per-task per-method UCB1 statistics."""

    visits: int = 0
    value_sum: float = 0.0

    def average(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits

    def ucb_score(
        self,
        *,
        parent_visits: int,
        c: float,
    ) -> float:
        if self.visits == 0:
            return math.inf
        avg = self.average()
        explore = c * math.sqrt(
            math.log(max(1, parent_visits)) / self.visits
        )
        return avg + explore


class LATSSearch:
    """UCB1-based MCTS over HTN method choices.

    The search is configured with a :class:`TaskNetwork` and an
    optional reward function.  ``search(task, iterations)`` runs
    ``iterations`` rollouts, each picking methods via UCB1 from
    accumulated stats; the returned plan is the one assembled by
    re-rolling the network with each task's *highest-average-
    reward* method.
    """

    def __init__(
        self,
        *,
        network: TaskNetwork,
        reward_fn: RewardFn = default_reward,
        exploration: float = DEFAULT_EXPLORATION,
        max_depth: int = DEFAULT_MAX_DEPTH,
        rng: random.Random | None = None,
    ) -> None:
        if exploration < 0.0:
            raise ValueError("exploration must be non-negative")
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        self._network = network
        self._reward = reward_fn
        self._c = exploration
        self._max_depth = max_depth
        self._rng = rng or random.Random()
        self._log = structlog.get_logger(__name__).bind(
            component="lats_search",
        )

    def search(
        self,
        *,
        task: Task,
        iterations: int = DEFAULT_ITERATIONS,
        state: Mapping[str, Any] | None = None,
    ) -> tuple[HTNPlan, SearchStatistics]:
        """Run ``iterations`` rollouts; return the best plan + stats.

        Returns:
            Tuple ``(plan, stats)``.  ``plan`` is empty when every
            rollout failed; ``stats.iterations_run`` always equals
            ``iterations`` so the audit log records the workload.
        """
        if iterations < 1:
            raise ValueError("iterations must be >= 1")
        snapshot_state = dict(state or {})
        stats: dict[str, dict[str, _MethodStats]] = {}
        failures = 0
        for _ in range(iterations):
            sample_plan = self._rollout(
                task=task,
                state=snapshot_state,
                stats=stats,
                ucb=True,
            )
            if sample_plan is None:
                failures += 1
                continue
            reward = self._reward(sample_plan)
            self._record_rollout(
                stats=stats, plan=sample_plan, reward=reward,
            )
        best_plan = self._best_plan(
            task=task,
            state=snapshot_state,
            stats=stats,
        )
        if best_plan is None:
            return HTNPlan(operators=()), SearchStatistics(
                iterations_run=iterations,
                plan_total_cost=0.0,
                plan_reward=0.0,
                failures=failures,
            )
        plan_reward = self._reward(best_plan)
        method_tally = {
            task_name: {
                method_name: s.visits
                for method_name, s in by_method.items()
            }
            for task_name, by_method in stats.items()
        }
        return best_plan, SearchStatistics(
            iterations_run=iterations,
            plan_total_cost=best_plan.total_cost,
            plan_reward=plan_reward,
            method_tally=method_tally,
            failures=failures,
        )

    # ── internals ─────────────────────────────────────────── #

    def _rollout(
        self,
        *,
        task: Task,
        state: Mapping[str, Any],
        stats: dict[str, dict[str, _MethodStats]],
        ucb: bool,
    ) -> HTNPlan | None:
        """Walk one decomposition; pick methods via UCB or argmax."""
        operators: list[Operator] = []
        chosen: list[str] = []
        try:
            self._descend(
                task_name=task.name,
                state=state,
                operators=operators,
                methods_used=chosen,
                stats=stats,
                ucb=ucb,
                depth=0,
                visited=set(),
            )
        except HTNPlanFailure:
            return None
        return HTNPlan(
            operators=tuple(operators),
            chosen_methods=tuple(chosen),
        )

    def _descend(
        self,
        *,
        task_name: str,
        state: Mapping[str, Any],
        operators: list[Operator],
        methods_used: list[str],
        stats: dict[str, dict[str, _MethodStats]],
        ucb: bool,
        depth: int,
        visited: set[str],
    ) -> None:
        if depth > self._max_depth:
            raise HTNPlanFailure(
                task_chain=tuple(visited),
                reason=f"max_depth={self._max_depth} exceeded",
            )
        operator = self._network.operators.get(task_name)
        if operator is not None and operator.applicable(state):
            operators.append(operator)
            return
        candidates = self._applicable_methods(
            task_name=task_name, state=state,
        )
        if not candidates:
            raise HTNPlanFailure(
                task_chain=tuple(visited) + (task_name,),
                reason=f"no applicable method for {task_name!r}",
            )
        method = self._pick_method(
            task_name=task_name,
            candidates=candidates,
            stats=stats,
            ucb=ucb,
        )
        methods_used.append(method.name)
        for sub_name in method.subtasks:
            self._descend(
                task_name=sub_name,
                state=state,
                operators=operators,
                methods_used=methods_used,
                stats=stats,
                ucb=ucb,
                depth=depth + 1,
                visited=visited | {task_name},
            )

    def _applicable_methods(
        self,
        *,
        task_name: str,
        state: Mapping[str, Any],
    ) -> list[Method]:
        candidates = self._network.methods.get(task_name) or ()
        return [m for m in candidates if m.applicable(state)]

    def _pick_method(
        self,
        *,
        task_name: str,
        candidates: list[Method],
        stats: dict[str, dict[str, _MethodStats]],
        ucb: bool,
    ) -> Method:
        if len(candidates) == 1:
            return candidates[0]
        by_method = stats.setdefault(task_name, {})
        for method in candidates:
            by_method.setdefault(method.name, _MethodStats())
        parent_visits = sum(
            by_method[m.name].visits for m in candidates
        )
        if not ucb:
            return self._argmax_average(
                candidates=candidates, by_method=by_method,
            )
        scores = [
            (
                by_method[m.name].ucb_score(
                    parent_visits=parent_visits, c=self._c,
                ),
                m,
            )
            for m in candidates
        ]
        scores.sort(key=lambda pair: pair[0], reverse=True)
        top_score = scores[0][0]
        top = [m for s, m in scores if s == top_score]
        return self._rng.choice(top)

    @staticmethod
    def _argmax_average(
        *,
        candidates: list[Method],
        by_method: dict[str, _MethodStats],
    ) -> Method:
        best = candidates[0]
        best_avg = by_method[best.name].average()
        for method in candidates[1:]:
            avg = by_method[method.name].average()
            if avg > best_avg:
                best = method
                best_avg = avg
        return best

    @staticmethod
    def _record_rollout(
        *,
        stats: dict[str, dict[str, _MethodStats]],
        plan: HTNPlan,
        reward: float,
    ) -> None:
        # ``chosen_methods`` is ordered by the order methods fired;
        # we credit each (task, method) pair with the same reward
        # so back-prop is uniform along the path.
        for method_name in plan.chosen_methods:
            for by_method in stats.values():
                method_stat = by_method.get(method_name)
                if method_stat is None:
                    continue
                method_stat.visits += 1
                method_stat.value_sum += reward

    def _best_plan(
        self,
        *,
        task: Task,
        state: Mapping[str, Any],
        stats: dict[str, dict[str, _MethodStats]],
    ) -> HTNPlan | None:
        return self._rollout(
            task=task, state=state, stats=stats, ucb=False,
        )
