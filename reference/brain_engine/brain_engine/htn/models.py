"""HTN value objects (Moat #12).

Three building blocks compose a Hierarchical Task Network:

- :class:`Operator` — primitive action, directly executable.  Pins
  a :class:`PreferredSolver` (LLM / Utility / SMT / Deterministic /
  HITL) per GR00T P2: each sub-action delegates to its preferred
  solver instead of the runtime forcing one method on every move.
- :class:`Method` — compound decomposition rule.  Maps a *task
  pattern* to an ordered list of subtask names, gated by a free-
  form precondition predicate.
- :class:`Task` — abstract goal label.

The HTN itself is just a lookup table from task name to a tuple of
:class:`Method` records and a tuple of :class:`Operator` records.
The planner in :mod:`brain_engine.htn.planner` walks that table
recursively to produce an :class:`HTNPlan`.

Erol/Hendler/Nau (1994) and SHOP2 (Nau et al., JAIR 2003) are the
canonical references; this module ships the same conceptual
shape but with frozen dataclasses + StrEnum for the modern Python
type story.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Final


__all__ = [
    "DEFAULT_PRECONDITION",
    "HTNPlan",
    "Method",
    "Operator",
    "PreferredSolver",
    "Task",
    "TaskNetwork",
]


class PreferredSolver(StrEnum):
    """Per-action solver tag (GR00T P2 decoupled WBC mapping).

    The runtime middleware reads this tag and routes the operator
    through the matching handler.  An operator with
    :attr:`HITL` cannot run autonomously — the planner emits it
    only when the path requires explicit human approval.
    """

    LLM = "llm"
    UTILITY = "utility"
    SMT = "smt"
    DETERMINISTIC = "deterministic"
    HITL = "hitl"


Precondition = Callable[[Mapping[str, Any]], bool]
"""State → ``bool`` predicate; defaults always-true."""


def _always_true(_: Mapping[str, Any]) -> bool:
    return True


DEFAULT_PRECONDITION: Final[Precondition] = _always_true


@dataclass(frozen=True, slots=True)
class Task:
    """Abstract goal label.

    Attributes:
        name: Stable string identifier (e.g.
            ``"resolve_noise_complaint"`` /
            ``"close_late_checkout"``).
        params: Free-form parameter map the methods / operators
            consume.  Keys are caller-defined strings; values are
            JSON-safe primitives.
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name required")


@dataclass(frozen=True, slots=True)
class Operator:
    """Primitive (directly executable) action.

    Attributes:
        name: Stable identifier.
        preferred_solver: Solver tag (GR00T P2 routing).
        cost: Non-negative numeric cost the planner can sum to
            compare alternative decompositions.
        precondition: State predicate.  When ``False`` the planner
            skips the operator and tries the next candidate.
    """

    name: str
    preferred_solver: PreferredSolver
    cost: float = 1.0
    precondition: Precondition = DEFAULT_PRECONDITION

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name required")
        if self.cost < 0.0:
            raise ValueError("cost must be non-negative")

    def applicable(self, state: Mapping[str, Any]) -> bool:
        """Return ``True`` when the operator's preconditions hold."""
        return self.precondition(state)


@dataclass(frozen=True, slots=True)
class Method:
    """Compound decomposition rule.

    Attributes:
        name: Stable identifier.
        task: Name of the task this method decomposes.
        subtasks: Ordered tuple of subtask names produced when
            the method fires.
        precondition: State predicate gating the method.
    """

    name: str
    task: str
    subtasks: Sequence[str]
    precondition: Precondition = DEFAULT_PRECONDITION

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name required")
        if not self.task:
            raise ValueError("task required")
        if not self.subtasks:
            raise ValueError("subtasks must be non-empty")

    def applicable(self, state: Mapping[str, Any]) -> bool:
        """Return ``True`` when the method's preconditions hold."""
        return self.precondition(state)


@dataclass(frozen=True, slots=True)
class TaskNetwork:
    """Lookup table the planner walks.

    Attributes:
        operators: name → :class:`Operator`.
        methods: task_name → tuple of :class:`Method` candidates,
            in declaration order (the planner tries them top-to-
            bottom and picks the first whose precondition holds).
    """

    operators: Mapping[str, Operator]
    methods: Mapping[str, Sequence[Method]]


@dataclass(frozen=True, slots=True)
class HTNPlan:
    """Result of a successful decomposition.

    Attributes:
        operators: Ordered tuple of grounded operators the planner
            chose; calling :func:`sum` over their ``cost`` gives
            the total plan cost.
        chosen_methods: Tuple of method names the planner used,
            in the order it fired them.  Records *why* the plan
            looks the way it does for the audit log.
    """

    operators: tuple[Operator, ...]
    chosen_methods: tuple[str, ...] = ()

    @property
    def total_cost(self) -> float:
        """Sum of operator costs."""
        return sum(op.cost for op in self.operators)

    @property
    def solver_mix(self) -> tuple[PreferredSolver, ...]:
        """Solvers the plan touches, in order."""
        return tuple(op.preferred_solver for op in self.operators)
