"""Value objects + leaf behaviours for the BT composition layer.

The public types are kept minimal on purpose — every leaf the
caller writes plugs in via :class:`ConditionBehaviour` or
:class:`ActionBehaviour`, both of which receive a free-form
:class:`TreeContext` (caller-shaped) and emit either a boolean
(condition) or a :class:`Status` (action).  This lets us keep
the BT runtime stateless across leaves and put all state in the
caller's context object — the same pattern the HTN / LATS
planner stack already uses elsewhere in the codebase.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

import py_trees
import structlog


__all__ = [
    "ActionBehaviour",
    "ConditionBehaviour",
    "Status",
    "TickRecord",
    "TreeContext",
]


Status = py_trees.common.Status


logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class TreeContext:
    """Free-form blackboard the leaves read + write.

    Behaviors must not depend on internal py_trees blackboards —
    every read / write goes through this typed object so the
    audit log can serialise it deterministically.

    Attributes:
        data: Caller-shaped dict.  Free-form keys; the BT
            runtime does not introspect.
        metadata: Reserved namespace for the runner — populated
            during ticking; leaves should not mutate it.
    """

    data: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def get(self, key: str, default: object = None) -> object:
        """Read a key from ``data`` with a default."""
        return self.data.get(key, default)

    def set(self, key: str, value: object) -> None:
        """Write a key into ``data``."""
        self.data[key] = value


@dataclass(frozen=True, slots=True)
class TickRecord:
    """One entry in the audit log for a tick."""

    leaf_name: str
    status: Status
    rationale: str
    ticked_at: datetime

    def __post_init__(self) -> None:
        if self.ticked_at.tzinfo is None:
            raise ValueError("ticked_at must be tz-aware")
        if not self.leaf_name:
            raise ValueError("leaf_name required")


class _BrainEngineLeaf(py_trees.behaviour.Behaviour):
    """Common base — captures the typed context + audit log."""

    def __init__(
        self,
        *,
        name: str,
        context: TreeContext,
    ) -> None:
        if not name:
            raise ValueError("name required")
        super().__init__(name=name)
        self._context = context
        self._log = logger.bind(component="bt_leaf", leaf=name)
        self._last_rationale = ""

    def _emit_record(self, status: Status) -> None:
        record = TickRecord(
            leaf_name=self.name,
            status=status,
            rationale=self._last_rationale,
            ticked_at=datetime.now(timezone.utc),
        )
        log = self._context.metadata.setdefault(
            "audit", []
        )
        if isinstance(log, list):
            log.append(record)


class ConditionBehaviour(_BrainEngineLeaf):
    """BT leaf that maps ``(context) -> bool`` to ``SUCCESS`` / ``FAILURE``.

    The wrapped predicate may raise.  Exceptions are translated
    to ``FAILURE`` with the exception message stored in the
    rationale — the BT keeps ticking; observability is the
    audit-log entry that records the failure mode.
    """

    def __init__(
        self,
        *,
        name: str,
        context: TreeContext,
        predicate: Callable[[TreeContext], bool],
        rationale_true: str = "",
        rationale_false: str = "",
    ) -> None:
        super().__init__(name=name, context=context)
        self._predicate = predicate
        self._rationale_true = rationale_true or (
            f"{name}: condition met"
        )
        self._rationale_false = rationale_false or (
            f"{name}: condition not met"
        )

    def update(self) -> Status:
        """Run the predicate and translate the bool to a status."""
        try:
            outcome = bool(self._predicate(self._context))
        except Exception as exc:  # noqa: BLE001 — translated.
            self._last_rationale = (
                f"{self.name}: exception {type(exc).__name__}: "
                f"{exc}"
            )
            self._log.warning(
                "leaf.exception",
                error=str(exc),
            )
            self._emit_record(Status.FAILURE)
            return Status.FAILURE
        if outcome:
            self._last_rationale = self._rationale_true
            self._emit_record(Status.SUCCESS)
            return Status.SUCCESS
        self._last_rationale = self._rationale_false
        self._emit_record(Status.FAILURE)
        return Status.FAILURE


class ActionBehaviour(_BrainEngineLeaf):
    """BT leaf that maps ``(context) -> Status`` for side effects.

    The wrapped callable may return :class:`Status`,
    :class:`bool`, or ``None``.  Booleans map ``True →
    SUCCESS`` / ``False → FAILURE``; ``None`` maps to
    ``SUCCESS``.  Exceptions translate to ``FAILURE`` exactly
    like :class:`ConditionBehaviour`.
    """

    def __init__(
        self,
        *,
        name: str,
        context: TreeContext,
        action: Callable[
            [TreeContext], Status | bool | None
        ],
        rationale: str = "",
    ) -> None:
        super().__init__(name=name, context=context)
        self._action = action
        self._rationale = rationale or f"{name}: executed"

    def update(self) -> Status:
        """Run the side-effecting action and translate the result."""
        try:
            outcome = self._action(self._context)
            status = self._translate(outcome)
        except Exception as exc:  # noqa: BLE001 — translated.
            self._last_rationale = (
                f"{self.name}: exception {type(exc).__name__}: "
                f"{exc}"
            )
            self._log.warning(
                "leaf.exception",
                error=str(exc),
            )
            self._emit_record(Status.FAILURE)
            return Status.FAILURE
        self._last_rationale = self._rationale
        self._emit_record(status)
        return status

    @staticmethod
    def _translate(
        outcome: Status | bool | None,
    ) -> Status:
        """Translate caller's return into a :class:`Status`."""
        if isinstance(outcome, Status):
            return outcome
        if outcome is None:
            return Status.SUCCESS
        if isinstance(outcome, bool):
            return Status.SUCCESS if outcome else Status.FAILURE
        # Anything else (truthy / falsy non-bool) — be strict.
        raise TypeError(
            "action must return Status / bool / None; got "
            f"{type(outcome).__name__}"
        )


_ = Mapping
