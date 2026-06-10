"""HTN planner: recursive decomposition over a :class:`TaskNetwork`.

The planner walks a target :class:`Task`:

1. If the task name maps to an :class:`Operator` whose
   precondition holds → emit the operator.
2. Else look up the methods for the task name; pick the first
   whose precondition holds; recurse on every subtask.
3. If neither resolves → raise :class:`HTNPlanFailure` carrying
   the task chain that failed.

The planner is *deterministic* in v0.1 — methods are tried in
declaration order, no LATS / R-WoM scoring yet.  The
:class:`brain_engine.htn.tree.PlanTreeNode` data model in
``tree.py`` is the seam where v1.0 plugs LATS-style search.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from brain_engine.htn.models import (
    HTNPlan,
    Operator,
    Task,
    TaskNetwork,
)


__all__ = ["HTNPlanFailure", "HTNPlanner"]


logger = structlog.get_logger(__name__)


class HTNPlanFailure(RuntimeError):
    """Raised when no decomposition resolves the requested task."""

    def __init__(
        self,
        *,
        task_chain: tuple[str, ...],
        reason: str,
    ) -> None:
        super().__init__(
            f"HTN plan failed at {' → '.join(task_chain)}: "
            f"{reason}"
        )
        self.task_chain = task_chain
        self.reason = reason


class HTNPlanner:
    """Decompose a :class:`Task` into an :class:`HTNPlan`."""

    def __init__(
        self,
        *,
        network: TaskNetwork,
        max_depth: int = 16,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        self._network = network
        self._max_depth = max_depth
        self._log = logger.bind(component="htn_planner")

    def plan(
        self,
        *,
        task: Task,
        state: Mapping[str, Any] | None = None,
    ) -> HTNPlan:
        """Return the :class:`HTNPlan` for ``task`` under ``state``."""
        operators: list[Operator] = []
        chosen_methods: list[str] = []
        self._decompose(
            task_name=task.name,
            state=dict(state or {}),
            chain=(task.name,),
            depth=0,
            out=operators,
            methods_used=chosen_methods,
        )
        return HTNPlan(
            operators=tuple(operators),
            chosen_methods=tuple(chosen_methods),
        )

    # ── internals ─────────────────────────────────────────────── #

    def _decompose(
        self,
        *,
        task_name: str,
        state: dict[str, Any],
        chain: tuple[str, ...],
        depth: int,
        out: list[Operator],
        methods_used: list[str],
    ) -> None:
        if depth > self._max_depth:
            raise HTNPlanFailure(
                task_chain=chain,
                reason=f"max_depth={self._max_depth} exceeded",
            )
        operator = self._operator_or_none(
            task_name=task_name, state=state,
        )
        if operator is not None:
            out.append(operator)
            return
        method = self._method_or_none(
            task_name=task_name, state=state,
        )
        if method is None:
            raise HTNPlanFailure(
                task_chain=chain,
                reason=(
                    "no applicable operator or method for "
                    f"task {task_name!r}"
                ),
            )
        methods_used.append(method.name)
        for sub_name in method.subtasks:
            self._decompose(
                task_name=sub_name,
                state=state,
                chain=chain + (sub_name,),
                depth=depth + 1,
                out=out,
                methods_used=methods_used,
            )

    def _operator_or_none(
        self,
        *,
        task_name: str,
        state: Mapping[str, Any],
    ) -> Operator | None:
        operator = self._network.operators.get(task_name)
        if operator is None:
            return None
        if not operator.applicable(state):
            return None
        return operator

    def _method_or_none(
        self,
        *,
        task_name: str,
        state: Mapping[str, Any],
    ):
        candidates = self._network.methods.get(task_name)
        if not candidates:
            return None
        for method in candidates:
            if method.applicable(state):
                return method
        return None
